from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.models import (
    AuditLog,
    File,
    FileOrganizationBatch,
    FileOrganizationProposal,
    WorkspaceRoot,
)
from app.database.session import Database
from app.file_ops.service import FileOrganizationService
from app.indexing.hashing import sha256_file
from app.indexing.scanner import scan_root

MIN_BATCH_OPERATIONS = 2
MAX_BATCH_OPERATIONS = 10


class FileOrganizationBatchService:
    def __init__(
        self,
        database: Database,
        organization_service: FileOrganizationService,
    ) -> None:
        self.database = database
        self.organization_service = organization_service

    def propose(
        self,
        *,
        task_id: str,
        workspace_id: str,
        summary: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(operations, list) or not (
            MIN_BATCH_OPERATIONS <= len(operations) <= MAX_BATCH_OPERATIONS
        ):
            raise ValueError(
                "A file organization batch must contain between 2 and 10 operations"
            )

        file_ids = [
            str(item.get("file_id") or "").strip()
            for item in operations
            if isinstance(item, dict)
        ]
        if len(file_ids) != len(operations) or any(not value for value in file_ids):
            raise ValueError("Every organization operation must provide a file_id")
        if len(set(file_ids)) != len(file_ids):
            raise ValueError(
                "A file organization batch cannot contain the same file more than once"
            )

        batch_id = str(uuid.uuid4())
        normalized_summary = (
            str(summary or "").strip()[:1000]
            or "Transactional file organization batch"
        )
        created_ids: list[str] = []
        try:
            for raw in operations:
                if not isinstance(raw, dict):
                    raise ValueError("Every organization operation must be an object")
                payload = self.organization_service.propose(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    file_id=str(raw.get("file_id") or ""),
                    operation=str(raw.get("operation") or ""),
                    summary=str(raw.get("summary") or normalized_summary),
                    new_name=str(raw.get("new_name") or ""),
                    target_relative_dir=str(raw.get("target_relative_dir") or ""),
                    batch_id=batch_id,
                )
                created_ids.append(payload["id"])

            proposals = self._proposal_records(batch_id)
            target_paths = [item.target_path.casefold() for item in proposals]
            if len(set(target_paths)) != len(target_paths):
                raise ValueError(
                    "A file organization batch cannot contain duplicate target paths"
                )
            source_paths = {item.original_path.casefold() for item in proposals}
            if any(item.target_path.casefold() in source_paths for item in proposals):
                raise ValueError(
                    "Phase 14 does not allow a batch target to be another member's source path"
                )

            batch = FileOrganizationBatch(
                id=batch_id,
                task_id=task_id,
                workspace_id=workspace_id,
                status="pending",
                summary=normalized_summary,
                operation_count=len(created_ids),
            )
            with self.database.session() as session:
                session.add(batch)
            return self.get(batch_id)
        except Exception:
            self._cleanup_failed_proposal(batch_id)
            raise

    def confirm(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch_record(batch_id, expected_status="pending")
        snapshots = self._snapshots(batch, expected_status="pending")
        self._preflight_apply(snapshots)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            current = session.get(FileOrganizationBatch, batch_id)
            if current is None or current.status != "pending":
                raise ValueError("File organization batch is no longer pending")
            members = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            if len(members) != current.operation_count:
                raise ValueError("File organization batch membership is incomplete")
            if any(item.status != "pending" for item in members):
                raise ValueError("Every file organization batch member must still be pending")
            current.status = "applying"
            current.confirmed_at = now
            current.error_message = None
            for item in members:
                item.status = "applying"
                item.confirmed_at = now
                item.error_message = None

        moved: list[dict[str, Any]] = []
        try:
            for snapshot in snapshots:
                self.organization_service._move_no_overwrite(
                    snapshot["source"],
                    snapshot["target"],
                )
                moved.append(snapshot)
                self.organization_service._verify_moved(
                    source=snapshot["source"],
                    target=snapshot["target"],
                    expected_sha=snapshot["original_sha256"],
                )
        except Exception as exc:
            failures = self._restore_original_paths(moved)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch file organization failed and automatic rollback was "
                        f"incomplete: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
                raise ValueError(
                    "Batch file organization failed and automatic rollback could not "
                    "restore every moved file; manual recovery is required"
                ) from exc
            self._reset_apply_to_pending(
                batch_id,
                (
                    "Batch file organization failed; every moved file was restored: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            raise ValueError(
                "Batch file organization failed; all already-moved files were restored"
            ) from exc

        try:
            self._finalize_applied(batch_id, snapshots, now)
        except Exception as exc:
            failures = self._restore_original_paths(moved)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch database finalization failed and path rollback was "
                        f"incomplete: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
            else:
                self._reset_apply_to_pending(
                    batch_id,
                    (
                        "Batch database finalization failed; every file was restored "
                        f"to its original path: {type(exc).__name__}: {exc}"
                    ),
                )
            raise

        return self.get(batch_id)

    def reject(self, batch_id: str) -> dict[str, Any]:
        self._batch_record(batch_id, expected_status="pending")
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None or batch.status != "pending":
                raise ValueError("Only pending file organization batches can be rejected")
            members = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            if len(members) != batch.operation_count:
                raise ValueError("File organization batch membership is incomplete")
            if any(item.status != "pending" for item in members):
                raise ValueError("Every file organization batch member must still be pending")
            batch.status = "rejected"
            batch.rejected_at = now
            batch.error_message = None
            for item in members:
                item.status = "rejected"
                item.rejected_at = now
                item.error_message = None
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_organization_batch_rejected",
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; operation_count={batch.operation_count}"
                    ),
                    risk_level=3,
                )
            )
        return self.get(batch_id)

    def rollback(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch_record(batch_id, expected_status="applied")
        snapshots = self._snapshots(batch, expected_status="applied")
        self._preflight_rollback(snapshots)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            current = session.get(FileOrganizationBatch, batch_id)
            if current is None or current.status != "applied":
                raise ValueError("File organization batch is no longer applied")
            members = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            if len(members) != current.operation_count:
                raise ValueError("File organization batch membership is incomplete")
            if any(item.status != "applied" for item in members):
                raise ValueError("Every file organization batch member must still be applied")
            current.status = "rolling_back"
            current.error_message = None
            for item in members:
                item.status = "rolling_back"
                item.error_message = None

        restored: list[dict[str, Any]] = []
        try:
            for snapshot in snapshots:
                self.organization_service._move_no_overwrite(
                    snapshot["source"],
                    snapshot["target"],
                )
                restored.append(snapshot)
                self.organization_service._verify_moved(
                    source=snapshot["source"],
                    target=snapshot["target"],
                    expected_sha=snapshot["original_sha256"],
                )
        except Exception as exc:
            failures = self._reapply_target_paths(restored)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch path rollback failed and the applied state could not be "
                        f"fully restored: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
                raise ValueError(
                    "Batch path rollback failed and DeskAI could not restore the "
                    "previously applied state; manual recovery is required"
                ) from exc
            self._reset_rollback_to_applied(
                batch_id,
                (
                    "Batch path rollback failed; DeskAI restored the previously applied "
                    f"state: {type(exc).__name__}: {exc}"
                ),
            )
            raise ValueError(
                "Batch path rollback failed; DeskAI restored the previously applied state"
            ) from exc

        try:
            self._finalize_rolled_back(batch_id, snapshots, now)
        except Exception as exc:
            failures = self._reapply_target_paths(restored)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch rollback database finalization failed and the applied "
                        f"state could not be restored: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
            else:
                self._reset_rollback_to_applied(
                    batch_id,
                    (
                        "Batch rollback database finalization failed; DeskAI restored "
                        f"the applied paths: {type(exc).__name__}: {exc}"
                    ),
                )
            raise

        return self.get(batch_id)

    def recover_incomplete_batches(self) -> dict[str, int]:
        with self.database.session() as session:
            ids = [
                item.id
                for item in session.scalars(
                    select(FileOrganizationBatch).where(
                        FileOrganizationBatch.status.in_(["applying", "rolling_back"])
                    )
                ).all()
            ]

        recovered = 0
        blocked = 0
        for batch_id in ids:
            try:
                batch = self._batch_record(batch_id)
                snapshots = self._recovery_snapshots(batch)
                invalid = [
                    item
                    for item in snapshots
                    if item["location"] not in {"original", "target"}
                ]
                if invalid:
                    self._mark_recovery_required(
                        batch_id,
                        "Startup recovery found ambiguous or missing batch members: "
                        + "; ".join(item["filename"] for item in invalid),
                    )
                    blocked += 1
                    continue

                failures: list[str] = []
                for item in snapshots:
                    if item["location"] == "original":
                        continue
                    try:
                        current = item["target_path"]
                        original = item["original_path"]
                        self.organization_service.source_edit_service.probe_source_available(
                            current
                        )
                        self.organization_service._ensure_same_device(
                            current,
                            original.parent,
                        )
                        self.organization_service._probe_target_parent(original.parent)
                        self.organization_service._move_no_overwrite(current, original)
                        self.organization_service._verify_moved(
                            source=current,
                            target=original,
                            expected_sha=item["original_sha256"],
                        )
                    except Exception as exc:
                        failures.append(
                            f"{item['filename']}: {type(exc).__name__}: {exc}"
                        )

                if failures:
                    self._mark_recovery_required(
                        batch_id,
                        "Startup recovery could not restore the full batch: "
                        + "; ".join(failures),
                    )
                    blocked += 1
                    continue

                if batch.status == "applying":
                    self._reset_apply_to_pending(
                        batch_id,
                        "DeskAI recovered an interrupted file organization batch and "
                        "restored every member to its original path.",
                        audit_action="file_organization_batch_startup_recovered",
                    )
                else:
                    finalized = self._recovery_snapshots(batch)
                    if any(item["location"] != "original" for item in finalized):
                        self._mark_recovery_required(
                            batch_id,
                            "Startup rollback recovery did not leave every member at "
                            "its original path.",
                        )
                        blocked += 1
                        continue
                    self._finalize_recovered_rollback(
                        batch_id,
                        finalized,
                        datetime.now(timezone.utc),
                    )
                recovered += 1
            except Exception as exc:
                self._mark_recovery_required(
                    batch_id,
                    f"Automatic batch startup recovery failed: {type(exc).__name__}: {exc}",
                )
                blocked += 1

        return {"recovered": recovered, "blocked": blocked}

    def get(self, batch_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None:
                raise ValueError("File organization batch not found")
            proposals = list(
                session.scalars(
                    select(FileOrganizationProposal)
                    .where(FileOrganizationProposal.batch_id == batch_id)
                    .order_by(
                        FileOrganizationProposal.created_at,
                        FileOrganizationProposal.id,
                    )
                ).all()
            )
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
            members = [
                self.organization_service.payload(
                    item,
                    filename=names.get(item.file_id, Path(item.original_path).name),
                )
                for item in proposals
            ]
            return self._payload(batch, members)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = (
                select(FileOrganizationBatch)
                .order_by(FileOrganizationBatch.created_at.desc())
                .limit(max(1, min(int(limit), 200)))
            )
            if workspace_id:
                statement = statement.where(
                    FileOrganizationBatch.workspace_id == workspace_id
                )
            if task_id:
                statement = statement.where(FileOrganizationBatch.task_id == task_id)
            ids = [item.id for item in session.scalars(statement).all()]
        return [self.get(batch_id) for batch_id in ids]

    @staticmethod
    def _payload(
        batch: FileOrganizationBatch,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": batch.id,
            "task_id": batch.task_id,
            "workspace_id": batch.workspace_id,
            "status": batch.status,
            "summary": batch.summary,
            "operation_count": batch.operation_count,
            "error_message": batch.error_message,
            "created_at": batch.created_at.isoformat(),
            "confirmed_at": (
                batch.confirmed_at.isoformat() if batch.confirmed_at else None
            ),
            "applied_at": batch.applied_at.isoformat() if batch.applied_at else None,
            "rejected_at": (
                batch.rejected_at.isoformat() if batch.rejected_at else None
            ),
            "rolled_back_at": (
                batch.rolled_back_at.isoformat() if batch.rolled_back_at else None
            ),
            "requires_user_confirmation": batch.status == "pending",
            "can_rollback": batch.status == "applied",
            "recovery_required": batch.status == "recovery_required",
            "transactional": True,
            "operations": operations,
        }

    def _batch_record(
        self,
        batch_id: str,
        *,
        expected_status: str | None = None,
    ) -> FileOrganizationBatch:
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None:
                raise ValueError("File organization batch not found")
            if expected_status and batch.status != expected_status:
                raise ValueError(
                    f"File organization batch must be {expected_status}"
                )
            session.expunge(batch)
            return batch

    def _proposal_records(self, batch_id: str) -> list[FileOrganizationProposal]:
        with self.database.session() as session:
            records = list(
                session.scalars(
                    select(FileOrganizationProposal)
                    .where(FileOrganizationProposal.batch_id == batch_id)
                    .order_by(
                        FileOrganizationProposal.created_at,
                        FileOrganizationProposal.id,
                    )
                ).all()
            )
            for item in records:
                session.expunge(item)
            return records

    def _snapshots(
        self,
        batch: FileOrganizationBatch,
        *,
        expected_status: str,
    ) -> list[dict[str, Any]]:
        proposals = self._proposal_records(batch.id)
        if len(proposals) != batch.operation_count or len(proposals) < MIN_BATCH_OPERATIONS:
            raise ValueError("File organization batch membership is incomplete")
        snapshots = [
            self.organization_service._action_snapshot(
                item.id,
                expected_status=expected_status,
            )
            for item in proposals
        ]
        if any(item["batch_id"] != batch.id for item in snapshots):
            raise ValueError("File organization batch membership is inconsistent")
        targets = [str(item["target_path"]).casefold() for item in snapshots]
        if len(set(targets)) != len(targets):
            raise ValueError("File organization batch contains duplicate target paths")
        return snapshots

    def _preflight_apply(self, snapshots: list[dict[str, Any]]) -> None:
        source_paths = {str(item["source"]).casefold() for item in snapshots}
        target_paths = [str(item["target"]).casefold() for item in snapshots]
        if len(set(target_paths)) != len(target_paths):
            raise ValueError("File organization batch contains duplicate target paths")
        if any(value in source_paths for value in target_paths):
            raise ValueError(
                "Batch target paths cannot overlap batch source paths"
            )
        for snapshot in snapshots:
            self.organization_service._preflight_move(
                snapshot,
                source=snapshot["source"],
                target=snapshot["target"],
            )

    def _preflight_rollback(self, snapshots: list[dict[str, Any]]) -> None:
        original_paths = [str(item["target"]).casefold() for item in snapshots]
        if len(set(original_paths)) != len(original_paths):
            raise ValueError("File organization batch contains duplicate original paths")
        for snapshot in snapshots:
            current = snapshot["source"]
            original = snapshot["target"]
            applied_sha = snapshot["applied_sha256"]
            if not applied_sha or sha256_file(current) != applied_sha:
                raise ValueError(
                    "A file changed after the batch was applied; batch rollback is blocked"
                )
            if original.exists() or original.is_symlink():
                raise ValueError(
                    "An original path is occupied; batch rollback is blocked"
                )
            self.organization_service.source_edit_service.probe_source_available(current)
            self.organization_service._ensure_not_processing(snapshot["file_id"])
            self.organization_service._ensure_same_device(current, original.parent)
            self.organization_service._probe_target_parent(original.parent)

    def _restore_original_paths(
        self,
        snapshots: list[dict[str, Any]],
    ) -> list[str]:
        failures: list[str] = []
        for snapshot in reversed(snapshots):
            try:
                target = snapshot["target_path"]
                original = snapshot["original_path"]
                self.organization_service._move_no_overwrite(target, original)
                self.organization_service._verify_moved(
                    source=target,
                    target=original,
                    expected_sha=snapshot["original_sha256"],
                )
            except Exception as exc:
                failures.append(
                    f"{Path(snapshot['original_path']).name}: {type(exc).__name__}: {exc}"
                )
        return failures

    def _reapply_target_paths(
        self,
        snapshots: list[dict[str, Any]],
    ) -> list[str]:
        failures: list[str] = []
        for snapshot in reversed(snapshots):
            try:
                original = snapshot["original_path"]
                target = snapshot["target_path"]
                self.organization_service._move_no_overwrite(original, target)
                self.organization_service._verify_moved(
                    source=original,
                    target=target,
                    expected_sha=snapshot["original_sha256"],
                )
            except Exception as exc:
                failures.append(
                    f"{Path(snapshot['target_path']).name}: {type(exc).__name__}: {exc}"
                )
        return failures

    def _finalize_applied(
        self,
        batch_id: str,
        snapshots: list[dict[str, Any]],
        applied_at: datetime,
    ) -> None:
        scan_errors: list[str] = []
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None or batch.status != "applying":
                raise ValueError("File organization batch is not in applying state")
            proposals = {
                item.id: item
                for item in session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            }
            for snapshot in snapshots:
                proposal = proposals.get(snapshot["proposal_id"])
                if proposal is None or proposal.status != "applying":
                    raise ValueError(
                        "File organization batch member is not in applying state"
                    )
                file = session.get(File, proposal.file_id)
                if file is None:
                    raise ValueError("File organization batch member is missing")
                self.organization_service._update_file_path(
                    file,
                    snapshot["target_path"],
                )
                proposal.status = "applied"
                proposal.applied_at = applied_at
                proposal.applied_sha256 = proposal.original_sha256
                proposal.error_message = None

            batch.status = "applied"
            batch.applied_at = applied_at
            batch.error_message = None

            for root in self._unique_roots(snapshots):
                try:
                    scan_root(
                        session,
                        root,
                        queue_changes=True,
                        metadata_shortcut=False,
                    )
                except Exception as exc:
                    scan_errors.append(f"{root.path}: {type(exc).__name__}: {exc}")
            if scan_errors:
                batch.error_message = (
                    "Batch organization succeeded; some reindex scans failed: "
                    + "; ".join(scan_errors)
                )[:4000]

            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_organization_batch_applied",
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; operation_count={batch.operation_count}; "
                        f"all_or_nothing=true; scan_errors={len(scan_errors)}"
                    ),
                    risk_level=6,
                )
            )

    def _finalize_rolled_back(
        self,
        batch_id: str,
        snapshots: list[dict[str, Any]],
        rolled_back_at: datetime,
    ) -> None:
        scan_errors: list[str] = []
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None or batch.status != "rolling_back":
                raise ValueError("File organization batch is not in rolling_back state")
            proposals = {
                item.id: item
                for item in session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            }
            for snapshot in snapshots:
                proposal = proposals.get(snapshot["proposal_id"])
                if proposal is None or proposal.status != "rolling_back":
                    raise ValueError(
                        "File organization batch member is not in rolling_back state"
                    )
                file = session.get(File, proposal.file_id)
                if file is None:
                    raise ValueError("File organization batch member is missing")
                self.organization_service._update_file_path(
                    file,
                    snapshot["original_path"],
                )
                proposal.status = "rolled_back"
                proposal.rolled_back_at = rolled_back_at
                proposal.error_message = None

            batch.status = "rolled_back"
            batch.rolled_back_at = rolled_back_at
            batch.error_message = None

            for root in self._unique_roots(snapshots):
                try:
                    scan_root(
                        session,
                        root,
                        queue_changes=True,
                        metadata_shortcut=False,
                    )
                except Exception as exc:
                    scan_errors.append(f"{root.path}: {type(exc).__name__}: {exc}")
            if scan_errors:
                batch.error_message = (
                    "Batch path rollback succeeded; some reindex scans failed: "
                    + "; ".join(scan_errors)
                )[:4000]

            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_organization_batch_rolled_back",
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; operation_count={batch.operation_count}; "
                        f"scan_errors={len(scan_errors)}"
                    ),
                    risk_level=6,
                )
            )

    def _finalize_recovered_rollback(
        self,
        batch_id: str,
        recovery_items: list[dict[str, Any]],
        rolled_back_at: datetime,
    ) -> None:
        scan_errors: list[str] = []
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None or batch.status != "rolling_back":
                raise ValueError(
                    "File organization batch is not in rolling_back state"
                )
            proposals = {
                item.id: item
                for item in session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            }
            for item in recovery_items:
                proposal = proposals.get(item["proposal_id"])
                if proposal is None:
                    raise ValueError("File organization batch member is missing")
                file = session.get(File, proposal.file_id)
                if file is None:
                    raise ValueError("File organization file record is missing")
                self.organization_service._update_file_path(
                    file,
                    item["original_path"],
                )
                proposal.status = "rolled_back"
                proposal.rolled_back_at = rolled_back_at
                proposal.error_message = None

            batch.status = "rolled_back"
            batch.rolled_back_at = rolled_back_at
            batch.error_message = None

            roots = self._recovery_roots(recovery_items)
            for root in roots:
                try:
                    scan_root(
                        session,
                        root,
                        queue_changes=True,
                        metadata_shortcut=False,
                    )
                except Exception as exc:
                    scan_errors.append(f"{root.path}: {type(exc).__name__}: {exc}")
            if scan_errors:
                batch.error_message = (
                    "Recovered batch rollback succeeded; some scans failed: "
                    + "; ".join(scan_errors)
                )[:4000]

            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_organization_batch_startup_rollback_completed",
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; operation_count={batch.operation_count}"
                    ),
                    risk_level=6,
                )
            )

    def _reset_apply_to_pending(
        self,
        batch_id: str,
        message: str,
        *,
        audit_action: str = "file_organization_batch_apply_failed_restored",
    ) -> None:
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None:
                return
            batch.status = "pending"
            batch.confirmed_at = None
            batch.applied_at = None
            batch.error_message = message[:4000]
            members = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            for item in members:
                item.status = "pending"
                item.confirmed_at = None
                item.applied_at = None
                item.applied_sha256 = None
                item.error_message = None
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action=audit_action,
                    target=batch.id,
                    result=message[:4000],
                    risk_level=6,
                )
            )

    def _reset_rollback_to_applied(
        self,
        batch_id: str,
        message: str,
    ) -> None:
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None:
                return
            batch.status = "applied"
            batch.error_message = message[:4000]
            members = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            for item in members:
                item.status = "applied"
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_organization_batch_rollback_failed_reapplied",
                    target=batch.id,
                    result=message[:4000],
                    risk_level=6,
                )
            )

    def _mark_recovery_required(self, batch_id: str, message: str) -> None:
        with self.database.session() as session:
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is None:
                return
            batch.status = "recovery_required"
            batch.error_message = message[:4000]
            members = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            for item in members:
                if item.status in {"applying", "rolling_back"}:
                    item.status = "recovery_required"
                    item.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_organization_batch_recovery_required",
                    target=batch.id,
                    result=message[:4000],
                    risk_level=7,
                )
            )

    def _recovery_snapshots(
        self,
        batch: FileOrganizationBatch,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            proposals = list(
                session.scalars(
                    select(FileOrganizationProposal)
                    .where(FileOrganizationProposal.batch_id == batch.id)
                    .order_by(
                        FileOrganizationProposal.created_at,
                        FileOrganizationProposal.id,
                    )
                ).all()
            )
            roots = list(
                session.scalars(
                    select(WorkspaceRoot).where(
                        WorkspaceRoot.workspace_id == batch.workspace_id,
                        WorkspaceRoot.read_allowed.is_(True),
                        WorkspaceRoot.write_allowed.is_(True),
                    )
                ).all()
            )
            for root in roots:
                session.expunge(root)

        if len(proposals) != batch.operation_count:
            raise ValueError("File organization batch membership is incomplete")

        result: list[dict[str, Any]] = []
        for proposal in proposals:
            original = Path(proposal.original_path)
            target = Path(proposal.target_path)
            root = self.organization_service._find_common_root(
                roots,
                original=original,
                target=target,
            )
            if root is None:
                result.append(
                    {
                        "proposal_id": proposal.id,
                        "filename": original.name,
                        "location": "invalid",
                        "original_path": original,
                        "target_path": target,
                        "original_sha256": proposal.original_sha256,
                        "root": None,
                    }
                )
                continue

            original_ok = (
                original.is_file()
                and not original.is_symlink()
                and sha256_file(original) == proposal.original_sha256
            )
            target_ok = (
                target.is_file()
                and not target.is_symlink()
                and sha256_file(target) == proposal.original_sha256
            )
            location = (
                "original"
                if original_ok and not target.exists() and not target.is_symlink()
                else "target"
                if target_ok and not original.exists() and not original.is_symlink()
                else "invalid"
            )
            result.append(
                {
                    "proposal_id": proposal.id,
                    "file_id": proposal.file_id,
                    "filename": original.name,
                    "location": location,
                    "original_path": original.resolve(strict=False),
                    "target_path": target.resolve(strict=False),
                    "original_sha256": proposal.original_sha256,
                    "root": root,
                }
            )
        return result

    def _cleanup_failed_proposal(self, batch_id: str) -> None:
        with self.database.session() as session:
            proposals = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == batch_id
                    )
                ).all()
            )
            for item in proposals:
                session.delete(item)
            batch = session.get(FileOrganizationBatch, batch_id)
            if batch is not None:
                session.delete(batch)

    @staticmethod
    def _unique_roots(snapshots: list[dict[str, Any]]) -> list[Any]:
        unique: dict[str, Any] = {}
        for snapshot in snapshots:
            root = snapshot["root"]
            unique[root.id] = root
        return list(unique.values())

    @staticmethod
    def _recovery_roots(items: list[dict[str, Any]]) -> list[Any]:
        unique: dict[str, Any] = {}
        for item in items:
            root = item.get("root")
            if root is not None:
                unique[root.id] = root
        return list(unique.values())
