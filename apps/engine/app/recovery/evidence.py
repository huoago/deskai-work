from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.artifacts.service import ArtifactService
from app.database.models import AuditLog
from app.database.session import Database
from app.recovery.reconciliation import RecoveryReconciliationService
from app.recovery.snapshot import RecoverySnapshotService


class RecoveryEvidenceService:
    """Builds a support/audit package without copying user file contents."""

    def __init__(
        self,
        database: Database,
        snapshot_service: RecoverySnapshotService,
        reconciliation_service: RecoveryReconciliationService,
        artifact_service: ArtifactService,
    ) -> None:
        self.database = database
        self.snapshot_service = snapshot_service
        self.reconciliation_service = reconciliation_service
        self.artifact_service = artifact_service

    def export(self, entry: dict[str, Any]) -> dict[str, Any]:
        entity_type = str(entry.get("entity_type") or "")
        transaction_id = str(entry.get("id") or "")
        task_id = str(entry.get("task_id") or "")
        workspace_id = str(entry.get("workspace_id") or "")
        if not entity_type or not transaction_id:
            raise ValueError("Recovery evidence transaction identity is incomplete")
        if not task_id or not workspace_id:
            raise ValueError("Recovery evidence transaction is missing task/workspace ownership")

        snapshot: dict[str, Any] | None = None
        snapshot_note = "transaction_not_recovery_required"
        if entry.get("recovery_required"):
            snapshot = self.snapshot_service.capture(entity_type, transaction_id)
            snapshot_note = "captured"

        reconciliations = [
            item
            for item in self.reconciliation_service.list(
                workspace_id=workspace_id,
                transaction_id=transaction_id,
                limit=500,
            )
            if item.get("entity_type") == entity_type
        ]
        audit = self._audit_timeline(task_id)
        created_at = datetime.now(timezone.utc).isoformat()

        payloads: dict[str, bytes] = {
            "transaction.json": self._json_bytes(entry),
            "diagnostic.json": self._json_bytes(entry.get("diagnostic")),
            "snapshot.json": self._json_bytes(
                snapshot
                if snapshot is not None
                else {
                    "available": False,
                    "reason": snapshot_note,
                    "note": (
                        "A live Phase 19 filesystem snapshot is only captured while "
                        "the transaction is recovery_required."
                    ),
                }
            ),
            "reconciliations.json": self._json_bytes(reconciliations),
            "audit-log.json": self._json_bytes(audit),
        }
        payloads["summary.md"] = self._summary(
            entry=entry,
            created_at=created_at,
            snapshot=snapshot,
            reconciliations=reconciliations,
            audit=audit,
        ).encode("utf-8")

        file_integrity = {
            name: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for name, content in sorted(payloads.items())
        }
        manifest = {
            "format": "deskai-recovery-evidence-v1",
            "created_at": created_at,
            "entity_type": entity_type,
            "transaction_id": transaction_id,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "transaction_status": entry.get("status"),
            "recovery_required": bool(entry.get("recovery_required")),
            "live_snapshot": snapshot is not None,
            "reconciliation_records": len(reconciliations),
            "audit_events": len(audit),
            "user_file_content_included": False,
            "user_files_modified": False,
            "package_files": file_integrity,
        }
        payloads["manifest.json"] = self._json_bytes(manifest)

        archive = io.BytesIO()
        with zipfile.ZipFile(
            archive,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as bundle:
            for name in sorted(payloads):
                bundle.writestr(name, payloads[name])

        artifact = self.artifact_service.create_recovery_evidence_package(
            task_id=task_id,
            workspace_id=workspace_id,
            filename=f"recovery-evidence-{entity_type}-{transaction_id[:8]}.zip",
            content=archive.getvalue(),
        )

        with self.database.session() as session:
            session.add(
                AuditLog(
                    task_id=task_id,
                    action="recovery_evidence_exported",
                    target=artifact.id,
                    result=(
                        f"entity_type={entity_type}; transaction_id={transaction_id}; "
                        f"artifact_sha256={artifact.sha256}; artifact_size={artifact.size}; "
                        "user_file_content_included=false; user_files_modified=false"
                    ),
                    risk_level=1,
                )
            )

        return {
            "artifact": self._artifact_payload(artifact),
            "manifest": manifest,
            "read_only_evidence": True,
            "user_file_content_included": False,
            "user_files_modified": False,
        }

    def _audit_timeline(self, task_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(AuditLog)
                    .where(AuditLog.task_id == task_id)
                    .order_by(AuditLog.timestamp.asc(), AuditLog.id.asc())
                    .limit(500)
                ).all()
            )
        return [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "agent_run_id": row.agent_run_id,
                "tool": row.tool,
                "action": row.action,
                "target": row.target,
                "result": row.result,
                "risk_level": row.risk_level,
            }
            for row in rows
        ]

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ).encode("utf-8")

    @staticmethod
    def _summary(
        *,
        entry: dict[str, Any],
        created_at: str,
        snapshot: dict[str, Any] | None,
        reconciliations: list[dict[str, Any]],
        audit: list[dict[str, Any]],
    ) -> str:
        diagnostic = entry.get("diagnostic") or {}
        filenames = ", ".join(str(item) for item in entry.get("filenames") or []) or "n/a"
        paths = entry.get("paths") or []
        lines = [
            "# DeskAI Recovery Evidence Package",
            "",
            f"- Created: {created_at}",
            f"- Entity type: {entry.get('entity_type')}",
            f"- Transaction id: {entry.get('id')}",
            f"- Status: {entry.get('status')}",
            f"- Workspace id: {entry.get('workspace_id')}",
            f"- Task id: {entry.get('task_id')}",
            f"- Transactional: {bool(entry.get('transactional'))}",
            f"- Files: {filenames}",
            f"- User file content included: false",
            f"- User files modified by export: false",
            "",
            "## Diagnostic",
            "",
            f"- Code: {diagnostic.get('code') or 'n/a'}",
            f"- Confidence: {diagnostic.get('confidence') or 'n/a'}",
            f"- Summary: {diagnostic.get('summary') or 'n/a'}",
            "",
            "## Current snapshot",
            "",
            (
                f"- State: {snapshot.get('assessment', {}).get('state')}"
                if snapshot
                else "- Live snapshot not captured because the transaction is not recovery_required."
            ),
            "",
            "## Recovery reconciliation history",
            "",
            f"- Records: {len(reconciliations)}",
            "",
            "## Audit timeline",
            "",
            f"- Events included: {len(audit)}",
            "",
            "## Known transaction paths",
            "",
        ]
        lines.extend(f"- {path}" for path in paths)
        if not paths:
            lines.append("- n/a")
        lines.extend(
            [
                "",
                "This package contains metadata, hashes, diagnostics, snapshots and audit evidence only.",
                "It does not embed Workspace file bytes, backups, candidates or recycle quarantine copies.",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _artifact_payload(item) -> dict[str, Any]:
        return {
            "id": item.id,
            "task_id": item.task_id,
            "workspace_id": item.workspace_id,
            "kind": item.kind,
            "filename": item.filename,
            "path": item.path,
            "mime_type": item.mime_type,
            "sha256": item.sha256,
            "size": item.size,
            "created_at": item.created_at.isoformat(),
        }
