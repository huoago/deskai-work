from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.models import (
    AuditLog,
    File,
    FileOrganizationProposal,
    FileRecycleProposal,
    IndexJob,
    SourceFileEdit,
    Task,
    WorkspaceRoot,
)
from app.database.session import Database
from app.indexing.hashing import sha256_file
from app.indexing.scanner import scan_root
from app.security.paths import normalize_root, require_within
from app.source_edits.service import SourceFileEditService

ACTIVE_PROPOSAL_STATUSES = {"pending", "applying", "rolling_back", "recovery_required"}
ACTIVE_RECYCLE_STATUSES = {"pending", "recycling", "restoring", "recovery_required"}


class FileRecycleService:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        source_edit_service: SourceFileEditService,
    ) -> None:
        self.database = database
        self.data_dir = data_dir.resolve()
        self.source_edit_service = source_edit_service
        raw_recycle_root = self.data_dir / "recycle_bin"
        if raw_recycle_root.is_symlink():
            raise ValueError("DeskAI recycle root cannot be a symlink")
        raw_recycle_root.mkdir(parents=True, exist_ok=True)
        self.recycle_root = raw_recycle_root.resolve(strict=True)
        if not self.recycle_root.is_relative_to(self.data_dir):
            raise ValueError("DeskAI recycle root escaped the private data directory")

    def propose(
        self,
        *,
        task_id: str,
        workspace_id: str,
        file_id: str,
        summary: str,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        file, _root, source = self.source_edit_service._editable_file(
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=file_id,
        )
        self._ensure_not_processing(file_id)
        self._ensure_no_conflicting_proposals(file_id)

        current_sha = sha256_file(source)
        if not file.sha256 or current_sha != file.sha256:
            raise ValueError(
                "Source file changed outside DeskAI; rescan it before proposing recycle"
            )

        proposal_id = str(uuid.uuid4())
        quarantine = self._assert_quarantine_path(
            self.recycle_root / workspace_id / proposal_id / source.name
        )
        proposal = FileRecycleProposal(
            id=proposal_id,
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=file_id,
            batch_id=batch_id,
            status="pending",
            summary=str(summary or "").strip()[:1000]
            or f"Recycle {file.filename}",
            original_path=str(source),
            quarantine_path=str(quarantine),
            original_sha256=current_sha,
            original_size=file.size,
            previous_file_status=file.status,
        )
        with self.database.session() as session:
            session.add(proposal)
            session.flush()
            session.expunge(proposal)
        return self.payload(proposal, filename=file.filename)

    def confirm(self, proposal_id: str) -> dict[str, Any]:
        self._ensure_individual_action(proposal_id)
        snapshot = self._snapshot_for_recycle(proposal_id)
        source = snapshot["source"]
        quarantine = snapshot["quarantine"]

        self._ensure_not_processing(snapshot["file_id"])
        self._ensure_source_available(source)
        if sha256_file(source) != snapshot["original_sha256"]:
            raise ValueError(
                "File changed after the recycle proposal; confirmation is blocked"
            )

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None or proposal.status != "pending":
                raise ValueError("Recycle proposal is no longer pending")
            proposal.status = "recycling"
            proposal.confirmed_at = now
            proposal.error_message = None

        try:
            self._ensure_verified_quarantine_copy(
                source,
                quarantine,
                snapshot["original_sha256"],
            )
            if sha256_file(source) != snapshot["original_sha256"]:
                raise ValueError(
                    "Source file changed while the quarantine copy was being prepared"
                )
            source.unlink()
            if source.exists() or source.is_symlink():
                raise ValueError("Source path still exists after recycle operation")
            self._verify_quarantine(quarantine, snapshot["original_sha256"])
        except Exception as exc:
            if source.exists():
                self._reset_pending(
                    proposal_id,
                    (
                        "Recycle did not remove the source file; verified quarantine "
                        f"copy may be retained for retry: {type(exc).__name__}: {exc}"
                    ),
                )
            else:
                self._mark_recovery_required(
                    proposal_id,
                    (
                        "Source path changed during recycle and DeskAI could not safely "
                        f"finalize the operation: {type(exc).__name__}: {exc}"
                    ),
                )
            raise ValueError(f"Recycle operation failed: {exc}") from exc

        try:
            self._finalize_recycled(proposal_id, snapshot, now)
        except Exception as exc:
            self._mark_recovery_required(
                proposal_id,
                (
                    "File was safely copied to quarantine and removed from the Workspace, "
                    f"but database finalization failed: {type(exc).__name__}: {exc}"
                ),
            )
            raise

        return self.get(proposal_id)

    def reject(self, proposal_id: str) -> dict[str, Any]:
        self._ensure_individual_action(proposal_id)
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recycle proposal not found")
            if proposal.status != "pending":
                raise ValueError("Only pending recycle proposals can be rejected")
            proposal.status = "rejected"
            proposal.rejected_at = now
            proposal.error_message = None
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="file_recycle_rejected",
                    target=proposal.file_id,
                    result=f"proposal_id={proposal.id}",
                    risk_level=4,
                )
            )
        return self.get(proposal_id)

    def restore(self, proposal_id: str) -> dict[str, Any]:
        self._ensure_individual_action(proposal_id)
        snapshot = self._snapshot_for_restore(proposal_id)
        original = snapshot["original"]
        quarantine = snapshot["quarantine"]

        self._verify_quarantine(quarantine, snapshot["original_sha256"])
        if original.exists() or original.is_symlink():
            raise ValueError("Original path is occupied; restore is blocked")
        if not original.parent.is_dir():
            raise ValueError("Original parent directory no longer exists")
        self._probe_target_parent(original.parent)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None or proposal.status != "recycled":
                raise ValueError("Recycle proposal is no longer restorable")
            proposal.status = "restoring"
            proposal.error_message = None

        try:
            self._restore_copy_no_overwrite(
                quarantine,
                original,
                snapshot["original_sha256"],
            )
        except Exception as exc:
            if not original.exists():
                self._reset_recycled(
                    proposal_id,
                    f"Restore failed before completion: {type(exc).__name__}: {exc}",
                )
            else:
                self._mark_recovery_required(
                    proposal_id,
                    (
                        "Restore created an original-path file but could not verify a safe "
                        f"final state: {type(exc).__name__}: {exc}"
                    ),
                )
            raise ValueError(f"Restore operation failed: {exc}") from exc

        try:
            self._finalize_restored(proposal_id, snapshot, now)
        except Exception as exc:
            self._mark_recovery_required(
                proposal_id,
                (
                    "Original file was restored from quarantine but database finalization "
                    f"failed: {type(exc).__name__}: {exc}"
                ),
            )
            raise

        return self.get(proposal_id)

    def recover_incomplete_operations(self) -> dict[str, int]:
        with self.database.session() as session:
            ids = [
                item.id
                for item in session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id.is_(None),
                        FileRecycleProposal.status.in_(["recycling", "restoring"]),
                    )
                ).all()
            ]

        recovered = 0
        blocked = 0
        for proposal_id in ids:
            try:
                with self.database.session() as session:
                    proposal = session.get(FileRecycleProposal, proposal_id)
                    if proposal is None:
                        continue
                    status = proposal.status
                    original = Path(proposal.original_path)
                    quarantine = self._assert_quarantine_path(
                        Path(proposal.quarantine_path)
                    )
                    expected_sha = proposal.original_sha256

                original_ok = (
                    original.is_file()
                    and not original.is_symlink()
                    and sha256_file(original) == expected_sha
                )
                quarantine_ok = (
                    quarantine.is_file()
                    and not quarantine.is_symlink()
                    and sha256_file(quarantine) == expected_sha
                )

                if status == "recycling":
                    if original_ok:
                        self._reset_pending(
                            proposal_id,
                            (
                                "DeskAI recovered an interrupted recycle before source "
                                "removal; original file remains intact."
                            ),
                            audit_action="file_recycle_startup_recovered_pending",
                        )
                        recovered += 1
                        continue
                    if (
                        quarantine_ok
                        and not original.exists()
                        and not original.is_symlink()
                    ):
                        snapshot = self._recovery_snapshot(proposal_id)
                        self._finalize_recycled(
                            proposal_id,
                            snapshot,
                            datetime.now(timezone.utc),
                            audit_action="file_recycle_startup_completed",
                        )
                        recovered += 1
                        continue
                elif status == "restoring":
                    if original_ok and quarantine_ok:
                        snapshot = self._recovery_snapshot(proposal_id)
                        self._finalize_restored(
                            proposal_id,
                            snapshot,
                            datetime.now(timezone.utc),
                            audit_action="file_restore_startup_completed",
                        )
                        recovered += 1
                        continue
                    if (
                        quarantine_ok
                        and not original.exists()
                        and not original.is_symlink()
                    ):
                        self._reset_recycled(
                            proposal_id,
                            (
                                "DeskAI recovered an interrupted restore before the "
                                "original path was recreated."
                            ),
                            audit_action="file_restore_startup_recovered_recycled",
                        )
                        recovered += 1
                        continue

                self._mark_recovery_required(
                    proposal_id,
                    (
                        "Automatic recycle recovery could not safely identify an "
                        "unambiguous original/quarantine state."
                    ),
                )
                blocked += 1
            except Exception as exc:
                self._mark_recovery_required(
                    proposal_id,
                    f"Automatic recycle recovery failed: {type(exc).__name__}: {exc}",
                )
                blocked += 1

        return {"recovered": recovered, "blocked": blocked}

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recycle proposal not found")
            file = session.get(File, proposal.file_id)
            filename = (
                file.filename if file is not None else Path(proposal.original_path).name
            )
            return self.payload(proposal, filename=filename)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = (
                select(FileRecycleProposal)
                .order_by(FileRecycleProposal.created_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
            if workspace_id:
                statement = statement.where(
                    FileRecycleProposal.workspace_id == workspace_id
                )
            if task_id:
                statement = statement.where(FileRecycleProposal.task_id == task_id)
            proposals = list(session.scalars(statement).all())
            file_ids = {item.file_id for item in proposals}
            names = (
                {
                    item.id: item.filename
                    for item in session.scalars(
                        select(File).where(File.id.in_(file_ids))
                    ).all()
                }
                if file_ids
                else {}
            )
            return [
                self.payload(
                    item,
                    filename=names.get(item.file_id, Path(item.original_path).name),
                )
                for item in proposals
            ]

    @staticmethod
    def payload(
        proposal: FileRecycleProposal,
        *,
        filename: str,
    ) -> dict[str, Any]:
        return {
            "id": proposal.id,
            "task_id": proposal.task_id,
            "workspace_id": proposal.workspace_id,
            "file_id": proposal.file_id,
            "batch_id": proposal.batch_id,
            "filename": filename,
            "status": proposal.status,
            "summary": proposal.summary,
            "original_path": proposal.original_path,
            "quarantine_path": proposal.quarantine_path,
            "original_sha256": proposal.original_sha256,
            "original_size": proposal.original_size,
            "previous_file_status": proposal.previous_file_status,
            "error_message": proposal.error_message,
            "created_at": proposal.created_at.isoformat(),
            "confirmed_at": (
                proposal.confirmed_at.isoformat() if proposal.confirmed_at else None
            ),
            "recycled_at": (
                proposal.recycled_at.isoformat() if proposal.recycled_at else None
            ),
            "rejected_at": (
                proposal.rejected_at.isoformat() if proposal.rejected_at else None
            ),
            "restored_at": (
                proposal.restored_at.isoformat() if proposal.restored_at else None
            ),
            "requires_user_confirmation": proposal.status == "pending",
            "can_restore": proposal.status == "recycled",
            "recovery_required": proposal.status == "recovery_required",
            "permanent_delete_available": False,
        }

    def _snapshot_for_recycle(self, proposal_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recycle proposal not found")
            if proposal.status != "pending":
                raise ValueError("Recycle proposal must be pending")
            file = session.get(File, proposal.file_id)
            if file is None or file.workspace_id != proposal.workspace_id:
                raise ValueError("File is no longer available in the Workspace")
            task = session.get(Task, proposal.task_id)
            if task is None or task.workspace_id != proposal.workspace_id:
                raise ValueError("Recycle task is no longer available")
            if file.status in {"deleted", "recycled", "revoked", "unsupported"}:
                raise ValueError("File is not currently recyclable")
            source = Path(file.path)
            if source.is_symlink():
                raise ValueError("Symlink source files cannot be recycled")
            try:
                source = source.resolve(strict=True)
                original = Path(proposal.original_path).resolve(strict=True)
            except OSError as exc:
                raise ValueError("Recycle source file is missing or inaccessible") from exc
            if source != original:
                raise ValueError(
                    "File path changed after the recycle proposal; confirmation is blocked"
                )
            root = self._writable_root(
                session,
                proposal.workspace_id,
                source,
            )
            if root is None:
                raise ValueError(
                    "Recycle requires write_allowed on the containing Workspace root"
                )
            session.expunge(root)
            return {
                "proposal_id": proposal.id,
                "task_id": proposal.task_id,
                "workspace_id": proposal.workspace_id,
                "file_id": proposal.file_id,
                "batch_id": proposal.batch_id,
                "source": source,
                "original": source,
                "quarantine": self._assert_quarantine_path(
                    Path(proposal.quarantine_path)
                ),
                "original_sha256": proposal.original_sha256,
                "previous_file_status": proposal.previous_file_status,
                "root": root,
            }

    def _snapshot_for_restore(self, proposal_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recycle proposal not found")
            if proposal.status != "recycled":
                raise ValueError("Recycle proposal must be recycled before restore")
            file = session.get(File, proposal.file_id)
            if file is None or file.workspace_id != proposal.workspace_id:
                raise ValueError("File record is no longer available")
            if file.status != "recycled":
                raise ValueError("File metadata is not in recycled state")
            original = Path(proposal.original_path).resolve(strict=False)
            root = self._writable_root_for_missing_path(
                session,
                proposal.workspace_id,
                original,
            )
            if root is None:
                raise ValueError(
                    "Restore requires write_allowed on the original Workspace root"
                )
            session.expunge(root)
            return {
                "proposal_id": proposal.id,
                "task_id": proposal.task_id,
                "workspace_id": proposal.workspace_id,
                "file_id": proposal.file_id,
                "batch_id": proposal.batch_id,
                "original": original,
                "quarantine": self._require_quarantine_path(proposal.quarantine_path),
                "original_sha256": proposal.original_sha256,
                "previous_file_status": proposal.previous_file_status,
                "root": root,
            }

    def _recovery_snapshot(self, proposal_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recycle proposal not found")
            original = Path(proposal.original_path).resolve(strict=False)
            root = self._writable_root_for_missing_path(
                session,
                proposal.workspace_id,
                original,
            )
            if root is None:
                raise ValueError("Workspace write permission is unavailable for recovery")
            session.expunge(root)
            return {
                "proposal_id": proposal.id,
                "task_id": proposal.task_id,
                "workspace_id": proposal.workspace_id,
                "file_id": proposal.file_id,
                "original": original,
                "quarantine": Path(proposal.quarantine_path).resolve(strict=False),
                "original_sha256": proposal.original_sha256,
                "previous_file_status": proposal.previous_file_status,
                "root": root,
            }

    def _finalize_recycled(
        self,
        proposal_id: str,
        snapshot: dict[str, Any],
        recycled_at: datetime,
        *,
        audit_action: str = "file_recycled",
    ) -> None:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None or proposal.status not in {"recycling", "pending"}:
                raise ValueError("Recycle proposal is not available for finalization")
            file = session.get(File, proposal.file_id)
            if file is None:
                raise ValueError("File record is missing")
            if Path(file.path).resolve(strict=False) != snapshot["original"]:
                raise ValueError("File record path changed during recycle")
            file.status = "recycled"
            proposal.status = "recycled"
            proposal.recycled_at = recycled_at
            proposal.error_message = None
            self._cancel_active_jobs(
                session,
                file.id,
                "File was intentionally moved to the DeskAI recycle bin",
            )
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=(
                        f"proposal_id={proposal.id}; quarantine={proposal.quarantine_path}; "
                        "permanent_delete=false"
                    ),
                    risk_level=6,
                )
            )

    def _finalize_restored(
        self,
        proposal_id: str,
        snapshot: dict[str, Any],
        restored_at: datetime,
        *,
        audit_action: str = "file_restore_completed",
    ) -> None:
        original = snapshot["original"]
        if not original.is_file() or original.is_symlink():
            raise ValueError("Restored file is missing")
        if sha256_file(original) != snapshot["original_sha256"]:
            raise ValueError("Restored file SHA-256 does not match quarantine copy")
        stat = original.stat()

        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None or proposal.status not in {"restoring", "recycled"}:
                raise ValueError("Recycle proposal is not available for restore finalization")
            file = session.get(File, proposal.file_id)
            if file is None:
                raise ValueError("File record is missing")
            file.path = str(original.resolve(strict=True))
            file.filename = original.name
            file.size = stat.st_size
            file.sha256 = proposal.original_sha256
            file.modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            file.status = proposal.previous_file_status
            proposal.status = "restored"
            proposal.restored_at = restored_at
            proposal.error_message = None
            scan_error: str | None = None
            try:
                scan_root(
                    session,
                    snapshot["root"],
                    queue_changes=True,
                    metadata_shortcut=False,
                )
            except Exception as exc:
                scan_error = f"{type(exc).__name__}: {exc}"[:2000]
                proposal.error_message = (
                    f"Restore succeeded; Workspace rescan failed: {scan_error}"
                )
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=(
                        f"proposal_id={proposal.id}; restored={proposal.original_path}; "
                        f"quarantine_retained=true; scan_error={scan_error or 'none'}"
                    ),
                    risk_level=6,
                )
            )

    def _reset_pending(
        self,
        proposal_id: str,
        message: str,
        *,
        audit_action: str = "file_recycle_failed_source_retained",
    ) -> None:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                return
            proposal.status = "pending"
            proposal.confirmed_at = None
            proposal.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=message[:4000],
                    risk_level=6,
                )
            )

    def _reset_recycled(
        self,
        proposal_id: str,
        message: str,
        *,
        audit_action: str = "file_restore_failed_recycled_retained",
    ) -> None:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                return
            proposal.status = "recycled"
            proposal.error_message = message[:4000]
            file = session.get(File, proposal.file_id)
            if file is not None:
                file.status = "recycled"
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=message[:4000],
                    risk_level=6,
                )
            )

    def _mark_recovery_required(self, proposal_id: str, message: str) -> None:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                return
            proposal.status = "recovery_required"
            proposal.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="file_recycle_recovery_required",
                    target=proposal.file_id,
                    result=message[:4000],
                    risk_level=7,
                )
            )

    def _ensure_not_processing(self, file_id: str) -> None:
        with self.database.session() as session:
            processing = session.scalar(
                select(IndexJob.id).where(
                    IndexJob.file_id == file_id,
                    IndexJob.status == "processing",
                )
            )
        if processing:
            raise ValueError(
                "File is currently being parsed/indexed; wait until processing finishes"
            )

    def _ensure_individual_action(self, proposal_id: str) -> None:
        with self.database.session() as session:
            proposal = session.get(FileRecycleProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recycle proposal not found")
            if proposal.batch_id:
                raise ValueError(
                    "This recycle proposal belongs to a transactional batch "
                    "and must be acted on through the batch"
                )

    def _ensure_no_conflicting_proposals(self, file_id: str) -> None:
        with self.database.session() as session:
            edit = session.scalar(
                select(SourceFileEdit.id).where(
                    SourceFileEdit.file_id == file_id,
                    SourceFileEdit.status.in_(ACTIVE_PROPOSAL_STATUSES),
                )
            )
            organization = session.scalar(
                select(FileOrganizationProposal.id).where(
                    FileOrganizationProposal.file_id == file_id,
                    FileOrganizationProposal.status.in_(ACTIVE_PROPOSAL_STATUSES),
                )
            )
            recycle = session.scalar(
                select(FileRecycleProposal.id).where(
                    FileRecycleProposal.file_id == file_id,
                    FileRecycleProposal.status.in_(ACTIVE_RECYCLE_STATUSES),
                )
            )
        if edit or organization or recycle:
            raise ValueError(
                "File already has an active edit, organization, or recycle proposal"
            )

    def _assert_quarantine_path(self, path: Path) -> Path:
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.recycle_root)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Quarantine path escaped the DeskAI private recycle directory"
            ) from exc
        return resolved

    def _require_quarantine_path(self, value: str) -> Path:
        try:
            path = self._assert_quarantine_path(Path(value))
            if path.is_symlink():
                raise ValueError("Quarantine copy cannot be a symlink")
            resolved = path.resolve(strict=True)
            resolved.relative_to(self.recycle_root)
            return resolved
        except OSError as exc:
            raise ValueError("Quarantine copy is missing or inaccessible") from exc

    @staticmethod
    def _cancel_active_jobs(session, file_id: str, reason: str) -> None:
        for job in session.scalars(
            select(IndexJob).where(
                IndexJob.file_id == file_id,
                IndexJob.status.in_(["queued", "processing"]),
            )
        ).all():
            job.status = "cancelled"
            job.error_code = "FILE_RECYCLED"
            job.error_message = reason

    def _ensure_source_available(self, source: Path) -> None:
        self.source_edit_service.probe_source_available(source)

    def _verify_quarantine(self, quarantine: Path, expected_sha: str) -> None:
        quarantine = self._assert_quarantine_path(quarantine)
        if not quarantine.is_file() or quarantine.is_symlink():
            raise ValueError("Verified quarantine copy is missing")
        if sha256_file(quarantine) != expected_sha:
            raise ValueError("Quarantine copy SHA-256 does not match source")

    def _ensure_verified_quarantine_copy(
        self,
        source: Path,
        quarantine: Path,
        expected_sha: str,
    ) -> None:
        quarantine = self._assert_quarantine_path(quarantine)
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        quarantine = self._assert_quarantine_path(quarantine)
        if quarantine.exists() or quarantine.is_symlink():
            self._verify_quarantine(quarantine, expected_sha)
            return

        temp = quarantine.with_name(
            f".{quarantine.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            shutil.copy2(source, temp)
            if sha256_file(temp) != expected_sha:
                raise ValueError("Temporary quarantine copy failed SHA-256 verification")
            os.replace(temp, quarantine)
            self._verify_quarantine(quarantine, expected_sha)
        finally:
            temp.unlink(missing_ok=True)

    def _restore_copy_no_overwrite(
        self,
        quarantine: Path,
        original: Path,
        expected_sha: str,
    ) -> None:
        temp = original.parent / f".deskai-restore-{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(quarantine, temp)
            if sha256_file(temp) != expected_sha:
                raise ValueError("Temporary restore copy failed SHA-256 verification")
            self._rename_no_overwrite(temp, original)
            if not original.is_file() or original.is_symlink():
                raise ValueError("Restored original file is missing")
            if sha256_file(original) != expected_sha:
                raise ValueError("Restored original file failed SHA-256 verification")
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _rename_no_overwrite(source: Path, target: Path) -> None:
        if target.exists() or target.is_symlink():
            raise ValueError("Target path already exists; overwrite is not allowed")
        if os.name == "nt":
            os.rename(source, target)
            return
        os.link(source, target)
        try:
            source.unlink()
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @staticmethod
    def _probe_target_parent(parent: Path) -> None:
        probe = parent / f".deskai-restore-probe-{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                probe,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except OSError as exc:
            raise ValueError("Original directory is not writable") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            probe.unlink(missing_ok=True)

    @staticmethod
    def _writable_root(
        session,
        workspace_id: str,
        source: Path,
    ) -> WorkspaceRoot | None:
        roots = list(
            session.scalars(
                select(WorkspaceRoot).where(
                    WorkspaceRoot.workspace_id == workspace_id,
                    WorkspaceRoot.read_allowed.is_(True),
                    WorkspaceRoot.write_allowed.is_(True),
                )
            ).all()
        )
        for root in roots:
            try:
                require_within(source, normalize_root(root.path))
                return root
            except (OSError, ValueError):
                continue
        return None

    @staticmethod
    def _writable_root_for_missing_path(
        session,
        workspace_id: str,
        path: Path,
    ) -> WorkspaceRoot | None:
        roots = list(
            session.scalars(
                select(WorkspaceRoot).where(
                    WorkspaceRoot.workspace_id == workspace_id,
                    WorkspaceRoot.read_allowed.is_(True),
                    WorkspaceRoot.write_allowed.is_(True),
                )
            ).all()
        )
        try:
            parent = path.parent.resolve(strict=True)
        except OSError:
            return None
        for root in roots:
            try:
                normalized = normalize_root(root.path)
                parent.relative_to(normalized)
                return root
            except (OSError, ValueError):
                continue
        return None
