from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import File, IndexJob, WorkspaceRoot
from app.indexing.hashing import sha256_file
from app.security.paths import normalize_root, require_within

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv",
    ".jpg", ".jpeg", ".png", ".webp", ".bmp",
}
EXCLUDED_DIR_NAMES = {
    ".git", "node_modules", "venv", ".venv", "dist", "build", ".cache", "AppData", "Temp",
}
EXCLUDED_EXTENSIONS = {".exe", ".dll", ".sys"}
MAX_AUTO_INDEX_SIZE = 500 * 1024 * 1024


@dataclass(slots=True)
class ScanStats:
    discovered: int = 0
    queued: int = 0
    unchanged: int = 0
    unsupported: int = 0
    skipped: int = 0
    deleted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "queued": self.queued,
            "unchanged": self.unchanged,
            "unsupported": self.unsupported,
            "skipped": self.skipped,
            "deleted": self.deleted,
        }


def _queue_job(session: Session, workspace_id: str, file_id: str, priority: int = 100) -> None:
    active_job = session.scalar(
        select(IndexJob).where(
            IndexJob.file_id == file_id,
            IndexJob.status.in_(["queued", "processing"]),
        )
    )
    if active_job is None:
        session.add(IndexJob(workspace_id=workspace_id, file_id=file_id, status="queued", priority=priority))


def _mark_missing_files_deleted(session: Session, workspace_id: str, root: Path, seen: set[str]) -> int:
    deleted = 0
    records = session.scalars(
        select(File).where(File.workspace_id == workspace_id, File.status != "deleted")
    ).all()
    for record in records:
        try:
            record_path = Path(record.path).resolve(strict=False)
            record_path.relative_to(root)
        except ValueError:
            continue
        if str(record_path) not in seen and not record_path.exists():
            record.status = "deleted"
            deleted += 1
    return deleted


def scan_root(session: Session, workspace_root: WorkspaceRoot) -> ScanStats:
    root = normalize_root(workspace_root.path)
    stats = ScanStats()
    seen: set[str] = set()

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIR_NAMES]
        current_path = Path(current)

        for filename in filenames:
            raw_path = current_path / filename
            stats.discovered += 1

            if raw_path.is_symlink():
                stats.skipped += 1
                continue

            try:
                path = require_within(raw_path, root)
                stat = path.stat()
            except (OSError, ValueError):
                stats.skipped += 1
                continue

            canonical = str(path)
            seen.add(canonical)
            extension = path.suffix.lower()
            existing = session.scalar(
                select(File).where(File.workspace_id == workspace_root.workspace_id, File.path == canonical)
            )

            if extension in EXCLUDED_EXTENSIONS or extension not in SUPPORTED_EXTENSIONS or stat.st_size > MAX_AUTO_INDEX_SIZE:
                stats.unsupported += 1
                if existing is None:
                    session.add(
                        File(
                            workspace_id=workspace_root.workspace_id,
                            path=canonical,
                            filename=path.name,
                            extension=extension,
                            mime_type=mimetypes.guess_type(path.name)[0],
                            size=stat.st_size,
                            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                            status="unsupported",
                        )
                    )
                else:
                    existing.status = "unsupported"
                continue

            digest = sha256_file(path)
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if existing is not None and existing.sha256 == digest and existing.status != "deleted":
                existing.size = stat.st_size
                existing.modified_at = modified_at
                stats.unchanged += 1
                continue

            if existing is None:
                existing = File(
                    workspace_id=workspace_root.workspace_id,
                    path=canonical,
                    filename=path.name,
                    extension=extension,
                    mime_type=mimetypes.guess_type(path.name)[0],
                    size=stat.st_size,
                    sha256=digest,
                    modified_at=modified_at,
                    status="pending",
                )
                session.add(existing)
                session.flush()
            else:
                existing.filename = path.name
                existing.extension = extension
                existing.mime_type = mimetypes.guess_type(path.name)[0]
                existing.size = stat.st_size
                existing.sha256 = digest
                existing.modified_at = modified_at
                existing.status = "pending"
                session.flush()

            _queue_job(session, workspace_root.workspace_id, existing.id)
            stats.queued += 1

    stats.deleted = _mark_missing_files_deleted(session, workspace_root.workspace_id, root, seen)
    return stats
