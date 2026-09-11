from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["recycle-batches"])


def _raise_api_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 409
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/recycle-batches")
def list_recycle_batches(
    request: Request,
    workspace_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, Any]]:
    return request.app.state.file_recycle_batch_service.list(
        workspace_id=workspace_id,
        task_id=task_id,
        limit=limit,
    )


@router.get("/recycle-batches/{batch_id}")
def get_recycle_batch(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.file_recycle_batch_service.get(batch_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recycle-batches/{batch_id}/confirm")
def confirm_recycle_batch(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.file_recycle_batch_service.confirm(batch_id)
    except ValueError as exc:
        _raise_api_error(exc)
    request.app.state.parser_worker.wake()
    request.app.state.workspace_watcher.wake()
    return result


@router.post("/recycle-batches/{batch_id}/reject")
def reject_recycle_batch(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.file_recycle_batch_service.reject(batch_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recycle-batches/{batch_id}/restore")
def restore_recycle_batch(batch_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.file_recycle_batch_service.restore(batch_id)
    except ValueError as exc:
        _raise_api_error(exc)
    request.app.state.parser_worker.wake()
    request.app.state.knowledge_indexer.wake()
    request.app.state.workspace_watcher.wake()
    return result
