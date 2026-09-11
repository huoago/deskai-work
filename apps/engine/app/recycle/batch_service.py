from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.models import (
    AuditLog,
    File,
    FileRecycleBatch,
    FileRecycleProposal,
)
from app.database.session import Database
from app.indexing.hashing import sha256_file
from app.indexing.scanner import scan_root
from app.recycle.service import FileRecycleService

MIN_BATCH_ITEMS = 2
MAX_BATCH_ITEMS = 10


class FileRecycleBatchService:
    def __init__(
        self,
        database: Database,
        recycle_service: FileRecycleService,
    ) -> None:
        self.database = database
        self.recycle_service = recycle_service

    def propose(
        self,
        *,
        task_id: str,
        workspace_id: str,
        summary: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(items, list) or not (
            MIN_BATCH_ITEMS <= len(items) <= MAX_BATCH_ITEMS
        ):
            raise ValueError("A recycle batch must contain between 2 and 10 files")

        file_ids = [
            str(item.get("file_id") or "").strip()
            for item in items
            if isinstance(item, dict)
        ]
        if len(file_ids) != len(items) or any(not value for value in file_ids):
            raise ValueError("Every recycle batch item must provide a file_id")
        if len(set(file_ids)) != len(file_ids):
            raise ValueError("A recycle batch cannot contain the same file more than once")

        batch_id = str(uuid.uuid4())
        normalized_summary = (
            str(summary or "").strip()[:1000]
            or "Transactional recoverable recycle batch"
        )
        try:
            for raw in items:
                if not isinstance(raw, dict):
                    raise ValueError("Every recycle batch item must be an object")
                self.recycle_service.propose(
                    task_id=task_id,
                    workspace_id=workspace_id,
                    file_id=str(raw.get("file_id") or ""),
                    summary=str(raw.get("summary") or normalized_summary),
                    batch_id=batch_id,
                )

            proposals = self._proposal_records(batch_id)
            original_paths = [item.original_path.casefold() for item in proposals]
            if len(set(original_paths)) != len(original_paths):
                raise ValueError("A recycle batch cannot contain duplicate source paths")

            with self.database.session() as session:
                session.add(
                    FileRecycleBatch(
                        id=batch_id,
                        task_id=task_id,
                        workspace_id=workspace_id,
                        status="pending",
                        summary=normalized_summary,
                        item_count=len(proposals),
                    )
                )
            return self.get(batch_id)
        except Exception:
            self._cleanup_failed_proposal(batch_id)
            raise

    def confirm(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch_record(batch_id, expected_status="pending")
        snapshots = self._recycle_snapshots(batch)
        self._preflight_recycle(snapshots)

        now = datetime.now(timezone.utc)
        self._set_recycling(batch_id, now)

        removed: list[dict[str, Any]] = []
        try:
            # Critical Phase 16 invariant: every quarantine copy is created and
            # verified before the first Workspace source is removed.
            for snapshot in snapshots:
                self.recycle_service._ensure_verified_quarantine_copy(
                    snapshot["source"],
                    snapshot["quarantine"],
                    snapshot["original_sha256"],
                )
                self.recycle_service._verify_quarantine(
                    snapshot["quarantine"],
                    snapshot["original_sha256"],
                )

            # Re-check every source after all quarantine copies have been staged.
            for snapshot in snapshots:
                self.recycle_service._ensure_not_processing(snapshot["file_id"])
                self.recycle_service._ensure_source_available(snapshot["source"])
                if sha256_file(snapshot["source"]) != snapshot["original_sha256"]:
                    raise ValueError(
                        f"{snapshot['source'].name} changed while quarantine copies "
                        "were being prepared"
                    )

            for snapshot in snapshots:
                self._remove_source(snapshot)
                removed.append(snapshot)
                self.recycle_service._verify_quarantine(
                    snapshot["quarantine"],
                    snapshot["original_sha256"],
                )
        except Exception as exc:
            if removed:
                failures = self._restore_removed_sources(removed)
                if failures:
                    self._mark_recovery_required(
                        batch_id,
                        (
                            "Batch recycle failed and automatic source restoration was "
                            f"incomplete: {type(exc).__name__}: {exc}; "
                            + "; ".join(failures)
                        ),
                    )
                    raise ValueError(
                        "Batch recycle failed and DeskAI could not restore every "
                        "already-removed file; manual recovery is required"
                    ) from exc
            self._reset_recycling_to_pending(
                batch_id,
                (
                    "Batch recycle failed before commit; Workspace originals are intact: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            raise ValueError(
                "Batch recycle failed; all Workspace originals remain or were restored"
            ) from exc

        try:
            self._finalize_recycled(batch_id, snapshots, now)
        except Exception as exc:
            failures = self._restore_removed_sources(removed)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch recycle database finalization failed and Workspace "
                        f"restoration was incomplete: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
            else:
                self._reset_recycling_to_pending(
                    batch_id,
                    (
                        "Batch recycle database finalization failed; every Workspace "
                        f"original was restored: {type(exc).__name__}: {exc}"
                    ),
                )
            raise

        return self.get(batch_id)

    def reject(self, batch_id: str) -> dict[str, Any]:
        self._batch_record(batch_id, expected_status="pending")
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None or batch.status != "pending":
                raise ValueError("Recycle batch is no longer pending")
            members = list(
                session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == batch_id
                    )
                ).all()
            )
            if len(members) != batch.item_count:
                raise ValueError("Recycle batch membership is incomplete")
            if any(item.status != "pending" for item in members):
                raise ValueError("Every recycle batch member must still be pending")

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
                    action="file_recycle_batch_rejected",
                    target=batch.id,
                    result=f"batch_id={batch.id}; item_count={batch.item_count}",
                    risk_level=5,
                )
            )
        return self.get(batch_id)

    def restore(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch_record(batch_id, expected_status="recycled")
        snapshots = self._restore_snapshots(batch)
        self._preflight_restore(snapshots)

        now = datetime.now(timezone.utc)
        self._set_restoring(batch_id)

        restored: list[dict[str, Any]] = []
        try:
            for snapshot in snapshots:
                self.recycle_service._restore_copy_no_overwrite(
                    snapshot["quarantine"],
                    snapshot["original"],
                    snapshot["original_sha256"],
                )
                restored.append(snapshot)
        except Exception as exc:
            failures = self._remove_restored_originals(restored)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch restore failed and DeskAI could not return every restored "
                        f"member to recycled state: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
                raise ValueError(
                    "Batch restore failed and automatic rollback to recycled state was "
                    "incomplete; manual recovery is required"
                ) from exc
            self._reset_restoring_to_recycled(
                batch_id,
                (
                    "Batch restore failed; restored members were removed again and all "
                    f"quarantine copies remain intact: {type(exc).__name__}: {exc}"
                ),
            )
            raise ValueError(
                "Batch restore failed; the complete batch remains safely recycled"
            ) from exc

        try:
            self._finalize_restored(batch_id, snapshots, now)
        except Exception as exc:
            failures = self._remove_restored_originals(restored)
            if failures:
                self._mark_recovery_required(
                    batch_id,
                    (
                        "Batch restore database finalization failed and rollback to "
                        f"recycled state was incomplete: {type(exc).__name__}: {exc}; "
                        + "; ".join(failures)
                    ),
                )
            else:
                self._reset_restoring_to_recycled(
                    batch_id,
                    (
                        "Batch restore database finalization failed; original-path copies "
                        f"were removed and quarantine remains intact: {type(exc).__name__}: {exc}"
                    ),
                )
            raise

        return self.get(batch_id)

    def recover_incomplete_batches(self) -> dict[str, int]:
        with self.database.session() as session:
            ids = [
                item.id
                for item in session.scalars(
                    select(FileRecycleBatch).where(
                        FileRecycleBatch.status.in_(["recycling", "restoring"])
                    )
                ).all()
            ]

        recovered = 0
        blocked = 0
        for batch_id in ids:
            try:
                batch = self._batch_record(batch_id)
                states = self._recovery_states(batch)
                invalid = [item for item in states if item["state"] == "invalid"]
                if invalid:
                    self._mark_recovery_required(
                        batch_id,
                        "Startup recovery found ambiguous recycle batch members: "
                        + "; ".join(item["filename"] for item in invalid),
                    )
                    blocked += 1
                    continue

                if batch.status == "recycling":
                    failures: list[str] = []
                    for item in states:
                        if item["state"] == "original":
                            continue
                        try:
                            self.recycle_service._restore_copy_no_overwrite(
                                item["quarantine"],
                                item["original"],
                                item["original_sha256"],
                            )
                        except Exception as exc:
                            failures.append(
                                f"{item['filename']}: {type(exc).__name__}: {exc}"
                            )
                    if failures:
                        self._mark_recovery_required(
                            batch_id,
                            "Startup recycle recovery could not restore every original: "
                            + "; ".join(failures),
                        )
                        blocked += 1
                        continue
                    self._reset_recycling_to_pending(
                        batch_id,
                        (
                            "DeskAI recovered an interrupted recycle batch and restored "
                            "every Workspace original."
                        ),
                        audit_action="file_recycle_batch_startup_recovered_pending",
                    )
                    recovered += 1
                    continue

                failures = []
                for item in states:
                    if item["state"] == "original":
                        continue
                    try:
                        self.recycle_service._restore_copy_no_overwrite(
                            item["quarantine"],
                            item["original"],
                            item["original_sha256"],
                        )
                    except Exception as exc:
                        failures.append(
                            f"{item['filename']}: {type(exc).__name__}: {exc}"
                        )
                if failures:
                    self._mark_recovery_required(
                        batch_id,
                        "Startup batch restore could not recreate every original: "
                        + "; ".join(failures),
                    )
                    blocked += 1
                    continue

                snapshots = self._recovery_restore_snapshots(batch)
                self._finalize_restored(
                    batch_id,
                    snapshots,
                    datetime.now(timezone.utc),
                    audit_action="file_recycle_batch_startup_restore_completed",
                )
                recovered += 1
            except Exception as exc:
                self._mark_recovery_required(
                    batch_id,
                    f"Automatic recycle batch recovery failed: {type(exc).__name__}: {exc}",
                )
                blocked += 1

        return {"recovered": recovered, "blocked": blocked}

    def get(self, batch_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None:
                raise ValueError("Recycle batch not found")
            proposals = list(
                session.scalars(
                    select(FileRecycleProposal)
                    .where(FileRecycleProposal.batch_id == batch_id)
                    .order_by(
                        FileRecycleProposal.created_at,
                        FileRecycleProposal.id,
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
                self.recycle_service.payload(
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
                select(FileRecycleBatch)
                .order_by(FileRecycleBatch.created_at.desc())
                .limit(max(1, min(int(limit), 200)))
            )
            if workspace_id:
                statement = statement.where(
                    FileRecycleBatch.workspace_id == workspace_id
                )
            if task_id:
                statement = statement.where(FileRecycleBatch.task_id == task_id)
            ids = [item.id for item in session.scalars(statement).all()]
        return [self.get(batch_id) for batch_id in ids]

    @staticmethod
    def _payload(
        batch: FileRecycleBatch,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": batch.id,
            "task_id": batch.task_id,
            "workspace_id": batch.workspace_id,
            "status": batch.status,
            "summary": batch.summary,
            "item_count": batch.item_count,
            "error_message": batch.error_message,
            "created_at": batch.created_at.isoformat(),
            "confirmed_at": (
                batch.confirmed_at.isoformat() if batch.confirmed_at else None
            ),
            "recycled_at": (
                batch.recycled_at.isoformat() if batch.recycled_at else None
            ),
            "rejected_at": (
                batch.rejected_at.isoformat() if batch.rejected_at else None
            ),
            "restored_at": (
                batch.restored_at.isoformat() if batch.restored_at else None
            ),
            "requires_user_confirmation": batch.status == "pending",
            "can_restore": batch.status == "recycled",
            "recovery_required": batch.status == "recovery_required",
            "permanent_delete_available": False,
            "transactional": True,
            "items": items,
        }

    def _batch_record(
        self,
        batch_id: str,
        *,
        expected_status: str | None = None,
    ) -> FileRecycleBatch:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None:
                raise ValueError("Recycle batch not found")
            if expected_status and batch.status != expected_status:
                raise ValueError(f"Recycle batch must be {expected_status}")
            session.expunge(batch)
            return batch

    def _proposal_records(self, batch_id: str) -> list[FileRecycleProposal]:
        with self.database.session() as session:
            records = list(
                session.scalars(
                    select(FileRecycleProposal)
                    .where(FileRecycleProposal.batch_id == batch_id)
                    .order_by(
                        FileRecycleProposal.created_at,
                        FileRecycleProposal.id,
                    )
                ).all()
            )
            for item in records:
                session.expunge(item)
            return records

    def _recycle_snapshots(
        self,
        batch: FileRecycleBatch,
    ) -> list[dict[str, Any]]:
        proposals = self._proposal_records(batch.id)
        if len(proposals) != batch.item_count or len(proposals) < MIN_BATCH_ITEMS:
            raise ValueError("Recycle batch membership is incomplete")
        snapshots = [
            self.recycle_service._snapshot_for_recycle(item.id)
            for item in proposals
        ]
        if any(item["batch_id"] != batch.id for item in snapshots):
            raise ValueError("Recycle batch membership is inconsistent")
        return snapshots

    def _restore_snapshots(
        self,
        batch: FileRecycleBatch,
    ) -> list[dict[str, Any]]:
        proposals = self._proposal_records(batch.id)
        if len(proposals) != batch.item_count or len(proposals) < MIN_BATCH_ITEMS:
            raise ValueError("Recycle batch membership is incomplete")
        snapshots = [
            self.recycle_service._snapshot_for_restore(item.id)
            for item in proposals
        ]
        if any(item["batch_id"] != batch.id for item in snapshots):
            raise ValueError("Recycle batch membership is inconsistent")
        return snapshots

    def _preflight_recycle(self, snapshots: list[dict[str, Any]]) -> None:
        originals = [str(item["source"]).casefold() for item in snapshots]
        if len(set(originals)) != len(originals):
            raise ValueError("Recycle batch contains duplicate source paths")
        for snapshot in snapshots:
            self.recycle_service._ensure_not_processing(snapshot["file_id"])
            self.recycle_service._ensure_source_available(snapshot["source"])
            if sha256_file(snapshot["source"]) != snapshot["original_sha256"]:
                raise ValueError(
                    f"{snapshot['source'].name} changed after the recycle proposal"
                )

    def _preflight_restore(self, snapshots: list[dict[str, Any]]) -> None:
        originals = [str(item["original"]).casefold() for item in snapshots]
        if len(set(originals)) != len(originals):
            raise ValueError("Recycle batch contains duplicate original paths")
        for snapshot in snapshots:
            self.recycle_service._verify_quarantine(
                snapshot["quarantine"],
                snapshot["original_sha256"],
            )
            original = snapshot["original"]
            if original.exists() or original.is_symlink():
                raise ValueError(
                    f"Original path is occupied for {original.name}; batch restore is blocked"
                )
            if not original.parent.is_dir():
                raise ValueError(
                    f"Original parent directory no longer exists for {original.name}"
                )
            self.recycle_service._probe_target_parent(original.parent)

    def _set_recycling(self, batch_id: str, now: datetime) -> None:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None or batch.status != "pending":
                raise ValueError("Recycle batch is no longer pending")
            members = list(
                session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == batch_id
                    )
                ).all()
            )
            if len(members) != batch.item_count or any(
                item.status != "pending" for item in members
            ):
                raise ValueError("Recycle batch membership is not fully pending")
            batch.status = "recycling"
            batch.confirmed_at = now
            batch.error_message = None
            for item in members:
                item.status = "recycling"
                item.confirmed_at = now
                item.error_message = None

    def _set_restoring(self, batch_id: str) -> None:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None or batch.status != "recycled":
                raise ValueError("Recycle batch is no longer restorable")
            members = list(
                session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == batch_id
                    )
                ).all()
            )
            if len(members) != batch.item_count or any(
                item.status != "recycled" for item in members
            ):
                raise ValueError("Recycle batch membership is not fully recycled")
            batch.status = "restoring"
            batch.error_message = None
            for item in members:
                item.status = "restoring"
                item.error_message = None

    def _remove_source(self, snapshot: dict[str, Any]) -> None:
        source = snapshot["source"]
        source.unlink()
        if source.exists() or source.is_symlink():
            raise ValueError(f"Source path still exists after recycle: {source.name}")

    def _restore_removed_sources(
        self,
        snapshots: list[dict[str, Any]],
    ) -> list[str]:
        failures: list[str] = []
        for snapshot in reversed(snapshots):
            try:
                original = snapshot["source"]
                if original.exists() or original.is_symlink():
                    if (
                        original.is_file()
                        and not original.is_symlink()
                        and sha256_file(original) == snapshot["original_sha256"]
                    ):
                        continue
                    raise ValueError("Original path is occupied by unexpected content")
                self.recycle_service._restore_copy_no_overwrite(
                    snapshot["quarantine"],
                    original,
                    snapshot["original_sha256"],
                )
            except Exception as exc:
                failures.append(
                    f"{snapshot['source'].name}: {type(exc).__name__}: {exc}"
                )
        return failures

    def _remove_restored_originals(
        self,
        snapshots: list[dict[str, Any]],
    ) -> list[str]:
        failures: list[str] = []
        for snapshot in reversed(snapshots):
            original = snapshot["original"]
            try:
                if not original.exists() and not original.is_symlink():
                    continue
                if (
                    not original.is_file()
                    or original.is_symlink()
                    or sha256_file(original) != snapshot["original_sha256"]
                ):
                    raise ValueError(
                        "Restored original changed before rollback to recycled state"
                    )
                original.unlink()
                if original.exists() or original.is_symlink():
                    raise ValueError("Restored original still exists after rollback")
                self.recycle_service._verify_quarantine(
                    snapshot["quarantine"],
                    snapshot["original_sha256"],
                )
            except Exception as exc:
                failures.append(
                    f"{original.name}: {type(exc).__name__}: {exc}"
                )
        return failures

    def _finalize_recycled(
        self,
        batch_id: str,
        snapshots: list[dict[str, Any]],
        recycled_at: datetime,
    ) -> None:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None or batch.status != "recycling":
                raise ValueError("Recycle batch is not in recycling state")
            proposals = {
                item.id: item
                for item in session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == batch_id
                    )
                ).all()
            }
            for snapshot in snapshots:
                proposal = proposals.get(snapshot["proposal_id"])
                if proposal is None or proposal.status != "recycling":
                    raise ValueError("Recycle batch member is not in recycling state")
                file = session.get(File, proposal.file_id)
                if file is None:
                    raise ValueError("Recycle batch File record is missing")
                if Path(file.path).resolve(strict=False) != snapshot["original"]:
                    raise ValueError("File record path changed during batch recycle")
                file.status = "recycled"
                proposal.status = "recycled"
                proposal.recycled_at = recycled_at
                proposal.error_message = None
                self.recycle_service._cancel_active_jobs(
                    session,
                    file.id,
                    "File was intentionally moved to the DeskAI recycle bin",
                )

            batch.status = "recycled"
            batch.recycled_at = recycled_at
            batch.error_message = None
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_recycle_batch_recycled",
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; item_count={batch.item_count}; "
                        "all_or_nothing=true; permanent_delete=false"
                    ),
                    risk_level=7,
                )
            )

    def _finalize_restored(
        self,
        batch_id: str,
        snapshots: list[dict[str, Any]],
        restored_at: datetime,
        *,
        audit_action: str = "file_recycle_batch_restored",
    ) -> None:
        for snapshot in snapshots:
            original = snapshot["original"]
            if (
                not original.is_file()
                or original.is_symlink()
                or sha256_file(original) != snapshot["original_sha256"]
            ):
                raise ValueError(f"Restored file failed verification: {original.name}")

        scan_errors: list[str] = []
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None or batch.status != "restoring":
                raise ValueError("Recycle batch is not in restoring state")
            proposals = {
                item.id: item
                for item in session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == batch_id
                    )
                ).all()
            }
            for snapshot in snapshots:
                proposal = proposals.get(snapshot["proposal_id"])
                if proposal is None or proposal.status != "restoring":
                    raise ValueError("Recycle batch member is not in restoring state")
                file = session.get(File, proposal.file_id)
                if file is None:
                    raise ValueError("Recycle batch File record is missing")
                original = snapshot["original"]
                stat = original.stat()
                file.path = str(original.resolve(strict=True))
                file.filename = original.name
                file.size = stat.st_size
                file.sha256 = proposal.original_sha256
                file.modified_at = datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                )
                file.status = proposal.previous_file_status
                proposal.status = "restored"
                proposal.restored_at = restored_at
                proposal.error_message = None

            batch.status = "restored"
            batch.restored_at = restored_at
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
                    "Batch restore succeeded; some Workspace rescans failed: "
                    + "; ".join(scan_errors)
                )[:4000]

            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action=audit_action,
                    target=batch.id,
                    result=(
                        f"batch_id={batch.id}; item_count={batch.item_count}; "
                        f"quarantine_retained=true; scan_errors={len(scan_errors)}"
                    ),
                    risk_level=7,
                )
            )

    def _reset_recycling_to_pending(
        self,
        batch_id: str,
        message: str,
        *,
        audit_action: str = "file_recycle_batch_failed_restored",
    ) -> None:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None:
                return
            batch.status = "pending"
            batch.confirmed_at = None
            batch.recycled_at = None
            batch.error_message = message[:4000]
            for item in session.scalars(
                select(FileRecycleProposal).where(
                    FileRecycleProposal.batch_id == batch_id
                )
            ).all():
                item.status = "pending"
                item.confirmed_at = None
                item.recycled_at = None
                item.error_message = None
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action=audit_action,
                    target=batch.id,
                    result=message[:4000],
                    risk_level=7,
                )
            )

    def _reset_restoring_to_recycled(
        self,
        batch_id: str,
        message: str,
    ) -> None:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None:
                return
            batch.status = "recycled"
            batch.error_message = message[:4000]
            for item in session.scalars(
                select(FileRecycleProposal).where(
                    FileRecycleProposal.batch_id == batch_id
                )
            ).all():
                item.status = "recycled"
                file = session.get(File, item.file_id)
                if file is not None:
                    file.status = "recycled"
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_recycle_batch_restore_failed_recycled",
                    target=batch.id,
                    result=message[:4000],
                    risk_level=7,
                )
            )

    def _mark_recovery_required(self, batch_id: str, message: str) -> None:
        with self.database.session() as session:
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is None:
                return
            batch.status = "recovery_required"
            batch.error_message = message[:4000]
            for item in session.scalars(
                select(FileRecycleProposal).where(
                    FileRecycleProposal.batch_id == batch_id
                )
            ).all():
                if item.status in {"recycling", "restoring"}:
                    item.status = "recovery_required"
                    item.error_message = message[:4000]
            session.add(
                AuditLog(
                    task_id=batch.task_id,
                    action="file_recycle_batch_recovery_required",
                    target=batch.id,
                    result=message[:4000],
                    risk_level=8,
                )
            )

    def _recovery_states(
        self,
        batch: FileRecycleBatch,
    ) -> list[dict[str, Any]]:
        proposals = self._proposal_records(batch.id)
        if len(proposals) != batch.item_count:
            raise ValueError("Recycle batch membership is incomplete")

        states: list[dict[str, Any]] = []
        for proposal in proposals:
            original = Path(proposal.original_path).resolve(strict=False)
            quarantine = self.recycle_service._assert_quarantine_path(
                Path(proposal.quarantine_path)
            )
            original_ok = (
                original.is_file()
                and not original.is_symlink()
                and sha256_file(original) == proposal.original_sha256
            )
            quarantine_exists = quarantine.exists() or quarantine.is_symlink()
            quarantine_ok = (
                quarantine.is_file()
                and not quarantine.is_symlink()
                and sha256_file(quarantine) == proposal.original_sha256
            )

            if original_ok and (not quarantine_exists or quarantine_ok):
                state = "original"
            elif (
                not original.exists()
                and not original.is_symlink()
                and quarantine_ok
            ):
                state = "recycled"
            else:
                state = "invalid"

            states.append(
                {
                    "proposal_id": proposal.id,
                    "file_id": proposal.file_id,
                    "filename": original.name,
                    "original": original,
                    "quarantine": quarantine,
                    "original_sha256": proposal.original_sha256,
                    "previous_file_status": proposal.previous_file_status,
                    "state": state,
                }
            )
        return states

    def _recovery_restore_snapshots(
        self,
        batch: FileRecycleBatch,
    ) -> list[dict[str, Any]]:
        proposals = self._proposal_records(batch.id)
        snapshots: list[dict[str, Any]] = []
        for proposal in proposals:
            original = Path(proposal.original_path).resolve(strict=False)
            with self.database.session() as session:
                root = self.recycle_service._writable_root_for_missing_path(
                    session,
                    proposal.workspace_id,
                    original,
                )
                if root is None:
                    raise ValueError(
                        f"Workspace write permission unavailable for {original.name}"
                    )
                session.expunge(root)
            snapshots.append(
                {
                    "proposal_id": proposal.id,
                    "file_id": proposal.file_id,
                    "batch_id": batch.id,
                    "original": original,
                    "quarantine": self.recycle_service._require_quarantine_path(
                        proposal.quarantine_path
                    ),
                    "original_sha256": proposal.original_sha256,
                    "previous_file_status": proposal.previous_file_status,
                    "root": root,
                }
            )
        return snapshots

    def _cleanup_failed_proposal(self, batch_id: str) -> None:
        with self.database.session() as session:
            proposals = list(
                session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == batch_id
                    )
                ).all()
            )
            quarantine_dirs: list[Path] = []
            for item in proposals:
                quarantine_dirs.append(Path(item.quarantine_path).parent)
                session.delete(item)
            batch = session.get(FileRecycleBatch, batch_id)
            if batch is not None:
                session.delete(batch)
        for directory in quarantine_dirs:
            try:
                resolved = self.recycle_service._assert_quarantine_path(directory)
                if resolved.exists() and resolved.is_dir() and not resolved.is_symlink():
                    shutil.rmtree(resolved, ignore_errors=True)
            except ValueError:
                continue

    @staticmethod
    def _unique_roots(snapshots: list[dict[str, Any]]) -> list[Any]:
        unique: dict[str, Any] = {}
        for snapshot in snapshots:
            root = snapshot["root"]
            unique[root.id] = root
        return list(unique.values())
