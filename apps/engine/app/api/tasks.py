from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.artifacts import artifact_payload
from app.database.models import AgentRun, AuditLog, GeneratedArtifact, Task, ToolCall, Workspace

router = APIRouter(tags=["tasks"])


class TaskCreate(BaseModel):
    workspace_id: str
    request: str = Field(min_length=1, max_length=20000)
    title: str | None = Field(default=None, max_length=1024)


def _task_payload(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "workspace_id": task.workspace_id,
        "title": task.title,
        "user_request": task.user_request,
        "status": task.status,
        "progress": task.progress,
        "result_text": task.result_text,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, request: Request) -> dict[str, Any]:
    with request.app.state.database.session() as session:
        workspace = session.get(Workspace, payload.workspace_id)
        if workspace is None or workspace.archived:
            raise HTTPException(status_code=404, detail="Workspace not found")
        title = (payload.title or payload.request.strip().splitlines()[0][:80]).strip()
        task = Task(
            workspace_id=payload.workspace_id,
            title=title or "Agent task",
            user_request=payload.request.strip(),
            status="pending",
            progress=0.0,
        )
        session.add(task)
        session.flush()
        result = _task_payload(task)
    request.app.state.agent_worker.wake()
    return result


@router.get("/tasks")
def list_tasks(
    request: Request,
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    with request.app.state.database.session() as session:
        statement = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if workspace_id:
            statement = statement.where(Task.workspace_id == workspace_id)
        return [_task_payload(item) for item in session.scalars(statement).all()]


@router.get("/tasks/{task_id}")
def task_detail(task_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.database.session() as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        runs = session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.started_at)
        ).all()
        run_ids = [run.id for run in runs]
        calls = []
        if run_ids:
            calls = session.scalars(
                select(ToolCall).where(ToolCall.agent_run_id.in_(run_ids)).order_by(ToolCall.started_at)
            ).all()
        payload = _task_payload(task)
        payload["runs"] = [
            {
                "id": run.id,
                "model": run.model,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
            }
            for run in runs
        ]
        artifacts = session.scalars(
            select(GeneratedArtifact)
            .where(GeneratedArtifact.task_id == task_id)
            .order_by(GeneratedArtifact.created_at)
        ).all()
        payload["artifacts"] = [artifact_payload(item) for item in artifacts]
        payload["tool_calls"] = [
            {
                "id": call.id,
                "agent_run_id": call.agent_run_id,
                "tool_name": call.tool_name,
                "arguments": call.arguments_json,
                "result_summary": call.result_summary,
                "status": call.status,
                "risk_level": call.risk_level,
                "confirmation_required": call.confirmation_required,
                "started_at": call.started_at.isoformat() if call.started_at else None,
                "completed_at": call.completed_at.isoformat() if call.completed_at else None,
            }
            for call in calls
        ]
    payload["file_edits"] = request.app.state.source_edit_service.list(
        task_id=task_id,
        limit=100,
    )
    payload["file_edit_batches"] = request.app.state.source_edit_batch_service.list(
        task_id=task_id,
        limit=100,
    )
    return payload


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, request: Request) -> dict[str, Any]:
    if not request.app.state.agent_worker.retry(task_id):
        raise HTTPException(status_code=409, detail="Task is not failed/blocked or does not exist")
    with request.app.state.database.session() as session:
        task = session.get(Task, task_id)
        return _task_payload(task)


@router.get("/agent/status")
def agent_status(request: Request, workspace_id: str | None = Query(default=None)) -> dict[str, Any]:
    snapshot = request.app.state.agent_worker.snapshot().as_dict()
    with request.app.state.database.session() as session:
        statement = select(Task.status, func.count(Task.id)).group_by(Task.status)
        if workspace_id:
            statement = statement.where(Task.workspace_id == workspace_id)
        counts = {str(key): int(value) for key, value in session.execute(statement).all()}
    snapshot["workspace_id"] = workspace_id
    snapshot["task_counts"] = counts
    return snapshot


@router.post("/agent/process")
def process_agent_queue(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    processed = request.app.state.agent_worker.process_available(limit=limit)
    return {"processed": processed, "status": request.app.state.agent_worker.snapshot().as_dict()}


@router.get("/activity")
def list_activity(
    request: Request,
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    with request.app.state.database.session() as session:
        statement = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        if workspace_id:
            task_ids = select(Task.id).where(Task.workspace_id == workspace_id)
            statement = statement.where(AuditLog.task_id.in_(task_ids))
        items = session.scalars(statement).all()
        return [
            {
                "id": item.id,
                "timestamp": item.timestamp.isoformat(),
                "task_id": item.task_id,
                "agent_run_id": item.agent_run_id,
                "tool": item.tool,
                "action": item.action,
                "target": item.target,
                "result": item.result,
                "risk_level": item.risk_level,
            }
            for item in items
        ]
