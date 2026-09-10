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

# V1 acceptance formats. Document contents are not parsed until Phase 3.
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
    ".jpg",
    ".png",
}
EXCLUDED_DIR_NAMES = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    ".cache",
    "AppData",
    "Temp",
}
EXCLUDED_EXTENSIONS = {".exe", ".dll", ".sys"}
MAX_AUTO_INDEX_SIZE = 500 * 1024 * 1024
ACTIVE_JOB_STATUSES = {"queued", "processing"}


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

    def merge(self, other: "ScanStats") -> None:
        self.discovered += other.discovered
        self.queued += other.queued
        self.unchanged += other.unchanged
        self.unsupported += other.unsupported
        self.skipped += other.skipped
        self.deleted += other.deleted


def _active_jobs(session: Session, file_id: str) -> list[IndexJob]:
    return list(
        session.scalars(
            select(IndexJob).where(
                IndexJob.file_id == file_id,
                IndexJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        ).all()
    )


def _queue_job(session: Session, workspace_id: str, file_id: str, priority: int = 100) -> None:
    jobs = _active_jobs(session, file_id)
    queued_job = next((job for job in jobs if job.status == "queued"), None)
    if queued_job is not None:
        queued_job.priority = min(queued_job.priority, priority)
        return

    # If a parser is already processing an older version, do not mutate that run.
    # Queue a fresh job for the latest File.sha256 instead.
    session.add(
        IndexJob(
            workspace_id=workspace_id,
            file_id=file_id,
            status="queued",
            priority=priority,
        )
    )


def _queue_pending_if_needed(session: Session, file: File, *, enabled: bool) -> bool:
    if not enabled or file.status != "pending" or _active_jobs(session, file.id):
        return False
    _queue_job(session, file.workspace_id, file.id)
    return True


def _cancel_active_jobs(session: Session, file_id: str, reason: str) -> None:
    for job in _active_jobs(session, file_id):
        job.status = "cancelled"
        job.error_code = "FILE_NOT_INDEXABLE"
        job.error_message = reason


def _same_metadata(record: File, size: int, modified_at: datetime) -> bool:
    if record.modified_at is None:
        return False
    return record.size == size and abs(record.modified_at.timestamp() - modified_at.timestamp()) < 0.001


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _mark_missing_files_deleted(session: Session, workspace_id: str, root: Path, seen: set[str]) -> int:
    deleted = 0
    records = session.scalars(
        select(File).where(File.workspace_id == workspace_id, File.status != "deleted")
    ).all()
    for record in records:
        try:
            record_path = Path(record.path).resolve(strict=False)
        except OSError:
            continue
        if not _is_within(record_path, root):
            continue
        if str(record_path) not in seen and not record_path.exists():
            record.status = "deleted"
            _cancel_active_jobs(session, record.id, "File was removed from the authorized root")
            deleted += 1
    return deleted


def reconcile_workspace_access(session: Session, workspace_id: str) -> int:
    """Mark files outside every readable authorized root as revoked.

    This only changes DeskAI metadata; it never deletes or modifies a local file.
    """
    roots = session.scalars(
        select(WorkspaceRoot).where(
            WorkspaceRoot.workspace_id == workspace_id,
            WorkspaceRoot.read_allowed.is_(True),
        )
    ).all()
    normalized_roots: list[Path] = []
    for root in roots:
        try:
            normalized_roots.append(normalize_root(root.path))
        except (OSError, ValueError):
            continue

    revoked = 0
    records = session.scalars(select(File).where(File.workspace_id == workspace_id)).all()
    for record in records:
        try:
            path = Path(record.path).resolve(strict=False)
        except OSError:
            continue
        if any(_is_within(path, root) for root in normalized_roots):
            continue
        if record.status != "revoked":
            record.status = "revoked"
            _cancel_active_jobs(session, record.id, "File authorization was revoked")
            revoked += 1
    return revoked


def scan_root(
    session: Session,
    workspace_root: WorkspaceRoot,
    *,
    queue_changes: bool = True,
    metadata_shortcut: bool = False,
) -> ScanStats:
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
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            existing = session.scalar(
                select(File).where(
                    File.workspace_id == workspace_root.workspace_id,
                    File.path == canonical,
                )
            )

            unsupported = (
                extension in EXCLUDED_EXTENSIONS
                or extension not in SUPPORTED_EXTENSIONS
                or stat.st_size > MAX_AUTO_INDEX_SIZE
            )
            if unsupported:
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
                            modified_at=modified_at,
                            status="unsupported",
                        )
                    )
                else:
                    existing.filename = path.name
                    existing.extension = extension
                    existing.mime_type = mimetypes.guess_type(path.name)[0]
                    existing.size = stat.st_size
                    existing.modified_at = modified_at
                    existing.sha256 = None
                    existing.status = "unsupported"
                    _cancel_active_jobs(session, existing.id, "File type or size is unsupported")
                continue

            if (
                metadata_shortcut
                and existing is not None
                and existing.status not in {"deleted", "revoked", "unsupported"}
                and existing.sha256
                and _same_metadata(existing, stat.st_size, modified_at)
            ):
                if _queue_pending_if_needed(session, existing, enabled=queue_changes):
                    stats.queued += 1
                else:
                    stats.unchanged += 1
                continue

            try:
                digest = sha256_file(path)
            except OSError:
                stats.skipped += 1
                continue

            if existing is not None and existing.sha256 == digest and existing.status not in {
                "deleted",
                "revoked",
            }:
                existing.size = stat.st_size
                existing.modified_at = modified_at
                if existing.status == "failed" and queue_changes and not metadata_shortcut:
                    existing.status = "pending"
                    _queue_job(session, existing.workspace_id, existing.id)
                    stats.queued += 1
                elif _queue_pending_if_needed(session, existing, enabled=queue_changes):
                    stats.queued += 1
                else:
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

            if queue_changes:
                _queue_job(session, workspace_root.workspace_id, existing.id)
                stats.queued += 1

    stats.deleted = _mark_missing_files_deleted(session, workspace_root.workspace_id, root, seen)
    return stats
