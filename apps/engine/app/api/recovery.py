from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["recovery"])

VISIBLE_STATUSES = {
    "applied",
    "rolling_back",
    "rolled_back",
    "recycled",
    "restoring",
    "restored",
    "recovery_required",
}


def _updated_at(item: dict[str, Any]) -> str:
    for key in (
        "restored_at",
        "rolled_back_at",
        "recycled_at",
        "applied_at",
        "confirmed_at",
        "created_at",
    ):
        value = item.get(key)
        if value:
            return str(value)
    return str(item.get("created_at") or "")


def _action(kind: str, item: dict[str, Any]) -> str | None:
    if item.get("recovery_required"):
        return None
    if kind in {
        "source_edit",
        "source_edit_batch",
        "file_organization",
        "file_organization_batch",
    } and item.get("can_rollback"):
        return "rollback"
    if kind in {"file_recycle", "file_recycle_batch"} and item.get("can_restore"):
        return "restore"
    return None


def _filenames(kind: str, item: dict[str, Any]) -> list[str]:
    if kind == "source_edit":
        return [str(item.get("filename") or "Unknown file")]
    if kind == "source_edit_batch":
        return [
            str(member.get("filename") or "Unknown file")
            for member in item.get("edits") or []
        ]
    if kind == "file_organization":
        return [str(item.get("filename") or "Unknown file")]
    if kind == "file_organization_batch":
        return [
            str(member.get("filename") or "Unknown file")
            for member in item.get("operations") or []
        ]
    if kind == "file_recycle":
        return [str(item.get("filename") or "Unknown file")]
    return [
        str(member.get("filename") or "Unknown file")
        for member in item.get("items") or []
    ]


def _paths(kind: str, item: dict[str, Any]) -> list[str]:
    if kind == "file_organization":
        return [
            value
            for value in (
                item.get("original_path"),
                item.get("target_path"),
            )
            if value
        ]
    if kind == "file_organization_batch":
        result: list[str] = []
        for member in item.get("operations") or []:
            for key in ("original_path", "target_path"):
                value = member.get(key)
                if value:
                    result.append(str(value))
        return result
    if kind == "file_recycle":
        value = item.get("original_path")
        return [str(value)] if value else []
    if kind == "file_recycle_batch":
        return [
            str(member["original_path"])
            for member in item.get("items") or []
            if member.get("original_path")
        ]
    return []


def _entry(
    kind: str,
    scope: str,
    item: dict[str, Any],
    count_key: str | None = None,
) -> dict[str, Any]:
    filenames = _filenames(kind, item)
    count = int(item.get(count_key) or len(filenames) or 1) if count_key else 1
    return {
        "id": item["id"],
        "entity_type": kind,
        "scope": scope,
        "task_id": item.get("task_id"),
        "workspace_id": item.get("workspace_id"),
        "status": item.get("status"),
        "summary": item.get("summary") or "",
        "created_at": item.get("created_at"),
        "updated_at": _updated_at(item),
        "error_message": item.get("error_message"),
        "item_count": count,
        "filenames": filenames,
        "paths": _paths(kind, item),
        "action": _action(kind, item),
        "recovery_required": bool(item.get("recovery_required")),
        "transactional": bool(item.get("transactional")),
    }


@router.get("/recovery")
def list_recovery_entries(
    request: Request,
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for item in request.app.state.source_edit_service.list(
        workspace_id=workspace_id,
        limit=500,
    ):
        if item.get("batch_id") or item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(_entry("source_edit", "single", item))

    for item in request.app.state.source_edit_batch_service.list(
        workspace_id=workspace_id,
        limit=200,
    ):
        if item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(
            _entry("source_edit_batch", "batch", item, "edit_count")
        )

    for item in request.app.state.file_organization_service.list(
        workspace_id=workspace_id,
        limit=500,
    ):
        if item.get("batch_id") or item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(_entry("file_organization", "single", item))

    for item in request.app.state.file_organization_batch_service.list(
        workspace_id=workspace_id,
        limit=200,
    ):
        if item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(
            _entry(
                "file_organization_batch",
                "batch",
                item,
                "operation_count",
            )
        )

    for item in request.app.state.file_recycle_service.list(
        workspace_id=workspace_id,
        limit=500,
    ):
        if item.get("batch_id") or item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(_entry("file_recycle", "single", item))

    for item in request.app.state.file_recycle_batch_service.list(
        workspace_id=workspace_id,
        limit=200,
    ):
        if item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(
            _entry("file_recycle_batch", "batch", item, "item_count")
        )

    candidates.sort(
        key=lambda item: (str(item.get("updated_at") or ""), item["id"]),
        reverse=True,
    )
    return candidates[:limit]
