from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.recovery import VISIBLE_STATUSES, _entry

router = APIRouter(tags=["recovery-evidence"])

ENTITY_CONFIG = {
    "source_edit": ("source_edit_service", "single", None),
    "source_edit_batch": ("source_edit_batch_service", "batch", "edit_count"),
    "file_organization": ("file_organization_service", "single", None),
    "file_organization_batch": (
        "file_organization_batch_service",
        "batch",
        "operation_count",
    ),
    "file_recycle": ("file_recycle_service", "single", None),
    "file_recycle_batch": ("file_recycle_batch_service", "batch", "item_count"),
}


@router.get("/recovery-evidence/capabilities")
def recovery_evidence_capabilities() -> dict[str, Any]:
    return {
        "format": "zip",
        "schema": "deskai-recovery-evidence-v1",
        "includes": [
            "transaction_metadata",
            "phase18_diagnostic",
            "phase19_live_snapshot_when_recovery_required",
            "phase20_reconciliation_history",
            "task_audit_log",
            "sha256_integrity_manifest",
            "human_readable_summary",
        ],
        "excludes": [
            "workspace_file_bytes",
            "source_edit_candidate_bytes",
            "automatic_backup_bytes",
            "recycle_quarantine_bytes",
        ],
        "user_files_modified": False,
        "agent_tool_available": False,
    }


@router.post("/recovery/{entity_type}/{transaction_id}/evidence-package")
def export_recovery_evidence(
    entity_type: str,
    transaction_id: str,
    request: Request,
) -> dict[str, Any]:
    config = ENTITY_CONFIG.get(entity_type)
    if config is None:
        raise HTTPException(status_code=404, detail="Recovery evidence entity type is not supported")

    service_name, scope, count_key = config
    service = getattr(request.app.state, service_name)
    try:
        item = service.get(transaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if item.get("status") not in VISIBLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Recovery evidence export is limited to transactions visible in Recovery Center",
        )
    if scope == "single" and item.get("batch_id"):
        raise HTTPException(
            status_code=409,
            detail="Batch members must be exported through the parent recovery transaction",
        )

    entry = _entry(entity_type, scope, item, count_key)
    try:
        return request.app.state.recovery_evidence_service.export(entry)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
