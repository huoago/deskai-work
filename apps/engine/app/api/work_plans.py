from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["work-plans"])


class WorkPlanSupervisionUpdate(BaseModel):
    max_auto_steps: int | None = Field(default=None, ge=1, le=100)
    runtime_budget_seconds: int | None = Field(default=None, ge=30, le=86400)
    step_timeout_seconds: int | None = Field(default=None, ge=5, le=3600)
    failure_policy: Literal["pause", "stop"] | None = None
    approval_risk_threshold: int | None = Field(default=None, ge=0, le=4)


class WorkPlanSkipRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


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


@router.get("/work-plans/{plan_id}/events")
def list_work_plan_events(
    plan_id: str,
    request: Request,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    try:
        return request.app.state.work_plan_service.list_events(
            plan_id,
            unread_only=unread_only,
            limit=limit,
        )
    except ValueError as exc:
        _raise_api_error(exc)


@router.get("/work-plans/{plan_id}/notifications")
def list_work_plan_notifications(
    plan_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    try:
        events = request.app.state.work_plan_service.list_events(
            plan_id,
            unread_only=True,
            limit=limit,
        )
        return [
            item
            for item in events
            if item.get("severity") in {"warning", "action"}
        ]
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/events/{event_id}/acknowledge")
def acknowledge_work_plan_event(
    plan_id: str,
    event_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.work_plan_service.acknowledge_event(
            plan_id,
            event_id,
        )
    except ValueError as exc:
        _raise_api_error(exc)


@router.patch("/work-plans/{plan_id}/supervision")
def update_work_plan_supervision(
    plan_id: str,
    payload: WorkPlanSupervisionUpdate,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.work_plan_service.update_supervision(
            plan_id,
            max_auto_steps=payload.max_auto_steps,
            runtime_budget_seconds=payload.runtime_budget_seconds,
            step_timeout_seconds=payload.step_timeout_seconds,
            failure_policy=payload.failure_policy,
            approval_risk_threshold=payload.approval_risk_threshold,
        )
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/pause")
def pause_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.work_plan_service.pause(plan_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/continue")
def continue_work_plan(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        result = request.app.state.work_plan_service.continue_plan(plan_id)
        if result.get("status") == "queued":
            request.app.state.work_plan_worker.wake()
        return result
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/steps/{step_id}/approve")
def approve_work_plan_step(
    plan_id: str,
    step_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        result = request.app.state.work_plan_service.approve_step(
            plan_id,
            step_id,
        )
        if result.get("status") == "queued":
            request.app.state.work_plan_worker.wake()
        return result
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/work-plans/{plan_id}/steps/{step_id}/skip")
def skip_work_plan_step(
    plan_id: str,
    step_id: str,
    payload: WorkPlanSkipRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        result = request.app.state.work_plan_service.skip_step(
            plan_id,
            step_id,
            reason=payload.reason,
        )
        if result.get("status") == "queued":
            request.app.state.work_plan_worker.wake()
        return result
    except ValueError as exc:
        _raise_api_error(exc)


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
