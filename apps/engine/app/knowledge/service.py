from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database.models import Chunk, File, FileVersion
from app.database.session import Database
from app.knowledge.chunking import build_chunks
from app.knowledge.embedding import EMBEDDING_PROVIDER, embed_text
from app.knowledge.fts import replace_file_fts
from app.knowledge.vector_store import LanceVectorStore
from app.parsing.cache import ParsedDocumentCache

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KnowledgeSnapshot:
    running: bool
    processed: int
    failed: int
    last_file_id: str | None
    last_completed_at: str | None
    last_error: str | None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "processed": self.processed,
            "failed": self.failed,
            "last_file_id": self.last_file_id,
            "last_completed_at": self.last_completed_at,
            "last_error": self.last_error,
            "embedding_provider": EMBEDDING_PROVIDER,
        }


class KnowledgeIndexer:
    """Turns Phase 3 parsed documents into Phase 4 retrieval indexes."""

    def __init__(
        self,
        database: Database,
        data_dir: Path,
        vector_path: Path,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        self.database = database
        self.cache = ParsedDocumentCache(data_dir)
        self.vector_store = LanceVectorStore(vector_path)
        self.interval_seconds = max(interval_seconds, 0.25)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._processed = 0
        self._failed = 0
        self._last_file_id: str | None = None
        self._last_completed_at: str | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="deskai-knowledge-indexer",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def wake(self) -> None:
        self._wake.set()

    def snapshot(self) -> KnowledgeSnapshot:
        thread = self._thread
        with self._lock:
            return KnowledgeSnapshot(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                processed=self._processed,
                failed=self._failed,
                last_file_id=self._last_file_id,
                last_completed_at=self._last_completed_at,
                last_error=self._last_error,
            )

    def process_available(self, limit: int = 20) -> int:
        count = 0
        for _ in range(max(limit, 0)):
            if not self.process_next():
                break
            count += 1
        return count

    def process_next(self) -> bool:
        with self._process_lock:
            claim = self._claim_next()
            if claim is None:
                return False

            file_id, version_id, workspace_id, filename, sha256 = claim
            try:
                parsed = self.cache.read(sha256)
                if parsed is None:
                    raise FileNotFoundError(f"Parsed cache is missing for {filename}")

                drafts = build_chunks(parsed)
                chunk_rows: list[Chunk] = []
                vector_rows: list[dict] = []
                for index, draft in enumerate(drafts):
                    chunk_id = str(uuid.uuid4())
                    row = Chunk(
                        id=chunk_id,
                        workspace_id=workspace_id,
                        file_id=file_id,
                        file_version_id=version_id,
                        chunk_index=index,
                        page_start=draft.page_start,
                        page_end=draft.page_end,
                        sheet_name=draft.sheet_name,
                        cell_range=draft.cell_range,
                        slide_start=draft.slide_start,
                        slide_end=draft.slide_end,
                        section_title=draft.section_title,
                        content=draft.content,
                        content_hash=draft.content_hash,
                        language=draft.language,
                        token_count=draft.token_count,
                        active=True,
                    )
                    chunk_rows.append(row)
                    vector_rows.append(
                        {
                            "chunk_id": chunk_id,
                            "workspace_id": workspace_id,
                            "file_id": file_id,
                            "file_version_id": version_id,
                            "filename": filename,
                            "content": draft.content,
                            "embedding_provider": EMBEDDING_PROVIDER,
                            "vector": embed_text(draft.content),
                        }
                    )

                # LanceDB is replaced first. If the following SQLite transaction
                # fails, the vector rows are harmless because search cross-checks
                # each hit against the authoritative SQLite current-version state.
                self.vector_store.replace_file(file_id, vector_rows)
                self._commit_index(
                    file_id=file_id,
                    version_id=version_id,
                    sha256=sha256,
                    filename=filename,
                    chunks=chunk_rows,
                )
                self._record_success(file_id)
            except Exception as exc:
                self._mark_failed(file_id, version_id, exc)
                self._record_failure(file_id, exc)
            return True

    def retry_file(self, file_id: str) -> bool:
        with self.database.session() as session:
            file = session.get(File, file_id)
            if file is None or file.current_version_id is None:
                return False
            version = session.get(FileVersion, file.current_version_id)
            if version is None:
                return False
            if file.status not in {"index_failed", "parsed"}:
                return False
            version.indexed_at = None
            file.status = "parsed"
        self.wake()
        return True

    def _claim_next(self) -> tuple[str, str, str, str, str] | None:
        with self.database.session() as session:
            row = session.execute(
                select(File, FileVersion)
                .join(FileVersion, FileVersion.id == File.current_version_id)
                .where(
                    File.status == "parsed",
                    FileVersion.active.is_(True),
                    FileVersion.indexed_at.is_(None),
                    File.sha256 == FileVersion.sha256,
                )
                .order_by(File.updated_at, File.id)
                .limit(1)
            ).first()
            if row is None:
                return None
            file, version = row
            return file.id, version.id, file.workspace_id, file.filename, version.sha256

    def _commit_index(
        self,
        *,
        file_id: str,
        version_id: str,
        sha256: str,
        filename: str,
        chunks: list[Chunk],
    ) -> None:
        with self.database.session() as session:
            file = session.get(File, file_id)
            version = session.get(FileVersion, version_id)
            if (
                file is None
                or version is None
                or file.current_version_id != version_id
                or file.sha256 != sha256
                or file.status != "parsed"
            ):
                return

            old_chunks = list(
                session.scalars(
                    select(Chunk).where(Chunk.file_id == file_id, Chunk.active.is_(True))
                ).all()
            )
            old_chunk_ids = [chunk.id for chunk in old_chunks]
            for chunk in old_chunks:
                chunk.active = False

            session.add_all(chunks)
            session.flush()
            replace_file_fts(
                session,
                old_chunk_ids=old_chunk_ids,
                workspace_id=file.workspace_id,
                filename=filename,
                chunks=chunks,
            )
            version.indexed_at = datetime.now(timezone.utc)
            file.status = "indexed"

    def _mark_failed(self, file_id: str, version_id: str, exc: Exception) -> None:
        with self.database.session() as session:
            file = session.get(File, file_id)
            version = session.get(FileVersion, version_id)
            if file is not None and file.current_version_id == version_id and version is not None:
                file.status = "index_failed"

    def _record_success(self, file_id: str) -> None:
        with self._lock:
            self._processed += 1
            self._last_file_id = file_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None

    def _record_failure(self, file_id: str, exc: Exception) -> None:
        logger.error(
            "Knowledge indexing failed for %s: %s",
            file_id,
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        with self._lock:
            self._failed += 1
            self._last_file_id = file_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _run(self) -> None:
        while not self._stop.is_set():
            processed = self.process_available(limit=8)
            if processed == 0:
                self._wake.wait(self.interval_seconds)
                self._wake.clear()
