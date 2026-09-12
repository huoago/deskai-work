from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database.models import (
    AuditLog,
    File,
    FileOrganizationBatch,
    FileOrganizationProposal,
    FileRecycleBatch,
    FileRecycleProposal,
    RecoveryReconciliationProposal,
    SourceEditBatch,
    SourceFileEdit,
)
from app.database.session import Database
from app.recovery.snapshot import RecoverySnapshotService


class RecoveryReconciliationService:
    """Safely reconciles frozen transaction metadata after a verified snapshot."""

    def __init__(
        self,
        database: Database,
        snapshot_service: RecoverySnapshotService,
    ) -> None:
        self.database = database
        self.snapshot_service = snapshot_service

    def propose(
        self,
        entity_type: str,
        transaction_id: str,
    ) -> dict[str, Any]:
        snapshot = self.snapshot_service.capture(entity_type, transaction_id)
        target_status, historical_action = self._eligible_target(snapshot)
        fingerprint = self.fingerprint(snapshot)

        with self.database.session() as session:
            existing = session.scalar(
                select(RecoveryReconciliationProposal)
                .where(
                    RecoveryReconciliationProposal.entity_type == entity_type,
                    RecoveryReconciliationProposal.transaction_id == transaction_id,
                    RecoveryReconciliationProposal.status == "pending",
                )
                .order_by(RecoveryReconciliationProposal.created_at.desc())
            )
            if existing is not None:
                if (
                    existing.snapshot_fingerprint == fingerprint
                    and existing.target_status == target_status
                    and existing.historical_action == historical_action
                ):
                    return self.payload(existing)
                existing.status = "stale"
                existing.stale_at = datetime.now(timezone.utc)
                existing.error_message = (
                    "A newer recovery snapshot superseded this proposal."
                )

            proposal = RecoveryReconciliationProposal(
                task_id=snapshot.get("task_id"),
                workspace_id=snapshot["workspace_id"],
                entity_type=entity_type,
                transaction_id=transaction_id,
                status="pending",
                snapshot_fingerprint=fingerprint,
                snapshot_state=snapshot["assessment"]["state"],
                target_status=target_status,
                historical_action=historical_action,
            )
            session.add(proposal)
            session.flush()
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="recovery_reconciliation_proposed",
                    target=proposal.id,
                    result=(
                        f"entity_type={entity_type}; transaction_id={transaction_id}; "
                        f"snapshot_state={proposal.snapshot_state}; "
                        f"target_status={target_status}; "
                        f"fingerprint={fingerprint}"
                    ),
                    risk_level=8,
                )
            )
            session.expunge(proposal)
            return self.payload(proposal)

    def confirm(self, proposal_id: str) -> dict[str, Any]:
        proposal = self._record(proposal_id, expected_status="pending")
        try:
            snapshot = self.snapshot_service.capture(
                proposal.entity_type,
                proposal.transaction_id,
            )
            target_status, historical_action = self._eligible_target(snapshot)
        except ValueError as exc:
            self._mark_stale(
                proposal_id,
                f"Recovery reconciliation precondition changed: {exc}",
            )
            raise ValueError(
                "Recovery state changed after the reconciliation proposal; "
                "create a new proposal"
            ) from exc

        fingerprint = self.fingerprint(snapshot)
        if (
            fingerprint != proposal.snapshot_fingerprint
            or snapshot["assessment"]["state"] != proposal.snapshot_state
            or target_status != proposal.target_status
            or historical_action != proposal.historical_action
        ):
            self._mark_stale(
                proposal_id,
                "Recovery snapshot changed after the reconciliation proposal.",
            )
            raise ValueError(
                "Recovery snapshot changed after the reconciliation proposal; "
                "create a new proposal"
            )

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            current = session.get(RecoveryReconciliationProposal, proposal_id)
            if current is None or current.status != "pending":
                raise ValueError("Recovery reconciliation proposal is no longer pending")

            self._apply_metadata_reconciliation(
                session,
                snapshot,
                target_status=current.target_status,
            )

            current.status = "confirmed"
            current.confirmed_at = now
            current.error_message = None
            session.add(
                AuditLog(
                    task_id=current.task_id,
                    action="recovery_reconciliation_confirmed",
                    target=current.id,
                    result=(
                        f"entity_type={current.entity_type}; "
                        f"transaction_id={current.transaction_id}; "
                        f"snapshot_state={current.snapshot_state}; "
                        f"target_status={current.target_status}; "
                        f"historical_action={current.historical_action}; "
                        f"fingerprint={current.snapshot_fingerprint}; "
                        "filesystem_mutation=false"
                    ),
                    risk_level=8,
                )
            )

        return self.get(proposal_id)

    def reject(self, proposal_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            proposal = session.get(RecoveryReconciliationProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recovery reconciliation proposal not found")
            if proposal.status != "pending":
                raise ValueError("Only pending reconciliation proposals can be rejected")
            proposal.status = "rejected"
            proposal.rejected_at = now
            proposal.error_message = None
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="recovery_reconciliation_rejected",
                    target=proposal.id,
                    result=(
                        f"entity_type={proposal.entity_type}; "
                        f"transaction_id={proposal.transaction_id}; "
                        "filesystem_mutation=false"
                    ),
                    risk_level=4,
                )
            )
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> dict[str, Any]:
        return self.payload(self._record(proposal_id))

    def list(
        self,
        *,
        workspace_id: str | None = None,
        transaction_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = (
                select(RecoveryReconciliationProposal)
                .order_by(RecoveryReconciliationProposal.created_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
            if workspace_id:
                statement = statement.where(
                    RecoveryReconciliationProposal.workspace_id == workspace_id
                )
            if transaction_id:
                statement = statement.where(
                    RecoveryReconciliationProposal.transaction_id == transaction_id
                )
            records = list(session.scalars(statement).all())
            return [self.payload(item) for item in records]

    @staticmethod
    def fingerprint(snapshot: dict[str, Any]) -> str:
        members = []
        for member in sorted(snapshot.get("members") or [], key=lambda item: item["id"]):
            observations = []
            for observation in sorted(
                member.get("observations") or [],
                key=lambda item: (str(item.get("role")), str(item.get("path"))),
            ):
                observations.append(
                    {
                        "role": observation.get("role"),
                        "path": observation.get("path"),
                        "exists": bool(observation.get("exists")),
                        "is_file": bool(observation.get("is_file")),
                        "is_symlink": bool(observation.get("is_symlink")),
                        "size": observation.get("size"),
                        "sha256": observation.get("sha256"),
                        "matches": sorted(observation.get("matches") or []),
                        "error": observation.get("error"),
                    }
                )
            members.append(
                {
                    "id": member["id"],
                    "tracked_path": member.get("tracked_path"),
                    "tracked_file_status": member.get("tracked_file_status"),
                    "state": member.get("state"),
                    "supporting_evidence_valid": bool(
                        member.get("supporting_evidence_valid")
                    ),
                    "technical_action_ready": bool(
                        member.get("technical_action_ready")
                    ),
                    "observations": observations,
                }
            )

        assessment = snapshot.get("assessment") or {}
        evidence = {
            "entity_type": snapshot.get("entity_type"),
            "transaction_id": snapshot.get("transaction_id"),
            "workspace_id": snapshot.get("workspace_id"),
            "persisted_status": snapshot.get("persisted_status"),
            "transactional": bool(snapshot.get("transactional")),
            "members": members,
            "assessment": {
                "state": assessment.get("state"),
                "member_states": assessment.get("member_states") or [],
                "safe_state_detected": bool(assessment.get("safe_state_detected")),
                "technical_action_preconditions_satisfied": bool(
                    assessment.get("technical_action_preconditions_satisfied")
                ),
                "action_after_reconciliation": assessment.get(
                    "action_after_reconciliation"
                ),
            },
        }
        encoded = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _eligible_target(snapshot: dict[str, Any]) -> tuple[str, str]:
        assessment = snapshot["assessment"]
        if not assessment.get("safe_state_detected"):
            raise ValueError(
                "Recovery reconciliation requires one consistent verified disk state"
            )

        entity_type = snapshot["entity_type"]
        state = assessment.get("state")
        action = assessment.get("action_after_reconciliation")

        target: tuple[str, str] | None = None
        if (
            entity_type
            in {
                "source_edit",
                "source_edit_batch",
                "file_organization",
                "file_organization_batch",
            }
            and state == "consistent_applied"
            and action == "rollback"
        ):
            target = ("applied", "rollback")
        elif (
            entity_type in {"file_recycle", "file_recycle_batch"}
            and state == "consistent_recycled"
            and action == "restore"
        ):
            target = ("recycled", "restore")

        if target is None:
            raise ValueError(
                "Phase 20 only reconciles verified applied or recycled states"
            )

        if not assessment.get("technical_action_preconditions_satisfied"):
            raise ValueError(
                "Recovery reconciliation requires all historical action artifacts"
            )

        return target

    def _apply_metadata_reconciliation(
        self,
        session,
        snapshot: dict[str, Any],
        *,
        target_status: str,
    ) -> None:
        entity_type = snapshot["entity_type"]
        transaction_id = snapshot["transaction_id"]
        members = {item["id"]: item for item in snapshot["members"]}

        if entity_type == "source_edit":
            record = session.get(SourceFileEdit, transaction_id)
            self._require_frozen(record)
            if record.batch_id:
                raise ValueError("Batch members cannot be reconciled individually")
            record.status = target_status
            record.applied_sha256 = record.candidate_sha256
            record.error_message = None
            return

        if entity_type == "source_edit_batch":
            batch = session.get(SourceEditBatch, transaction_id)
            self._require_frozen(batch)
            edits = list(
                session.scalars(
                    select(SourceFileEdit).where(
                        SourceFileEdit.batch_id == transaction_id
                    )
                ).all()
            )
            if len(edits) != batch.edit_count or set(members) != {
                item.id for item in edits
            }:
                raise ValueError("Source edit batch membership changed")
            batch.status = target_status
            batch.error_message = None
            for item in edits:
                item.status = target_status
                item.applied_sha256 = item.candidate_sha256
                item.error_message = None
            return

        if entity_type == "file_organization":
            proposal = session.get(FileOrganizationProposal, transaction_id)
            self._require_frozen(proposal)
            if proposal.batch_id:
                raise ValueError("Batch members cannot be reconciled individually")
            self._reconcile_organization_member(session, proposal, members[proposal.id])
            proposal.status = target_status
            proposal.applied_sha256 = proposal.original_sha256
            proposal.error_message = None
            return

        if entity_type == "file_organization_batch":
            batch = session.get(FileOrganizationBatch, transaction_id)
            self._require_frozen(batch)
            proposals = list(
                session.scalars(
                    select(FileOrganizationProposal).where(
                        FileOrganizationProposal.batch_id == transaction_id
                    )
                ).all()
            )
            if len(proposals) != batch.operation_count or set(members) != {
                item.id for item in proposals
            }:
                raise ValueError("File organization batch membership changed")
            batch.status = target_status
            batch.error_message = None
            for proposal in proposals:
                self._reconcile_organization_member(
                    session,
                    proposal,
                    members[proposal.id],
                )
                proposal.status = target_status
                proposal.applied_sha256 = proposal.original_sha256
                proposal.error_message = None
            return

        if entity_type == "file_recycle":
            proposal = session.get(FileRecycleProposal, transaction_id)
            self._require_frozen(proposal)
            if proposal.batch_id:
                raise ValueError("Batch members cannot be reconciled individually")
            proposal.status = target_status
            proposal.error_message = None
            file = session.get(File, proposal.file_id)
            if file is None:
                raise ValueError("Recycle File record is missing")
            file.status = "recycled"
            return

        if entity_type == "file_recycle_batch":
            batch = session.get(FileRecycleBatch, transaction_id)
            self._require_frozen(batch)
            proposals = list(
                session.scalars(
                    select(FileRecycleProposal).where(
                        FileRecycleProposal.batch_id == transaction_id
                    )
                ).all()
            )
            if len(proposals) != batch.item_count or set(members) != {
                item.id for item in proposals
            }:
                raise ValueError("Recycle batch membership changed")
            batch.status = target_status
            batch.error_message = None
            for proposal in proposals:
                proposal.status = target_status
                proposal.error_message = None
                file = session.get(File, proposal.file_id)
                if file is None:
                    raise ValueError("Recycle batch File record is missing")
                file.status = "recycled"
            return

        raise ValueError("Recovery reconciliation entity type is not supported")

    @staticmethod
    def _reconcile_organization_member(
        session,
        proposal: FileOrganizationProposal,
        member: dict[str, Any],
    ) -> None:
        observation = next(
            (
                item
                for item in member["observations"]
                if item["role"] == "target_path"
            ),
            None,
        )
        if (
            observation is None
            or not observation["is_file"]
            or observation["sha256"] != proposal.original_sha256
        ):
            raise ValueError("Organization target no longer matches the transaction")

        target = Path(proposal.target_path)
        file = session.get(File, proposal.file_id)
        if file is None:
            raise ValueError("File organization File record is missing")
        file.path = str(target)
        file.filename = target.name
        file.sha256 = proposal.original_sha256
        if observation.get("size") is not None:
            file.size = int(observation["size"])

    @staticmethod
    def _require_frozen(record: Any) -> None:
        if record is None:
            raise ValueError("Recovery transaction not found")
        if record.status != "recovery_required":
            raise ValueError("Recovery transaction is no longer recovery_required")

    def _record(
        self,
        proposal_id: str,
        *,
        expected_status: str | None = None,
    ) -> RecoveryReconciliationProposal:
        with self.database.session() as session:
            proposal = session.get(RecoveryReconciliationProposal, proposal_id)
            if proposal is None:
                raise ValueError("Recovery reconciliation proposal not found")
            if expected_status and proposal.status != expected_status:
                raise ValueError(
                    f"Recovery reconciliation proposal must be {expected_status}"
                )
            session.expunge(proposal)
            return proposal

    def _mark_stale(self, proposal_id: str, message: str) -> None:
        with self.database.session() as session:
            proposal = session.get(RecoveryReconciliationProposal, proposal_id)
            if proposal is None or proposal.status != "pending":
                return
            proposal.status = "stale"
            proposal.stale_at = datetime.now(timezone.utc)
            proposal.error_message = message[:2000]
            session.add(
                AuditLog(
                    task_id=proposal.task_id,
                    action="recovery_reconciliation_stale",
                    target=proposal.id,
                    result=message[:2000],
                    risk_level=8,
                )
            )

    @staticmethod
    def payload(proposal: RecoveryReconciliationProposal) -> dict[str, Any]:
        return {
            "id": proposal.id,
            "task_id": proposal.task_id,
            "workspace_id": proposal.workspace_id,
            "entity_type": proposal.entity_type,
            "transaction_id": proposal.transaction_id,
            "status": proposal.status,
            "snapshot_fingerprint": proposal.snapshot_fingerprint,
            "snapshot_state": proposal.snapshot_state,
            "target_status": proposal.target_status,
            "historical_action": proposal.historical_action,
            "error_message": proposal.error_message,
            "created_at": proposal.created_at.isoformat(),
            "confirmed_at": (
                proposal.confirmed_at.isoformat()
                if proposal.confirmed_at
                else None
            ),
            "rejected_at": (
                proposal.rejected_at.isoformat()
                if proposal.rejected_at
                else None
            ),
            "stale_at": (
                proposal.stale_at.isoformat()
                if proposal.stale_at
                else None
            ),
            "requires_user_confirmation": proposal.status == "pending",
            "filesystem_mutation": False,
        }
