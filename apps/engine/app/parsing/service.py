from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database.models import File, FileVersion, IndexJob, WorkspaceRoot
from app.database.session import Database
from app.indexing.hashing import sha256_file
from app.parsing.cache import ParsedDocumentCache
from app.parsing.readers import PARSER_VERSION, ParseError, parse_document
from app.security.paths import is_within

logger = logging.getLogger(__name__)
BLOCKED_FILE_STATUSES = {"deleted", "revoked", "unsupported"}


@dataclass(slots=True)
class ParserWorkerSnapshot:
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
            "parser_version": PARSER_VERSION,
        }


class ParserWorker:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        *,
        interval_seconds: float = 0.75,
    ) -> None:
        self.database = database
        self.cache = ParsedDocumentCache(data_dir)
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
                name="deskai-parser-worker",
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

    def snapshot(self) -> ParserWorkerSnapshot:
        thread = self._thread
        with self._lock:
            return ParserWorkerSnapshot(
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
        # The desktop can request an immediate parse while the background worker
        # is active. Serialize claims so the same queued SQLite job is never parsed twice.
        with self._process_lock:
            claim = self._claim_next()
            if claim is None:
                return False

            job_id, file_id, path, expected_sha = claim
            try:
                parsed = parse_document(path)
                actual_sha = sha256_file(path)
                if actual_sha != expected_sha:
                    self._mark_stale(job_id, file_id)
                    return True

                payload = parsed.as_dict()
                payload.update(
                    {
                        "file_id": file_id,
                        "sha256": expected_sha,
                        "source_path": str(path),
                        "parsed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                cache_path = self.cache.write(expected_sha, payload)
                if self._complete(job_id, file_id, expected_sha, cache_path):
                    self._record_success(file_id)
            except Exception as exc:
                self._fail(job_id, file_id, exc)
                self._record_failure(file_id, exc)
            return True

    def _claim_next(self) -> tuple[str, str, Path, str] | None:
        with self.database.session() as session:
            while True:
                job = session.scalar(
                    select(IndexJob)
                    .where(IndexJob.status == "queued")
                    .order_by(IndexJob.priority, IndexJob.created_at)
                    .limit(1)
                )
                if job is None:
                    return None

                if job.file_id is None:
                    job.status = "cancelled"
                    job.error_code = "FILE_MISSING"
                    job.error_message = "Index job has no file reference"
                    session.flush()
                    continue

                file = session.get(File, job.file_id)
                if file is None:
                    job.status = "cancelled"
                    job.error_code = "FILE_MISSING"
                    job.error_message = "File record no longer exists"
                    session.flush()
                    continue

                if file.status in BLOCKED_FILE_STATUSES or not file.sha256:
                    job.status = "cancelled"
                    job.error_code = "FILE_NOT_PARSEABLE"
                    job.error_message = f"File status is {file.status}"
                    session.flush()
                    continue

                try:
                    path = Path(file.path).expanduser().resolve(strict=True)
                except OSError as exc:
                    job.status = "failed"
                    job.error_code = "FILE_UNAVAILABLE"
                    job.error_message = str(exc)
                    file.status = "failed"
                    session.flush()
                    continue

                roots = session.scalars(
                    select(WorkspaceRoot).where(
                        WorkspaceRoot.workspace_id == file.workspace_id,
                        WorkspaceRoot.read_allowed.is_(True),
                    )
                ).all()
                if not any(_safe_is_within(path, root.path) for root in roots):
                    job.status = "cancelled"
                    job.error_code = "AUTHORIZATION_REVOKED"
                    job.error_message = "File is outside readable workspace roots"
                    file.status = "revoked"
                    session.flush()
                    continue

                job.status = "processing"
                job.error_code = None
                job.error_message = None
                return job.id, file.id, path, file.sha256

    def _mark_stale(self, job_id: str, file_id: str) -> None:
        with self.database.session() as session:
            job = session.get(IndexJob, job_id)
            file = session.get(File, file_id)
            if job is not None:
                job.status = "cancelled"
                job.error_code = "FILE_CHANGED_DURING_PARSE"
                job.error_message = "File changed while it was being parsed"
            if file is not None and file.status not in BLOCKED_FILE_STATUSES:
                file.status = "pending"
                self._ensure_queued(session, file)

    def _complete(self, job_id: str, file_id: str, sha256: str, cache_path: Path) -> bool:
        with self.database.session() as session:
            job = session.get(IndexJob, job_id)
            file = session.get(File, file_id)
            if job is None or file is None:
                return False
            if file.sha256 != sha256 or file.status in BLOCKED_FILE_STATUSES:
                job.status = "cancelled"
                job.error_code = "STALE_PARSE_RESULT"
                job.error_message = "Parsed result no longer matches the current file"
                if file.status not in BLOCKED_FILE_STATUSES:
                    self._ensure_queued(session, file)
                return False

            for version in session.scalars(
                select(FileVersion).where(
                    FileVersion.file_id == file_id,
                    FileVersion.active.is_(True),
                )
            ).all():
                version.active = False

            version = session.scalar(
                select(FileVersion).where(
                    FileVersion.file_id == file_id,
                    FileVersion.sha256 == sha256,
                )
            )
            if version is None:
                version = FileVersion(
                    file_id=file.id,
                    sha256=sha256,
                    size=file.size,
                    modified_at=file.modified_at,
                    parser_version=PARSER_VERSION,
                    active=True,
                )
                session.add(version)
                session.flush()
            else:
                version.size = file.size
                version.modified_at = file.modified_at
                version.parser_version = PARSER_VERSION
                version.active = True

            file.current_version_id = version.id
            file.status = "parsed"
            job.status = "completed"
            job.error_code = None
            job.error_message = f"Parsed cache: {cache_path.name}"
            return True

    def _fail(self, job_id: str, file_id: str, exc: Exception) -> None:
        with self.database.session() as session:
            job = session.get(IndexJob, job_id)
            file = session.get(File, file_id)
            if job is not None:
                job.status = "failed"
                job.error_code = (
                    "PARSE_ERROR" if isinstance(exc, ParseError) else "PARSER_INTERNAL_ERROR"
                )
                job.error_message = str(exc)[:4000]
            if file is not None and file.status not in BLOCKED_FILE_STATUSES:
                file.status = "failed"

    def _ensure_queued(self, session, file: File) -> None:
        exists = session.scalar(
            select(IndexJob).where(
                IndexJob.file_id == file.id,
                IndexJob.status == "queued",
            )
        )
        if exists is None:
            session.add(
                IndexJob(
                    workspace_id=file.workspace_id,
                    file_id=file.id,
                    status="queued",
                    priority=100,
                )
            )

    def _record_success(self, file_id: str) -> None:
        with self._lock:
            self._processed += 1
            self._last_file_id = file_id
            self._last_completed_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None

    def _record_failure(self, file_id: str, exc: Exception) -> None:
        logger.error(
            "Parser worker failed for %s: %s",
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


def _safe_is_within(path: Path, root: str) -> bool:
    try:
        return is_within(path, root)
    except (OSError, ValueError):
        return False
