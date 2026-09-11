from __future__ import annotations

import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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
from app.security.paths import normalize_root
from app.source_edits.service import SourceFileEditService

INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
BUSY_OPERATION_STATUSES = {"pending", "applying", "rolling_back", "recovery_required"}


class FileOrganizationService:
    def __init__(
        self,
        database: Database,
        source_edit_service: SourceFileEditService,
    ) -> None:
        self.database = database
        self.source_edit_service = source_edit_service

    def propose(
        self,
        *,
        task_id: str,
        workspace_id: str,
        file_id: str,
        operation: str,
        summary: str,
        new_name: str,
        target_relative_dir: str,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        file, root, source = self.source_edit_service._editable_file(
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=file_id,
        )
        self._ensure_not_processing(file_id)
        self._ensure_no_active_proposal(file_id)

        current_sha = sha256_file(source)
        if not file.sha256 or current_sha != file.sha256:
            raise ValueError(
                "Source file changed outside DeskAI; rescan it before proposing file organization"
            )

        root_path = normalize_root(root.path)
        operation = str(operation or "").strip().lower()
        if operation == "rename":
            normalized_name = self._validate_filename(new_name, source.suffix)
            target = (source.parent / normalized_name).resolve(strict=False)
        elif operation == "move":
            target_dir = self._resolve_target_dir(root_path, target_relative_dir)
            target = (target_dir / source.name).resolve(strict=False)
        else:
            raise ValueError("Phase 13 supports only rename and move operations")

        self._validate_target(source, target, root_path)
        normalized_summary = (
            str(summary or "").strip()[:1000]
            or f"Proposed {operation} for {file.filename}"
        )
        proposal = FileOrganizationProposal(
            id=str(uuid.uuid4()),
            task_id=task_id,
            workspace_id=workspace_id,
            file_id=file_id,
            batch_id=batch_id,
            operation=operation,
            status="pending",
            summary=normalized_summary,
            original_path=str(source),
            target_path=str(target),
            original_sha256=current_sha,
        )
        with self.database.session() as session:
            session.add(proposal)
            session.flush()
            session.expunge(proposal)
        return self.payload(proposal, filename=file.filename)

    def confirm(self, proposal_id: str) -> dict[str, Any]:
        self._ensure_individual_action(proposal_id)
        snapshot = self._action_snapshot(proposal_id, expected_status="pending")
        source = snapshot["source"]
        target = snapshot["target"]
        self._preflight_move(snapshot, source=source, target=target)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None or proposal.status != "pending":
                raise ValueError("File organization proposal is no longer pending")
            proposal.status = "applying"
            proposal.confirmed_at = now
            proposal.error_message = None

        try:
            self._move_no_overwrite(source, target)
            self._verify_moved(
                source=source,
                target=target,
                expected_sha=snapshot["original_sha256"],
            )
        except Exception as exc:
            try:
                if target.is_file() and not source.exists():
                    self._move_no_overwrite(target, source)
                    self._verify_moved(
                        source=target,
                        target=source,
                        expected_sha=snapshot["original_sha256"],
                    )
                self._reset_pending(
                    proposal_id,
                    (
                        "File organization apply failed before completion; "
                        f"original path is intact: {type(exc).__name__}: {exc}"
                    ),
                )
            except Exception as recovery_exc:
                self._mark_recovery_required(
                    proposal_id,
                    (
                        "File organization apply failed and the original path could not "
                        f"be safely restored: {type(exc).__name__}: {exc}; "
                        f"recovery={type(recovery_exc).__name__}: {recovery_exc}"
                    ),
                )
            raise ValueError(f"File organization apply failed: {exc}") from exc

        try:
            self._finalize_applied(proposal_id, snapshot, now)
        except Exception as exc:
            try:
                self._move_no_overwrite(target, source)
                self._verify_moved(
                    source=target,
                    target=source,
                    expected_sha=snapshot["original_sha256"],
                )
                self._reset_pending(
                    proposal_id,
                    (
                        "Database finalization failed after the path change; "
                        f"DeskAI restored the original path: {type(exc).__name__}: {exc}"
                    ),
                )
            except Exception as recovery_exc:
                self._mark_recovery_required(
                    proposal_id,
                    (
                        "Database finalization failed and DeskAI could not restore the "
                        f"original path: {type(exc).__name__}: {exc}; "
                        f"recovery={type(recovery_exc).__name__}: {recovery_exc}"
                    ),
                )
            raise

        return self.get(proposal_id)

    def reject(self, proposal_id: str) -> dict[str, Any]:
        self._ensure_individual_action(proposal_id)
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                raise ValueError("File organization proposal not found")
            if proposal.status != "pending":
                raise ValueError("Only pending file organization proposals can be rejected")
            proposal.status = "rejected"
            proposal.rejected_at = now
            proposal.error_message = None
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="file_organization_rejected",
                    target=proposal.file_id,
                    result=f"proposal_id={proposal.id}; operation={proposal.operation}",
                    risk_level=3,
                )
            )
        return self.get(proposal_id)

    def rollback(self, proposal_id: str) -> dict[str, Any]:
        self._ensure_individual_action(proposal_id)
        snapshot = self._action_snapshot(proposal_id, expected_status="applied")
        current = snapshot["source"]
        original = snapshot["target"]
        applied_sha = snapshot["applied_sha256"]
        if not applied_sha:
            raise ValueError("Applied file organization hash is missing")
        if sha256_file(current) != applied_sha:
            raise ValueError(
                "File changed after the organization operation; automatic rollback is blocked"
            )
        if original.exists() or original.is_symlink():
            raise ValueError("Original path is occupied; automatic rollback is blocked")
        self.source_edit_service.probe_source_available(current)
        self._ensure_not_processing(snapshot["file_id"])
        self._ensure_same_device(current, original.parent)
        self._probe_target_parent(original.parent)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None or proposal.status != "applied":
                raise ValueError("File organization proposal is no longer applied")
            proposal.status = "rolling_back"
            proposal.error_message = None

        try:
            self._move_no_overwrite(current, original)
            self._verify_moved(
                source=current,
                target=original,
                expected_sha=snapshot["original_sha256"],
            )
        except Exception as exc:
            try:
                if original.is_file() and not current.exists():
                    self._move_no_overwrite(original, current)
                    self._verify_moved(
                        source=original,
                        target=current,
                        expected_sha=snapshot["original_sha256"],
                    )
                self._reset_applied(
                    proposal_id,
                    (
                        "File organization rollback failed before completion; "
                        f"applied path is intact: {type(exc).__name__}: {exc}"
                    ),
                )
            except Exception as recovery_exc:
                self._mark_recovery_required(
                    proposal_id,
                    (
                        "File organization rollback failed and the applied path could not "
                        f"be safely restored: {type(exc).__name__}: {exc}; "
                        f"recovery={type(recovery_exc).__name__}: {recovery_exc}"
                    ),
                )
            raise ValueError(f"File organization rollback failed: {exc}") from exc

        try:
            self._finalize_rolled_back(proposal_id, snapshot, now)
        except Exception as exc:
            try:
                self._move_no_overwrite(original, current)
                self._verify_moved(
                    source=original,
                    target=current,
                    expected_sha=snapshot["original_sha256"],
                )
                self._reset_applied(
                    proposal_id,
                    (
                        "Rollback database finalization failed; DeskAI restored the "
                        f"applied path: {type(exc).__name__}: {exc}"
                    ),
                )
            except Exception as recovery_exc:
                self._mark_recovery_required(
                    proposal_id,
                    (
                        "Rollback database finalization failed and the applied path could "
                        f"not be restored: {type(exc).__name__}: {exc}; "
                        f"recovery={type(recovery_exc).__name__}: {recovery_exc}"
                    ),
                )
            raise

        return self.get(proposal_id)

    def recover_incomplete_operations(self) -> dict[str, int]:
        with self.database.session() as session:
            ids = [
                item.id
                for item in session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id.is_(None),
                        FileOrganizationProposal.status.in_(["applying", "rolling_back"]),
                    )
                ).all()
            ]

        recovered = 0
        blocked = 0
        for proposal_id in ids:
            try:
                with self.database.session() as session:
                    proposal = session.get(FileOrganizationProposal, proposal_id)
                    if proposal is None:
                        continue
                    status = proposal.status
                    original = Path(proposal.original_path)
                    target = Path(proposal.target_path)
                    expected_sha = proposal.original_sha256

                original_ok = (
                    original.is_file()
                    and not original.is_symlink()
                    and sha256_file(original) == expected_sha
                )
                target_ok = (
                    target.is_file()
                    and not target.is_symlink()
                    and sha256_file(target) == expected_sha
                )

                if status == "applying":
                    if original_ok and not target.exists() and not target.is_symlink():
                        self._reset_pending(
                            proposal_id,
                            "DeskAI recovered an interrupted organization operation before the path change.",
                            audit_action="file_organization_startup_recovered_pending",
                        )
                        recovered += 1
                        continue
                    if target_ok and not original.exists() and not original.is_symlink():
                        snapshot = self._action_snapshot_recovery(proposal_id, current_path=target)
                        self._finalize_applied(
                            proposal_id,
                            snapshot,
                            datetime.now(timezone.utc),
                            audit_action="file_organization_startup_completed_apply",
                        )
                        recovered += 1
                        continue
                elif status == "rolling_back":
                    if target_ok and not original.exists() and not original.is_symlink():
                        self._reset_applied(
                            proposal_id,
                            "DeskAI recovered an interrupted rollback before the reverse path change.",
                            audit_action="file_organization_startup_recovered_applied",
                        )
                        recovered += 1
                        continue
                    if original_ok and not target.exists() and not target.is_symlink():
                        snapshot = self._action_snapshot_recovery(
                            proposal_id,
                            current_path=original,
                        )
                        self._finalize_rolled_back(
                            proposal_id,
                            snapshot,
                            datetime.now(timezone.utc),
                            audit_action="file_organization_startup_completed_rollback",
                        )
                        recovered += 1
                        continue

                self._mark_recovery_required(
                    proposal_id,
                    (
                        "Automatic startup recovery could not safely identify a single "
                        "original/applied file path with the expected SHA-256."
                    ),
                )
                blocked += 1
            except Exception as exc:
                self._mark_recovery_required(
                    proposal_id,
                    f"Automatic startup recovery failed: {type(exc).__name__}: {exc}",
                )
                blocked += 1
        return {"recovered": recovered, "blocked": blocked}

    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                raise ValueError("File organization proposal not found")
            file = session.get(File, proposal.file_id)
            filename = file.filename if file is not None else Path(proposal.original_path).name
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
                select(FileOrganizationProposal)
                .order_by(FileOrganizationProposal.created_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
            if workspace_id:
                statement = statement.where(
                    FileOrganizationProposal.workspace_id == workspace_id
                )
            if task_id:
                statement = statement.where(FileOrganizationProposal.task_id == task_id)
            proposals = list(session.scalars(statement).all())
            file_ids = {item.file_id for item in proposals}
            names = (
                {
                    item.id: item.filename
                    for item in session.scalars(select(File).where(File.id.in_(file_ids))).all()
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
        proposal: FileOrganizationProposal,
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
            "operation": proposal.operation,
            "status": proposal.status,
            "summary": proposal.summary,
            "original_path": proposal.original_path,
            "target_path": proposal.target_path,
            "original_sha256": proposal.original_sha256,
            "applied_sha256": proposal.applied_sha256,
            "error_message": proposal.error_message,
            "created_at": proposal.created_at.isoformat(),
            "confirmed_at": (
                proposal.confirmed_at.isoformat() if proposal.confirmed_at else None
            ),
            "applied_at": proposal.applied_at.isoformat() if proposal.applied_at else None,
            "rejected_at": (
                proposal.rejected_at.isoformat() if proposal.rejected_at else None
            ),
            "rolled_back_at": (
                proposal.rolled_back_at.isoformat() if proposal.rolled_back_at else None
            ),
            "requires_user_confirmation": proposal.status == "pending",
            "can_rollback": proposal.status == "applied",
            "recovery_required": proposal.status == "recovery_required",
        }

    def _ensure_individual_action(self, proposal_id: str) -> None:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                raise ValueError("File organization proposal not found")
            if proposal.batch_id:
                raise ValueError(
                    "This file organization proposal belongs to a transactional batch "
                    "and must be acted on through the batch"
                )

    def _action_snapshot(
        self,
        proposal_id: str,
        *,
        expected_status: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                raise ValueError("File organization proposal not found")
            if proposal.status != expected_status:
                raise ValueError(
                    f"File organization proposal must be {expected_status}"
                )
            file = session.get(File, proposal.file_id)
            if file is None or file.workspace_id != proposal.workspace_id:
                raise ValueError("File is no longer available in the Workspace")
            task = session.get(Task, proposal.task_id)
            if task is None or task.workspace_id != proposal.workspace_id:
                raise ValueError("File organization task is no longer available")

            roots = list(
                session.scalars(
                    select(WorkspaceRoot).where(
                        WorkspaceRoot.workspace_id == proposal.workspace_id,
                        WorkspaceRoot.read_allowed.is_(True),
                        WorkspaceRoot.write_allowed.is_(True),
                    )
                ).all()
            )
            original = Path(proposal.original_path)
            target = Path(proposal.target_path)
            expected_current = original if expected_status == "pending" else target
            current = Path(file.path)
            if current.is_symlink():
                raise ValueError("Symlink source files cannot be organized")
            current = current.resolve(strict=True)
            expected_current = expected_current.resolve(strict=True)
            if current != expected_current:
                raise ValueError(
                    "File path changed after the organization proposal; action is blocked"
                )

            root = self._find_common_root(roots, original=original, target=target)
            if root is None:
                raise ValueError(
                    "Workspace write permission was removed or target left the authorized root"
                )
            session.expunge(root)
            return {
                "proposal_id": proposal.id,
                "task_id": proposal.task_id,
                "workspace_id": proposal.workspace_id,
                "file_id": proposal.file_id,
                "batch_id": proposal.batch_id,
                "operation": proposal.operation,
                "source": current,
                "target": (
                    target.resolve(strict=False)
                    if expected_status == "pending"
                    else original.resolve(strict=False)
                ),
                "original_path": original.resolve(strict=False),
                "target_path": target.resolve(strict=False),
                "root": root,
                "original_sha256": proposal.original_sha256,
                "applied_sha256": proposal.applied_sha256,
            }

    def _action_snapshot_recovery(
        self,
        proposal_id: str,
        *,
        current_path: Path,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                raise ValueError("File organization proposal not found")
            file = session.get(File, proposal.file_id)
            if file is None or file.workspace_id != proposal.workspace_id:
                raise ValueError("File is no longer available in the Workspace")
            roots = list(
                session.scalars(
                    select(WorkspaceRoot).where(
                        WorkspaceRoot.workspace_id == proposal.workspace_id,
                        WorkspaceRoot.read_allowed.is_(True),
                        WorkspaceRoot.write_allowed.is_(True),
                    )
                ).all()
            )
            original = Path(proposal.original_path)
            target = Path(proposal.target_path)
            root = self._find_common_root(roots, original=original, target=target)
            if root is None:
                raise ValueError("Workspace write permission is not available for recovery")
            session.expunge(root)
            return {
                "proposal_id": proposal.id,
                "task_id": proposal.task_id,
                "workspace_id": proposal.workspace_id,
                "file_id": proposal.file_id,
                "operation": proposal.operation,
                "source": current_path.resolve(strict=True),
                "target": original.resolve(strict=False),
                "original_path": original.resolve(strict=False),
                "target_path": target.resolve(strict=False),
                "root": root,
                "original_sha256": proposal.original_sha256,
                "applied_sha256": proposal.applied_sha256,
            }

    def _preflight_move(
        self,
        snapshot: dict[str, Any],
        *,
        source: Path,
        target: Path,
    ) -> None:
        self._ensure_not_processing(snapshot["file_id"])
        if sha256_file(source) != snapshot["original_sha256"]:
            raise ValueError(
                "File changed after the organization proposal; confirmation is blocked"
            )
        if target.exists() or target.is_symlink():
            raise ValueError("Target path already exists; overwrite is not allowed")
        if not target.parent.is_dir():
            raise ValueError("Target directory no longer exists")
        self.source_edit_service.probe_source_available(source)
        self._ensure_same_device(source, target.parent)
        self._probe_target_parent(target.parent)

    def _finalize_applied(
        self,
        proposal_id: str,
        snapshot: dict[str, Any],
        applied_at: datetime,
        *,
        audit_action: str = "file_organization_applied",
    ) -> None:
        target = snapshot["target_path"]
        scan_error: str | None = None
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None or proposal.status != "applying":
                raise ValueError("File organization proposal is not in applying state")
            file = session.get(File, proposal.file_id)
            if file is None:
                raise ValueError("File record is missing")
            self._update_file_path(file, target)
            proposal.status = "applied"
            proposal.applied_at = applied_at
            proposal.applied_sha256 = proposal.original_sha256
            proposal.error_message = None
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
                    f"Path change succeeded; reindex rescan failed: {scan_error}"
                )
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=(
                        f"proposal_id={proposal.id}; operation={proposal.operation}; "
                        f"target={proposal.target_path}; "
                        + (f"scan_error={scan_error}" if scan_error else "reindex_scan=ok")
                    ),
                    risk_level=5,
                )
            )

    def _finalize_rolled_back(
        self,
        proposal_id: str,
        snapshot: dict[str, Any],
        rolled_back_at: datetime,
        *,
        audit_action: str = "file_organization_rolled_back",
    ) -> None:
        original = snapshot["original_path"]
        scan_error: str | None = None
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None or proposal.status not in {"rolling_back", "applied"}:
                raise ValueError("File organization proposal is not available for rollback")
            file = session.get(File, proposal.file_id)
            if file is None:
                raise ValueError("File record is missing")
            self._update_file_path(file, original)
            proposal.status = "rolled_back"
            proposal.rolled_back_at = rolled_back_at
            proposal.error_message = None
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
                    f"Path rollback succeeded; reindex rescan failed: {scan_error}"
                )
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=(
                        f"proposal_id={proposal.id}; restored={proposal.original_path}; "
                        + (f"scan_error={scan_error}" if scan_error else "reindex_scan=ok")
                    ),
                    risk_level=5,
                )
            )

    def _reset_pending(
        self,
        proposal_id: str,
        message: str,
        *,
        audit_action: str = "file_organization_apply_failed",
    ) -> None:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                return
            proposal.status = "pending"
            proposal.confirmed_at = None
            proposal.applied_at = None
            proposal.applied_sha256 = None
            proposal.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=message[:4000],
                    risk_level=5,
                )
            )

    def _reset_applied(
        self,
        proposal_id: str,
        message: str,
        *,
        audit_action: str = "file_organization_rollback_failed",
    ) -> None:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                return
            proposal.status = "applied"
            proposal.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action=audit_action,
                    target=proposal.file_id,
                    result=message[:4000],
                    risk_level=5,
                )
            )

    def _mark_recovery_required(self, proposal_id: str, message: str) -> None:
        with self.database.session() as session:
            proposal = session.get(FileOrganizationProposal, proposal_id)
            if proposal is None:
                return
            proposal.status = "recovery_required"
            proposal.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="file_organization_recovery_required",
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

    def _ensure_no_active_proposal(self, file_id: str) -> None:
        with self.database.session() as session:
            active = session.scalar(
                select(FileOrganizationProposal.id).where(
                    FileOrganizationProposal.file_id == file_id,
                    FileOrganizationProposal.status.in_(BUSY_OPERATION_STATUSES),
                )
            )
            edit = session.scalar(
                select(SourceFileEdit.id).where(
                    SourceFileEdit.file_id == file_id,
                    SourceFileEdit.status.in_(BUSY_OPERATION_STATUSES),
                )
            )
            recycle = session.scalar(
                select(FileRecycleProposal.id).where(
                    FileRecycleProposal.file_id == file_id,
                    FileRecycleProposal.status.in_(
                        {"pending", "recycling", "restoring", "recovery_required"}
                    ),
                )
            )
        if active or edit or recycle:
            raise ValueError(
                "This file already has an active edit, organization, or recycle proposal"
            )

    @staticmethod
    def _validate_filename(name: str, expected_suffix: str) -> str:
        value = str(name or "")
        if not value or value != value.strip():
            raise ValueError("New filename cannot be empty or have leading/trailing spaces")
        if len(value) > 240:
            raise ValueError("New filename is too long")
        if value in {".", ".."} or Path(value).name != value:
            raise ValueError("New filename must be a filename only, not a path")
        if value.endswith(".") or any(ord(char) < 32 for char in value):
            raise ValueError("New filename contains Windows-incompatible characters")
        if any(char in INVALID_FILENAME_CHARS for char in value):
            raise ValueError("New filename contains Windows-incompatible characters")
        reserved_base = value.split(".", 1)[0].upper()
        if reserved_base in WINDOWS_RESERVED_NAMES:
            raise ValueError("New filename is reserved by Windows")
        if Path(value).suffix.lower() != expected_suffix.lower():
            raise ValueError("Phase 13 rename must preserve the file extension")
        return value

    @staticmethod
    def _resolve_target_dir(root: Path, relative_dir: str) -> Path:
        raw = str(relative_dir or "").strip().replace("\\", "/")
        if not raw:
            raise ValueError("Move requires a target_relative_dir")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Target directory must stay inside the same Workspace root")
        target_dir = root.joinpath(*relative.parts).resolve(strict=True)
        try:
            target_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "Target directory must stay inside the same Workspace root"
            ) from exc
        if not target_dir.is_dir():
            raise ValueError("Target directory must already exist")
        return target_dir

    @staticmethod
    def _validate_target(source: Path, target: Path, root: Path) -> None:
        target_parent = target.parent.resolve(strict=True)
        try:
            target_parent.relative_to(root)
        except ValueError as exc:
            raise ValueError("Target path is outside the authorized Workspace root") from exc
        canonical_target = (target_parent / target.name).resolve(strict=False)
        if canonical_target == source:
            raise ValueError("Organization proposal would not change the file path")
        if canonical_target.exists() or canonical_target.is_symlink():
            raise ValueError("Target path already exists; overwrite is not allowed")

    @staticmethod
    def _find_common_root(
        roots: list[WorkspaceRoot],
        *,
        original: Path,
        target: Path,
    ) -> WorkspaceRoot | None:
        original_parent = original.parent.resolve(strict=True)
        target_parent = target.parent.resolve(strict=True)
        for root in roots:
            try:
                normalized = normalize_root(root.path)
                original_parent.relative_to(normalized)
                target_parent.relative_to(normalized)
                return root
            except (OSError, ValueError):
                continue
        return None

    @staticmethod
    def _ensure_same_device(source: Path, target_parent: Path) -> None:
        if source.stat().st_dev != target_parent.stat().st_dev:
            raise ValueError(
                "Phase 13 move is limited to the same filesystem/device"
            )

    @staticmethod
    def _probe_target_parent(target_parent: Path) -> None:
        probe = target_parent / f".deskai-write-probe-{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                probe,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except OSError as exc:
            raise ValueError(
                "Target directory is not writable"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            probe.unlink(missing_ok=True)

    @staticmethod
    def _move_no_overwrite(source: Path, target: Path) -> None:
        if target.exists() or target.is_symlink():
            raise ValueError("Target path already exists; overwrite is not allowed")
        if os.name == "nt":
            os.rename(source, target)
            return

        # POSIX os.rename may replace an existing destination. A hard-link +
        # unlink sequence gives no-overwrite creation semantics for same-device files.
        os.link(source, target)
        try:
            source.unlink()
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @staticmethod
    def _verify_moved(
        *,
        source: Path,
        target: Path,
        expected_sha: str,
    ) -> None:
        if source.exists() or source.is_symlink():
            raise ValueError("Source path still exists after organization operation")
        if not target.is_file() or target.is_symlink():
            raise ValueError("Target file is missing after organization operation")
        if sha256_file(target) != expected_sha:
            raise ValueError("Target SHA-256 changed during organization operation")

    @staticmethod
    def _update_file_path(file: File, path: Path) -> None:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
        file.path = str(resolved)
        file.filename = resolved.name
        file.extension = resolved.suffix.lower()
        file.mime_type = mimetypes.guess_type(resolved.name)[0]
        file.size = stat.st_size
        file.modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        file.sha256 = sha256_file(resolved)
