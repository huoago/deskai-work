from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.database.models import File, IndexJob

router = APIRouter(tags=["files"])


@router.get("/files")
def list_files(request: Request, workspace_id: str = Query(...)) -> list[dict]:
    with request.app.state.database.session() as session:
        records = session.scalars(
            select(File).where(File.workspace_id == workspace_id).order_by(File.filename)
        ).all()
        return [
            {
                "id": item.id,
                "workspace_id": item.workspace_id,
                "path": item.path,
                "filename": item.filename,
                "extension": item.extension,
                "mime_type": item.mime_type,
                "size": item.size,
                "sha256": item.sha256,
                "status": item.status,
                "modified_at": item.modified_at,
            }
            for item in records
        ]


@router.get("/index-jobs")
def list_index_jobs(request: Request, workspace_id: str = Query(...)) -> list[dict]:
    with request.app.state.database.session() as session:
        records = session.scalars(
            select(IndexJob)
            .where(IndexJob.workspace_id == workspace_id)
            .order_by(IndexJob.priority, IndexJob.created_at)
        ).all()
        return [
            {
                "id": item.id,
                "file_id": item.file_id,
                "status": item.status,
                "priority": item.priority,
                "error_code": item.error_code,
                "error_message": item.error_message,
            }
            for item in records
        ]
