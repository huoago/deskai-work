from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["file-organization"])


def _raise_api_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 409
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/file-operations")
def list_file_operations(
    request: Request,
    workspace_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return request.app.state.file_organization_service.list(
        workspace_id=workspace_id,
        task_id=task_id,
        limit=limit,
    )


@router.get("/file-operations/{proposal_id}")
def get_file_operation(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.file_organization_service.get(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/file-operations/{proposal_id}/confirm")
def confirm_file_operation(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.file_organization_service.confirm(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)
    request.app.state.parser_worker.wake()
    request.app.state.workspace_watcher.wake()
    return result


@router.post("/file-operations/{proposal_id}/reject")
def reject_file_operation(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.file_organization_service.reject(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/file-operations/{proposal_id}/rollback")
def rollback_file_operation(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.file_organization_service.rollback(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)
    request.app.state.parser_worker.wake()
    request.app.state.workspace_watcher.wake()
    return result
