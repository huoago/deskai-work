from __future__ import annotations

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
    SourceEditBatch,
    SourceFileEdit,
)
from app.database.session import Database
from app.indexing.hashing import sha256_file
from app.source_edits.service import SourceFileEditService

SUPPORTED_ENTITY_TYPES = {
    "source_edit",
    "source_edit_batch",
    "file_organization",
    "file_organization_batch",
    "file_recycle",
    "file_recycle_batch",
}


class RecoverySnapshotService:
    """Builds an ephemeral, read-only view of recovery evidence on disk."""

    def __init__(
        self,
        database: Database,
        source_edit_service: SourceFileEditService,
    ) -> None:
        self.database = database
        self.source_edit_service = source_edit_service

    def capture(self, entity_type: str, transaction_id: str) -> dict[str, Any]:
        if entity_type not in SUPPORTED_ENTITY_TYPES:
            raise ValueError("Recovery snapshot entity type is not supported")

        with self.database.session() as session:
            if entity_type == "source_edit":
                record = session.get(SourceFileEdit, transaction_id)
                if record is None:
                    raise ValueError("Recovery transaction not found")
                self._require_recovery_required(record.status)
                members = [self._source_edit_descriptor(session, record)]
                task_id = record.task_id
                workspace_id = record.workspace_id
                transactional = False
            elif entity_type == "source_edit_batch":
                batch = session.get(SourceEditBatch, transaction_id)
                if batch is None:
                    raise ValueError("Recovery transaction not found")
                self._require_recovery_required(batch.status)
                edits = list(
                    session.scalars(
                        select(SourceFileEdit)
                        .where(SourceFileEdit.batch_id == batch.id)
                        .order_by(SourceFileEdit.created_at, SourceFileEdit.id)
                    ).all()
                )
                members = [self._source_edit_descriptor(session, item) for item in edits]
                task_id = batch.task_id
                workspace_id = batch.workspace_id
                transactional = True
            elif entity_type == "file_organization":
                record = session.get(FileOrganizationProposal, transaction_id)
                if record is None:
                    raise ValueError("Recovery transaction not found")
                self._require_recovery_required(record.status)
                members = [self._organization_descriptor(session, record)]
                task_id = record.task_id
                workspace_id = record.workspace_id
                transactional = False
            elif entity_type == "file_organization_batch":
                batch = session.get(FileOrganizationBatch, transaction_id)
                if batch is None:
                    raise ValueError("Recovery transaction not found")
                self._require_recovery_required(batch.status)
                operations = list(
                    session.scalars(
                        select(FileOrganizationProposal)
                        .where(FileOrganizationProposal.batch_id == batch.id)
                        .order_by(
                            FileOrganizationProposal.created_at,
                            FileOrganizationProposal.id,
                        )
                    ).all()
                )
                members = [self._organization_descriptor(session, item) for item in operations]
                task_id = batch.task_id
                workspace_id = batch.workspace_id
                transactional = True
            elif entity_type == "file_recycle":
                record = session.get(FileRecycleProposal, transaction_id)
                if record is None:
                    raise ValueError("Recovery transaction not found")
                self._require_recovery_required(record.status)
                members = [self._recycle_descriptor(session, record)]
                task_id = record.task_id
                workspace_id = record.workspace_id
                transactional = False
            else:
                batch = session.get(FileRecycleBatch, transaction_id)
                if batch is None:
                    raise ValueError("Recovery transaction not found")
                self._require_recovery_required(batch.status)
                proposals = list(
                    session.scalars(
                        select(FileRecycleProposal)
                        .where(FileRecycleProposal.batch_id == batch.id)
                        .order_by(
                            FileRecycleProposal.created_at,
                            FileRecycleProposal.id,
                        )
                    ).all()
                )
                members = [self._recycle_descriptor(session, item) for item in proposals]
                task_id = batch.task_id
                workspace_id = batch.workspace_id
                transactional = True

            timeline = [
                {
                    "timestamp": row.timestamp.isoformat(),
                    "action": row.action,
                    "target": row.target,
                    "result": row.result,
                    "risk_level": row.risk_level,
                }
                for row in session.scalars(
                    select(AuditLog)
                    .where(AuditLog.task_id == task_id)
                    .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
                    .limit(25)
                ).all()
            ]

        inspected = [self._inspect_member(entity_type, member) for member in members]
        assessment = self._assessment(entity_type, inspected)
        return {
            "entity_type": entity_type,
            "transaction_id": transaction_id,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "transactional": transactional,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "persisted_status": "recovery_required",
            "read_only": True,
            "members": inspected,
            "assessment": assessment,
            "audit_timeline": timeline,
        }

    @staticmethod
    def _require_recovery_required(status: str) -> None:
        if status != "recovery_required":
            raise ValueError(
                "Recovery snapshot is only available for recovery_required transactions"
            )

    def _source_edit_descriptor(
        self,
        session,
        edit: SourceFileEdit,
    ) -> dict[str, Any]:
        file = session.get(File, edit.file_id)
        source_path = file.path if file is not None else None
        filename = (
            file.filename
            if file is not None
            else Path(edit.candidate_path).name
        )
        backup_path = edit.backup_path
        if not backup_path and filename:
            backup_path = str(
                (self.source_edit_service.backup_root / edit.id / filename).resolve()
            )
        return {
            "id": edit.id,
            "filename": filename,
            "tracked_path": source_path,
            "tracked_file_status": file.status if file is not None else None,
            "source_path": source_path,
            "candidate_path": edit.candidate_path,
            "backup_path": backup_path,
            "original_sha256": edit.original_sha256,
            "candidate_sha256": edit.candidate_sha256,
            "applied_sha256": edit.applied_sha256,
        }

    @staticmethod
    def _organization_descriptor(
        session,
        proposal: FileOrganizationProposal,
    ) -> dict[str, Any]:
        file = session.get(File, proposal.file_id)
        return {
            "id": proposal.id,
            "filename": file.filename if file is not None else Path(proposal.target_path).name,
            "tracked_path": file.path if file is not None else None,
            "tracked_file_status": file.status if file is not None else None,
            "original_path": proposal.original_path,
            "target_path": proposal.target_path,
            "original_sha256": proposal.original_sha256,
            "applied_sha256": proposal.applied_sha256,
        }

    @staticmethod
    def _recycle_descriptor(
        session,
        proposal: FileRecycleProposal,
    ) -> dict[str, Any]:
        file = session.get(File, proposal.file_id)
        return {
            "id": proposal.id,
            "filename": file.filename if file is not None else Path(proposal.original_path).name,
            "tracked_path": file.path if file is not None else None,
            "tracked_file_status": file.status if file is not None else None,
            "original_path": proposal.original_path,
            "quarantine_path": proposal.quarantine_path,
            "original_sha256": proposal.original_sha256,
            "original_size": proposal.original_size,
        }

    def _inspect_member(
        self,
        entity_type: str,
        member: dict[str, Any],
    ) -> dict[str, Any]:
        if entity_type.startswith("source_edit"):
            return self._inspect_source_edit(member)
        if entity_type.startswith("file_organization"):
            return self._inspect_organization(member)
        return self._inspect_recycle(member)

    def _inspect_source_edit(self, member: dict[str, Any]) -> dict[str, Any]:
        expected_source = {
            "original": member["original_sha256"],
            "candidate": member["candidate_sha256"],
            "applied": member.get("applied_sha256"),
        }
        source = self._observe_path(
            member.get("source_path"),
            "workspace_source",
            expected_source,
        )
        candidate = self._observe_path(
            member.get("candidate_path"),
            "staged_candidate",
            {"candidate": member["candidate_sha256"]},
        )
        backup = self._observe_path(
            member.get("backup_path"),
            "automatic_backup",
            {"original": member["original_sha256"]},
        )

        source_matches = set(source["matches"])
        if "original" in source_matches:
            state = "original"
            support_valid = True
            technical_action_ready = False
        elif {"candidate", "applied"} & source_matches:
            state = "applied"
            support_valid = (
                "original" in backup["matches"]
                and "candidate" in candidate["matches"]
            )
            technical_action_ready = bool(
                member.get("applied_sha256")
                and support_valid
                and "applied" in source_matches
            )
        elif not source["exists"]:
            state = "missing"
            support_valid = False
            technical_action_ready = False
        else:
            state = "unknown"
            support_valid = False
            technical_action_ready = False

        return {
            "id": member["id"],
            "filename": member["filename"],
            "tracked_path": member.get("tracked_path"),
            "tracked_file_status": member.get("tracked_file_status"),
            "state": state,
            "supporting_evidence_valid": support_valid,
            "technical_action_ready": technical_action_ready,
            "observations": [source, backup, candidate],
        }

    def _inspect_organization(self, member: dict[str, Any]) -> dict[str, Any]:
        expected = {
            "original": member["original_sha256"],
            "applied": member.get("applied_sha256"),
        }
        original = self._observe_path(
            member["original_path"],
            "original_path",
            expected,
        )
        target = self._observe_path(
            member["target_path"],
            "target_path",
            expected,
        )
        original_good = "original" in original["matches"]
        target_good = bool({"original", "applied"} & set(target["matches"]))

        if original_good and not target["exists"]:
            state = "original"
            technical_action_ready = False
        elif not original["exists"] and target_good:
            state = "applied"
            technical_action_ready = True
        elif original_good and target_good:
            state = "conflict"
            technical_action_ready = False
        elif not original["exists"] and not target["exists"]:
            state = "missing"
            technical_action_ready = False
        else:
            state = "unknown"
            technical_action_ready = False

        safe = state in {"original", "applied"}
        return {
            "id": member["id"],
            "filename": member["filename"],
            "tracked_path": member.get("tracked_path"),
            "tracked_file_status": member.get("tracked_file_status"),
            "state": state,
            "supporting_evidence_valid": safe,
            "technical_action_ready": technical_action_ready,
            "observations": [original, target],
        }

    def _inspect_recycle(self, member: dict[str, Any]) -> dict[str, Any]:
        expected = {"original": member["original_sha256"]}
        original = self._observe_path(
            member["original_path"],
            "workspace_original",
            expected,
        )
        quarantine = self._observe_path(
            member["quarantine_path"],
            "quarantine_copy",
            expected,
        )
        original_good = "original" in original["matches"]
        quarantine_good = "original" in quarantine["matches"]

        if not original["exists"] and quarantine_good:
            state = "recycled"
            support_valid = True
            technical_action_ready = True
        elif original_good and quarantine_good:
            state = "restored"
            support_valid = True
            technical_action_ready = False
        elif original_good and not quarantine["exists"]:
            state = "original_without_quarantine"
            support_valid = False
            technical_action_ready = False
        elif not original["exists"] and not quarantine["exists"]:
            state = "missing"
            support_valid = False
            technical_action_ready = False
        else:
            state = "unknown"
            support_valid = False
            technical_action_ready = False

        return {
            "id": member["id"],
            "filename": member["filename"],
            "tracked_path": member.get("tracked_path"),
            "tracked_file_status": member.get("tracked_file_status"),
            "state": state,
            "supporting_evidence_valid": support_valid,
            "technical_action_ready": technical_action_ready,
            "observations": [original, quarantine],
        }

    @staticmethod
    def _observe_path(
        raw_path: str | None,
        role: str,
        expected_hashes: dict[str, str | None],
    ) -> dict[str, Any]:
        base = {
            "role": role,
            "path": raw_path,
            "exists": False,
            "is_file": False,
            "is_symlink": False,
            "size": None,
            "sha256": None,
            "matches": [],
            "error": None,
        }
        if not raw_path:
            base["error"] = "path_unavailable"
            return base

        path = Path(raw_path)
        try:
            link_component = RecoverySnapshotService._link_component(path)
            if link_component is not None:
                base["exists"] = link_component == str(path)
                base["is_symlink"] = True
                base["error"] = f"link_component_not_followed:{link_component}"
                return base
            if not path.exists():
                return base
            base["exists"] = True
            if not path.is_file():
                base["error"] = "not_a_regular_file"
                return base
            base["is_file"] = True
            stat = path.stat()
            base["size"] = stat.st_size
            digest = sha256_file(path)
            base["sha256"] = digest
            base["matches"] = [
                label
                for label, expected in expected_hashes.items()
                if expected and digest == expected
            ]
            return base
        except OSError as exc:
            base["error"] = f"{type(exc).__name__}: {exc}"
            return base

    @staticmethod
    def _link_component(path: Path) -> str | None:
        chain = [candidate for candidate in reversed(path.parents)]
        chain.append(path)
        for candidate in chain:
            try:
                if candidate.is_symlink():
                    return str(candidate)
                is_junction = getattr(candidate, "is_junction", None)
                if callable(is_junction) and is_junction():
                    return str(candidate)
            except OSError:
                return str(candidate)
        return None

    @staticmethod
    def _assessment(
        entity_type: str,
        members: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not members:
            return {
                "state": "ambiguous",
                "safe_state_detected": False,
                "technical_action_preconditions_satisfied": False,
                "safe_to_retry_existing_action": False,
                "action_after_reconciliation": None,
                "requires_operator_reconciliation": True,
                "reason": "The persisted transaction has no members to inspect.",
            }

        states = [member["state"] for member in members]
        unique_states = set(states)
        recognized = {
            "source_edit": {"original", "applied"},
            "source_edit_batch": {"original", "applied"},
            "file_organization": {"original", "applied"},
            "file_organization_batch": {"original", "applied"},
            "file_recycle": {"recycled", "restored"},
            "file_recycle_batch": {"recycled", "restored"},
        }[entity_type]

        if len(unique_states) == 1 and states[0] in recognized:
            common_state = states[0]
            state = f"consistent_{common_state}"
            safe_state = all(
                member["supporting_evidence_valid"]
                for member in members
            )
        elif len(unique_states) > 1 and unique_states.issubset(recognized):
            common_state = None
            state = "mixed_transaction"
            safe_state = False
        else:
            common_state = None
            state = "ambiguous"
            safe_state = False

        if entity_type.startswith("source_edit") or entity_type.startswith(
            "file_organization"
        ):
            action_after_reconciliation = (
                "rollback" if common_state == "applied" else None
            )
        else:
            action_after_reconciliation = (
                "restore" if common_state == "recycled" else None
            )

        technical_action_ready = bool(
            action_after_reconciliation
            and all(member["technical_action_ready"] for member in members)
        )

        if state == "mixed_transaction":
            reason = (
                "Transaction members resolve to different valid disk states; "
                "the batch is not atomic on disk."
            )
        elif state == "ambiguous":
            reason = (
                "At least one member is missing, conflicting, symlinked, "
                "hash-mismatched, or otherwise cannot be mapped to a trusted state."
            )
        elif technical_action_ready:
            reason = (
                "Disk evidence is internally consistent and the original recovery "
                "artifacts required by the historical action are present, but the "
                "persisted recovery_required status still blocks execution."
            )
        else:
            reason = (
                "Disk evidence resolves to one consistent state. No automatic state "
                "transition is performed; the persisted recovery_required record "
                "still requires operator reconciliation."
            )

        return {
            "state": state,
            "member_states": states,
            "safe_state_detected": safe_state,
            "technical_action_preconditions_satisfied": technical_action_ready,
            "safe_to_retry_existing_action": False,
            "action_after_reconciliation": action_after_reconciliation,
            "requires_operator_reconciliation": True,
            "reason": reason,
        }
