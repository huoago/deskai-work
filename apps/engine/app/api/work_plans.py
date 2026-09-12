from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["work-plans"])


def _raise_api_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 409
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/work-plans")
def list_work_plans(
    request: Request,
    workspace_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return request.app.state.work_plan_service.list(
        workspace_id=workspace_id,
        task_id=task_id,
        limit=limit,
    )


@router.get("/work-plan-worker/status")
def get_work_plan_worker_status(request: Request) -> dict[str, Any]:
    return request.app.state.work_plan_worker.snapshot().as_dict()


@router.post("/work-plan-worker/process")
def process_work_plan_queue(
    request: Request,
    limit: int = Query(default=3, ge=1, le=20),
) -> dict[str, Any]:
    processed = request.app.state.work_plan_worker.process_available(limit=limit)
    return {
        "processed": processed,
        "worker": request.app.state.work_plan_worker.snapshot().as_dict(),
    }


@router.get("/work-plans/{plan_id}")
def get_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.work_plan_service.get(plan_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/tasks/{task_id}/work-plan")
def draft_work_plan(task_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.work_plan_service.draft(task_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/start")
def start_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.work_plan_service.start(plan_id)
        request.app.state.work_plan_worker.wake()
        return result
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/resume")
def resume_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.work_plan_service.resume(plan_id)
        if result.get("status") == "queued":
            request.app.state.work_plan_worker.wake()
        return result
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/retry")
def retry_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.work_plan_service.retry(plan_id)
        request.app.state.work_plan_worker.wake()
        return result
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/cancel")
def cancel_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.work_plan_service.cancel(plan_id)
    except ValueError as exc:
        _raise_api_error(exc)
