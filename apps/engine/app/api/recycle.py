from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["recycle-bin"])


def _raise_api_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 409
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/recycle-proposals")
def list_recycle_proposals(
    request: Request,
    workspace_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return request.app.state.file_recycle_service.list(
        workspace_id=workspace_id,
        task_id=task_id,
        limit=limit,
    )


@router.get("/recycle-proposals/{proposal_id}")
def get_recycle_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.file_recycle_service.get(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recycle-proposals/{proposal_id}/confirm")
def confirm_recycle_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.file_recycle_service.confirm(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)
    request.app.state.parser_worker.wake()
    request.app.state.workspace_watcher.wake()
    return result


@router.post("/recycle-proposals/{proposal_id}/reject")
def reject_recycle_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.file_recycle_service.reject(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recycle-proposals/{proposal_id}/restore")
def restore_recycled_file(proposal_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.file_recycle_service.restore(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)
    request.app.state.parser_worker.wake()
    request.app.state.knowledge_indexer.wake()
    request.app.state.workspace_watcher.wake()
    return result
