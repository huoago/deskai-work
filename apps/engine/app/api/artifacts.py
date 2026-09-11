from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.database.models import GeneratedArtifact

router = APIRouter(tags=["artifacts"])


def artifact_payload(item: GeneratedArtifact) -> dict[str, Any]:
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


@router.get("/artifacts")
def list_artifacts(
    request: Request,
    workspace_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    with request.app.state.database.session() as session:
        statement = select(GeneratedArtifact).order_by(GeneratedArtifact.created_at.desc()).limit(limit)
        if workspace_id:
            statement = statement.where(GeneratedArtifact.workspace_id == workspace_id)
        if task_id:
            statement = statement.where(GeneratedArtifact.task_id == task_id)
        return [artifact_payload(item) for item in session.scalars(statement).all()]


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.database.session() as session:
        item = session.get(GeneratedArtifact, artifact_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return artifact_payload(item)
