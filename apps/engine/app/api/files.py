from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.database.models import File, IndexJob

router = APIRouter(tags=["files"])


@router.get("/files")
def list_files(request: Request, workspace_id: str = Query(...)) -> list[dict]:
    with request.app.state.database.session() as session:
        records = session.scalars(
            select(File).where(File.workspace_id == workspace_id).order_by(File.filename)
        ).all()
        active_jobs = {
            job.file_id: job
            for job in session.scalars(
                select(IndexJob).where(
                    IndexJob.workspace_id == workspace_id,
                    IndexJob.status.in_(["queued", "processing"]),
                )
            ).all()
            if job.file_id
        }
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
                "queue_status": active_jobs[item.id].status if item.id in active_jobs else None,
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
        result = []
        for item in records:
            file = session.get(File, item.file_id) if item.file_id else None
            result.append(
                {
                    "id": item.id,
                    "file_id": item.file_id,
                    "filename": file.filename if file else None,
                    "path": file.path if file else None,
                    "sha256": file.sha256 if file else None,
                    "status": item.status,
                    "priority": item.priority,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
        return result


@router.get("/index-jobs/summary")
def index_job_summary(request: Request, workspace_id: str = Query(...)) -> dict:
    with request.app.state.database.session() as session:
        rows = session.execute(
            select(IndexJob.status, func.count(IndexJob.id))
            .where(IndexJob.workspace_id == workspace_id)
            .group_by(IndexJob.status)
        ).all()
        counts = {status: count for status, count in rows}
        return {
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "total": sum(counts.values()),
        }
