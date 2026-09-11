from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.models import AuditLog, File, SourceEditBatch, SourceFileEdit
from app.database.session import Database
from app.indexing.hashing import sha256_file
from app.indexing.scanner import scan_root
from app.source_edits.service import SourceFileEditService

MIN_BATCH_EDITS = 2
MAX_BATCH_EDITS = 10


class SourceFileEditBatchService:
    def __init__(self, database: Database, edit_service: SourceFileEditService) -> None:
        self.database = database
        self.edit_service = edit_service

    def propose(
        self,
        *,
        task_id: str,
        workspace_id: str,
        summary: str,
        edits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(edits, list) or not MIN_BATCH_EDITS <= len(edits) <= MAX_BATCH_EDITS:
            raise ValueError("A transactional batch must contain between 2 and 10 edits")
        file_ids = [str(item.get("file_id") or "").strip() for item in edits if isinstance(item, dict)]
        if len(file_ids) != len(edits) or any(not item for item in file_ids):
            raise ValueError("Every batch edit must provide a file_id")
        if len(set(file_ids)) != len(file_ids):
            raise ValueError("A transactional batch cannot contain the same file more than once")

        batch_id = str(uuid.uuid4())
        normalized_summary = str(summary or "").strip()[:1000] or "Transactional source-file edit batch"
        created_ids: list[str] = []
        try:
            for raw in edits:
                if not isinstance(raw, dict):
                    raise ValueError("Every batch edit must be an object")
                replacements = raw.get("replacements") or []
                cell_edits = raw.get("cell_edits") or []
                if not isinstance(replacements, list) or not isinstance(cell_edits, list):
                    raise ValueError("replacements and cell_edits must be lists")
                payload = self.edit_service.propose(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    file_id=str(raw.get("file_id") or ""),
                    mode=str(raw.get("mode") or ""),
                    summary=str(raw.get("summary") or normalized_summary),
                    replacements=replacements,
                    cell_edits=cell_edits,
                    batch_id=batch_id,
                )
                created_ids.append(payload["id"])

            batch = SourceEditBatch(
                id=batch_id,
                task_id=task_id,
                workspace_id=workspace_id,
                status="pending",
                summary=normalized_summary,
                edit_count=len(created_ids),
            )
            with self.database.session() as session:
                session.add(batch)
            return self.get(batch_id)
        except Exception:
            self._cleanup_failed_proposal(batch_id, created_ids)
            raise

    def confirm(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch_record(batch_id, expected_status="pending")
        snapshots = self._snapshots(batch, expected_edit_status="pending")
        self._preflight_apply(snapshots)
        self._create_backups(snapshots)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            current = session.get(SourceEditBatch, batch_id)
            if current is None or current.status != "pending":
                raise ValueError("Edit batch is no longer pending")
            current.status = "applying"
            current.confirmed_at = now
            current.error_message = None
            members = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in members:
                edit.status = "applying"
                edit.confirmed_at = now
                snap = next(item for item in snapshots if item["edit_id"] == edit.id)
                edit.backup_path = str(snap["backup"])
                edit.error_message = None

        applied: list[dict[str, Any]] = []
        try:
            for snapshot in snapshots:
                self.edit_service._atomic_replace(snapshot["candidate"], snapshot["source"])
                applied.append(snapshot)
                if sha256_file(snapshot["source"]) != snapshot["candidate_sha256"]:
                    raise ValueError(
                        f"Applied hash verification failed for {snapshot['filename']}"
                    )
        except Exception as exc:
            recovery_failures = self._restore_originals(applied)
            if recovery_failures:
                self._mark_recovery_required(
                    batch_id,
                    f"Batch apply failed and automatic rollback was incomplete: {exc}; "
                    + "; ".join(recovery_failures),
                )
                raise ValueError(
                    "Batch apply failed and automatic rollback could not fully restore every file; "
                    "manual recovery is required"
                ) from exc
            self._reset_apply_to_pending(
                batch_id,
                f"Batch apply failed; all already-written files were restored: {type(exc).__name__}: {exc}",
            )
            raise ValueError(
                "Batch apply failed; all already-written files were automatically restored"
            ) from exc

        try:
            self._finalize_applied(batch_id, snapshots, now)
        except Exception as exc:
            recovery_failures = self._restore_originals(applied)
            if recovery_failures:
                self._mark_recovery_required(
                    batch_id,
                    f"Database finalization failed and rollback was incomplete: {exc}; "
                    + "; ".join(recovery_failures),
                )
            else:
                self._reset_apply_to_pending(
                    batch_id,
                    f"Database finalization failed; source files were restored: {type(exc).__name__}: {exc}",
                )
            raise

        return self.get(batch_id)

    def reject(self, batch_id: str) -> dict[str, Any]:
        self._batch_record(batch_id, expected_status="pending")
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None or batch.status != "pending":
                raise ValueError("Only pending edit batches can be rejected")
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            if len(edits) != batch.edit_count:
                raise ValueError("Edit batch membership is incomplete")
            batch.status = "rejected"
            batch.rejected_at = now
            batch.error_message = None
            for edit in edits:
                if edit.status != "pending":
                    raise ValueError("Every batch member must still be pending")
                edit.status = "rejected"
                edit.rejected_at = now
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="source_edit_batch_rejected",
                    target=batch.id,
                    result=f"batch_id={batch.id}; edit_count={batch.edit_count}",
                    risk_level=3,
                )
            )
        return self.get(batch_id)

    def rollback(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch_record(batch_id, expected_status="applied")
        snapshots = self._snapshots(batch, expected_edit_status="applied")
        self._preflight_rollback(snapshots)

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            current = session.get(SourceEditBatch, batch_id)
            if current is None or current.status != "applied":
                raise ValueError("Edit batch is no longer applied")
            current.status = "rolling_back"
            current.error_message = None
            members = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in members:
                edit.status = "rolling_back"

        restored: list[dict[str, Any]] = []
        try:
            for snapshot in snapshots:
                self.edit_service._atomic_replace(snapshot["backup"], snapshot["source"])
                restored.append(snapshot)
                if sha256_file(snapshot["source"]) != snapshot["original_sha256"]:
                    raise ValueError(
                        f"Rollback hash verification failed for {snapshot['filename']}"
                    )
        except Exception as exc:
            reapply_failures = self._reapply_candidates(restored)
            if reapply_failures:
                self._mark_recovery_required(
                    batch_id,
                    f"Batch rollback failed and reapply was incomplete: {exc}; "
                    + "; ".join(reapply_failures),
                )
                raise ValueError(
                    "Batch rollback failed and the previously applied state could not be fully restored; "
                    "manual recovery is required"
                ) from exc
            self._reset_rollback_to_applied(
                batch_id,
                f"Batch rollback failed; the applied state was restored: {type(exc).__name__}: {exc}",
            )
            raise ValueError(
                "Batch rollback failed; DeskAI restored the previously applied batch state"
            ) from exc

        self._finalize_rolled_back(batch_id, snapshots, now)
        return self.get(batch_id)

    def recover_incomplete_batches(self) -> dict[str, int]:
        with self.database.session() as session:
            ids = [
                item.id
                for item in session.scalars(
                    select(SourceEditBatch).where(
                        SourceEditBatch.status.in_(["applying", "rolling_back"])
                    )
                ).all()
            ]
        recovered = 0
        blocked = 0
        for batch_id in ids:
            try:
                batch = self._batch_record(batch_id)
                snapshots = self._snapshots(batch, expected_edit_status=batch.status)
                failures: list[str] = []
                for snapshot in snapshots:
                    backup = snapshot["backup"]
                    if not backup.is_file() or sha256_file(backup) != snapshot["original_sha256"]:
                        failures.append(f"{snapshot['filename']}: backup missing or corrupted")
                        continue
                    current_sha = sha256_file(snapshot["source"])
                    if current_sha == snapshot["original_sha256"]:
                        continue
                    if current_sha != snapshot["candidate_sha256"]:
                        failures.append(
                            f"{snapshot['filename']}: current file no longer matches original or candidate"
                        )
                        continue
                    try:
                        self.edit_service.probe_source_available(snapshot["source"])
                        self.edit_service._atomic_replace(backup, snapshot["source"])
                        if sha256_file(snapshot["source"]) != snapshot["original_sha256"]:
                            failures.append(f"{snapshot['filename']}: recovery hash verification failed")
                    except Exception as exc:
                        failures.append(f"{snapshot['filename']}: {type(exc).__name__}: {exc}")

                if failures:
                    self._mark_recovery_required(
                        batch_id,
                        "Automatic startup recovery could not safely restore the entire batch: "
                        + "; ".join(failures),
                    )
                    blocked += 1
                    continue

                if batch.status == "applying":
                    self._reset_apply_to_pending(
                        batch_id,
                        "DeskAI recovered an interrupted batch apply and restored all source files.",
                        audit_action="source_edit_batch_startup_recovered",
                    )
                else:
                    self._finalize_rolled_back(
                        batch_id,
                        snapshots,
                        datetime.now(timezone.utc),
                        audit_action="source_edit_batch_startup_rollback_completed",
                    )
                recovered += 1
            except Exception as exc:
                self._mark_recovery_required(
                    batch_id,
                    f"Automatic startup recovery failed: {type(exc).__name__}: {exc}",
                )
                blocked += 1
        return {"recovered": recovered, "blocked": blocked}

    def get(self, batch_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None:
                raise ValueError("Edit batch not found")
            edits = list(
                session.scalars(
                    select(SourceFileEdit)
                    .where(SourceFileEdit.batch_id == batch_id)
                    .order_by(SourceFileEdit.created_at, SourceFileEdit.id)
                ).all()
            )
            file_ids = {edit.file_id for edit in edits}
            names = (
                {
                    item.id: item.filename
                    for item in session.scalars(select(File).where(File.id.in_(file_ids))).all()
                }
                if file_ids
                else {}
            )
            edit_payloads = [
                self.edit_service.payload(
                    edit,
                    filename=names.get(edit.file_id, "Unknown file"),
                )
                for edit in edits
            ]
            return self._payload(batch, edit_payloads)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = (
                select(SourceEditBatch)
                .order_by(SourceEditBatch.created_at.desc())
                .limit(max(1, min(int(limit), 200)))
            )
            if workspace_id:
                statement = statement.where(SourceEditBatch.workspace_id == workspace_id)
            if task_id:
                statement = statement.where(SourceEditBatch.task_id == task_id)
            ids = [item.id for item in session.scalars(statement).all()]
        return [self.get(batch_id) for batch_id in ids]

    @staticmethod
    def _payload(batch: SourceEditBatch, edits: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": batch.id,
            "task_id": batch.task_id,
            "workspace_id": batch.workspace_id,
            "status": batch.status,
            "summary": batch.summary,
            "edit_count": batch.edit_count,
            "error_message": batch.error_message,
            "created_at": batch.created_at.isoformat(),
            "confirmed_at": batch.confirmed_at.isoformat() if batch.confirmed_at else None,
            "applied_at": batch.applied_at.isoformat() if batch.applied_at else None,
            "rejected_at": batch.rejected_at.isoformat() if batch.rejected_at else None,
            "rolled_back_at": batch.rolled_back_at.isoformat() if batch.rolled_back_at else None,
            "requires_user_confirmation": batch.status == "pending",
            "can_rollback": batch.status == "applied",
            "recovery_required": batch.status == "recovery_required",
            "transactional": True,
            "edits": edits,
        }

    def _batch_record(
        self,
        batch_id: str,
        *,
        expected_status: str | None = None,
    ) -> SourceEditBatch:
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None:
                raise ValueError("Edit batch not found")
            if expected_status and batch.status != expected_status:
                raise ValueError(f"Edit batch must be {expected_status}")
            session.expunge(batch)
            return batch

    def _snapshots(
        self,
        batch: SourceEditBatch,
        *,
        expected_edit_status: str,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            edits = list(
                session.scalars(
                    select(SourceFileEdit)
                    .where(SourceFileEdit.batch_id == batch.id)
                    .order_by(SourceFileEdit.created_at, SourceFileEdit.id)
                ).all()
            )
            edit_ids = [item.id for item in edits]
        if len(edit_ids) != batch.edit_count or len(edit_ids) < MIN_BATCH_EDITS:
            raise ValueError("Edit batch membership is incomplete")
        snapshots = [
            self.edit_service._action_snapshot(edit_id, expected_status=expected_edit_status)
            for edit_id in edit_ids
        ]
        if any(item["batch_id"] != batch.id for item in snapshots):
            raise ValueError("Edit batch membership is inconsistent")
        source_paths = [str(item["source"]).lower() for item in snapshots]
        if len(set(source_paths)) != len(source_paths):
            raise ValueError("Edit batch contains duplicate source paths")
        return snapshots

    def _preflight_apply(self, snapshots: list[dict[str, Any]]) -> None:
        for snapshot in snapshots:
            source = snapshot["source"]
            if sha256_file(source) != snapshot["original_sha256"]:
                raise ValueError(
                    f"Source file changed after the batch proposal: {snapshot['filename']}"
                )
            candidate = snapshot["candidate"]
            if not candidate.is_file() or sha256_file(candidate) != snapshot["candidate_sha256"]:
                raise ValueError(
                    f"Staged candidate is missing or corrupted: {snapshot['filename']}"
                )
            self.edit_service.probe_source_available(source)

    def _preflight_rollback(self, snapshots: list[dict[str, Any]]) -> None:
        for snapshot in snapshots:
            applied_sha = snapshot["applied_sha256"]
            if not applied_sha or sha256_file(snapshot["source"]) != applied_sha:
                raise ValueError(
                    f"Source file changed after the batch was applied: {snapshot['filename']}"
                )
            if not snapshot["backup"].is_file() or sha256_file(snapshot["backup"]) != snapshot["original_sha256"]:
                raise ValueError(
                    f"Original backup is missing or corrupted: {snapshot['filename']}"
                )
            if not snapshot["candidate"].is_file() or sha256_file(snapshot["candidate"]) != snapshot["candidate_sha256"]:
                raise ValueError(
                    f"Applied candidate copy is missing or corrupted: {snapshot['filename']}"
                )
            self.edit_service.probe_source_available(snapshot["source"])

    @staticmethod
    def _backup_is_valid(snapshot: dict[str, Any]) -> bool:
        backup = snapshot["backup"]
        return backup.is_file() and sha256_file(backup) == snapshot["original_sha256"]

    def _create_backups(self, snapshots: list[dict[str, Any]]) -> None:
        for snapshot in snapshots:
            backup = snapshot["backup"]
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                if not self._backup_is_valid(snapshot):
                    raise ValueError(
                        f"Existing backup is invalid or corrupted: {snapshot['filename']}"
                    )
                continue
            shutil.copy2(snapshot["source"], backup)
            if not self._backup_is_valid(snapshot):
                raise ValueError(f"Backup verification failed: {snapshot['filename']}")

    def _restore_originals(self, snapshots: list[dict[str, Any]]) -> list[str]:
        failures: list[str] = []
        for snapshot in reversed(snapshots):
            try:
                self.edit_service._atomic_replace(snapshot["backup"], snapshot["source"])
                if sha256_file(snapshot["source"]) != snapshot["original_sha256"]:
                    raise ValueError("restored hash mismatch")
            except Exception as exc:
                failures.append(f"{snapshot['filename']}: {type(exc).__name__}: {exc}")
        return failures

    def _reapply_candidates(self, snapshots: list[dict[str, Any]]) -> list[str]:
        failures: list[str] = []
        for snapshot in reversed(snapshots):
            try:
                self.edit_service._atomic_replace(snapshot["candidate"], snapshot["source"])
                if sha256_file(snapshot["source"]) != snapshot["candidate_sha256"]:
                    raise ValueError("reapplied hash mismatch")
            except Exception as exc:
                failures.append(f"{snapshot['filename']}: {type(exc).__name__}: {exc}")
        return failures

    def _finalize_applied(
        self,
        batch_id: str,
        snapshots: list[dict[str, Any]],
        applied_at: datetime,
    ) -> None:
        scan_errors: list[str] = []
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None or batch.status != "applying":
                raise ValueError("Edit batch is not in applying state")
            edits = {
                item.id: item
                for item in session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            }
            for snapshot in snapshots:
                edit = edits.get(snapshot["edit_id"])
                if edit is None or edit.status != "applying":
                    raise ValueError("Edit batch member is not in applying state")
                edit.status = "applied"
                edit.applied_at = applied_at
                edit.applied_sha256 = snapshot["candidate_sha256"]
                edit.error_message = None
            batch.status = "applied"
            batch.applied_at = applied_at
            batch.error_message = None
            for root in self._unique_roots(snapshots):
                try:
                    scan_root(session, root, queue_changes=True, metadata_shortcut=False)
                except Exception as exc:
                    scan_errors.append(f"{root.path}: {type(exc).__name__}: {exc}")
            if scan_errors:
                batch.error_message = (
                    "Batch applied successfully; some reindex scans failed: "
                    + "; ".join(scan_errors)
                )[:4000]
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="source_edit_batch_applied",
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; edit_count={batch.edit_count}; "
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
        *,
        audit_action: str = "source_edit_batch_rolled_back",
    ) -> None:
        scan_errors: list[str] = []
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None or batch.status not in {"rolling_back", "applied"}:
                raise ValueError("Edit batch is not available for rollback finalization")
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in edits:
                edit.status = "rolled_back"
                edit.rolled_back_at = rolled_back_at
                edit.error_message = None
            batch.status = "rolled_back"
            batch.rolled_back_at = rolled_back_at
            batch.error_message = None
            for root in self._unique_roots(snapshots):
                try:
                    scan_root(session, root, queue_changes=True, metadata_shortcut=False)
                except Exception as exc:
                    scan_errors.append(f"{root.path}: {type(exc).__name__}: {exc}")
            if scan_errors:
                batch.error_message = (
                    "Batch rollback succeeded; some reindex scans failed: "
                    + "; ".join(scan_errors)
                )[:4000]
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action=audit_action,
                    target=batch.id,
                    result=f"batch_id={batch.id}; edit_count={batch.edit_count}",
                    risk_level=6,
                )
            )

    def _reset_apply_to_pending(
        self,
        batch_id: str,
        message: str,
        *,
        audit_action: str = "source_edit_batch_apply_failed_restored",
    ) -> None:
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None:
                return
            batch.status = "pending"
            batch.confirmed_at = None
            batch.applied_at = None
            batch.error_message = message[:4000]
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in edits:
                edit.status = "pending"
                edit.confirmed_at = None
                edit.applied_at = None
                edit.applied_sha256 = None
                edit.error_message = None
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action=audit_action,
                    target=batch.id,
                    result=message[:4000],
                    risk_level=6,
                )
            )

    def _reset_rollback_to_applied(self, batch_id: str, message: str) -> None:
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None:
                return
            batch.status = "applied"
            batch.error_message = message[:4000]
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in edits:
                edit.status = "applied"
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="source_edit_batch_rollback_failed_reapplied",
                    target=batch.id,
                    result=message[:4000],
                    risk_level=6,
                )
            )

    def _mark_recovery_required(self, batch_id: str, message: str) -> None:
        with self.database.session() as session:
            batch = session.get(SourceEditBatch, batch_id)
            if batch is None:
                return
            batch.status = "recovery_required"
            batch.error_message = message[:4000]
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in edits:
                if edit.status in {"applying", "rolling_back"}:
                    edit.status = "recovery_required"
                    edit.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="source_edit_batch_recovery_required",
                    target=batch.id,
                    result=message[:4000],
                    risk_level=7,
                )
            )

    def _cleanup_failed_proposal(self, batch_id: str, created_ids: list[str]) -> None:
        with self.database.session() as session:
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(SourceFileEdit.batch_id == batch_id)
                ).all()
            )
            for edit in edits:
                session.delete(edit)
            batch = session.get(SourceEditBatch, batch_id)
            if batch is not None:
                session.delete(batch)
        for edit_id in created_ids:
            candidate_dir = (self.edit_service.proposal_root / edit_id).resolve()
            if candidate_dir.is_relative_to(self.edit_service.proposal_root):
                shutil.rmtree(candidate_dir, ignore_errors=True)

    @staticmethod
    def _unique_roots(snapshots: list[dict[str, Any]]) -> list[Any]:
        unique: dict[str, Any] = {}
        for snapshot in snapshots:
            root = snapshot["root"]
            unique[root.id] = root
        return list(unique.values())
