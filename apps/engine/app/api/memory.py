from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.database.models import MemoryLearningJob

router = APIRouter(tags=["memory"])


class MemoryCreate(BaseModel):
    workspace_id: str | None = None
    type: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=255)
    value: Any
    importance: float = Field(default=0.8, ge=0, le=1)


class MemoryUpdate(BaseModel):
    type: str | None = Field(default=None, min_length=1, max_length=64)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    predicate: str | None = Field(default=None, min_length=1, max_length=255)
    value: Any | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    status: str | None = Field(default=None, pattern="^(active|inactive)$")
    reason: str = Field(default="manual edit", max_length=1000)


def _memory_payload(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "type": item.type,
        "subject": item.subject,
        "predicate": item.predicate,
        "value": item.value_json,
        "confidence": item.confidence,
        "importance": item.importance,
        "source_type": item.source_type,
        "source_message_id": item.source_message_id,
        "source_chunk_id": item.source_chunk_id,
        "status": item.status,
        "valid_from": item.valid_from.isoformat() if item.valid_from else None,
        "valid_to": item.valid_to.isoformat() if item.valid_to else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


@router.get("/memory/status")
def memory_status(request: Request, workspace_id: str | None = Query(default=None)) -> dict:
    service = request.app.state.memory_service
    memories = service.list_memories(
        workspace_id=workspace_id,
        include_global=True,
        include_inactive=False,
    )
    snapshot = request.app.state.memory_worker.snapshot().as_dict()
    with request.app.state.database.session() as session:
        statement = select(
            MemoryLearningJob.status,
            func.count(MemoryLearningJob.id),
        ).group_by(MemoryLearningJob.status)
        if workspace_id:
            statement = statement.where(MemoryLearningJob.workspace_id == workspace_id)
        job_counts = {
            str(job_status): int(count)
            for job_status, count in session.execute(statement).all()
        }
    snapshot.update(
        {
            "workspace_id": workspace_id,
            "active_memories": len(memories),
            "queued_jobs": job_counts.get("queued", 0),
            "processing_jobs": job_counts.get("processing", 0),
            "failed_jobs": job_counts.get("failed", 0),
            "blocked_jobs": job_counts.get("blocked", 0),
        }
    )
    return snapshot


@router.post("/memory/process")
def process_memory_learning(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    processed = request.app.state.memory_worker.process_available(limit=limit)
    return {
        "processed": processed,
        "status": request.app.state.memory_worker.snapshot().as_dict(),
    }


@router.post("/memory/retry")
def retry_memory_learning(request: Request) -> dict:
    queued = request.app.state.memory_worker.retry_failed()
    return {"queued": queued}


@router.get("/memories")
def list_memories(
    request: Request,
    workspace_id: str | None = Query(default=None),
    include_global: bool = Query(default=True),
    include_inactive: bool = Query(default=False),
) -> list[dict[str, Any]]:
    items = request.app.state.memory_service.list_memories(
        workspace_id=workspace_id,
        include_global=include_global,
        include_inactive=include_inactive,
    )
    return [_memory_payload(item) for item in items]


@router.get("/memories/search")
def search_memories(
    request: Request,
    query: str = Query(min_length=1, max_length=2000),
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=8, ge=1, le=50),
) -> dict:
    results = request.app.state.memory_service.retrieve(
        workspace_id=workspace_id,
        query=query,
        limit=limit,
    )
    return {"query": query, "count": len(results), "results": results}


@router.post("/memories", status_code=status.HTTP_201_CREATED)
def create_memory(payload: MemoryCreate, request: Request) -> dict[str, Any]:
    try:
        item = request.app.state.memory_service.create_manual(
            workspace_id=payload.workspace_id,
            type=payload.type,
            subject=payload.subject,
            predicate=payload.predicate,
            value=payload.value,
            importance=payload.importance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _memory_payload(item)


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: str, payload: MemoryUpdate, request: Request) -> dict[str, Any]:
    try:
        item = request.app.state.memory_service.update_manual(
            memory_id,
            **payload.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_payload(item)


@router.delete("/memories/{memory_id}")
def deactivate_memory(memory_id: str, request: Request) -> dict[str, Any]:
    item = request.app.state.memory_service.deactivate(memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_payload(item)


@router.get("/memories/{memory_id}/versions")
def memory_versions(memory_id: str, request: Request) -> list[dict[str, Any]]:
    items = request.app.state.memory_service.versions(memory_id)
    return [
        {
            "id": item.id,
            "memory_id": item.memory_id,
            "value": item.value_json,
            "reason": item.reason,
            "created_at": item.created_at.isoformat(),
        }
        for item in items
    ]
