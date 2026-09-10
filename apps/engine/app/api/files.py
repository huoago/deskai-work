from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.database.models import File, FileVersion, IndexJob

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
        result: list[dict] = []
        for item in records:
            version = session.get(FileVersion, item.current_version_id) if item.current_version_id else None
            result.append(
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
                    "current_version_id": item.current_version_id,
                    "parser_version": version.parser_version if version else None,
                    "queue_status": active_jobs[item.id].status if item.id in active_jobs else None,
                }
            )
        return result


@router.get("/files/{file_id}/parsed")
def parsed_file_preview(
    file_id: str,
    request: Request,
    max_chars: int = Query(default=20000, ge=1000, le=100000),
    max_units: int = Query(default=30, ge=1, le=200),
) -> dict:
    with request.app.state.database.session() as session:
        file = session.get(File, file_id)
        if file is None:
            raise HTTPException(status_code=404, detail="File not found")
        version = session.get(FileVersion, file.current_version_id) if file.current_version_id else None
        if file.status not in {"parsed", "indexed"} or version is None:
            raise HTTPException(status_code=409, detail="File has not completed Phase 3 parsing")
        payload = request.app.state.parser_worker.cache.read(version.sha256)
        if payload is None:
            raise HTTPException(status_code=404, detail="Parsed cache is missing")

    text = str(payload.get("text") or "")
    units = list(payload.get("units") or [])
    return {
        "file_id": file_id,
        "filename": file.filename,
        "sha256": version.sha256,
        "parser": payload.get("parser"),
        "parser_version": payload.get("parser_version"),
        "file_type": payload.get("file_type"),
        "title": payload.get("title"),
        "metadata": payload.get("metadata") or {},
        "text": text[:max_chars],
        "text_truncated": len(text) > max_chars,
        "units": units[:max_units],
        "units_truncated": len(units) > max_units,
    }


@router.get("/parser/status")
def parser_status(request: Request) -> dict:
    return request.app.state.parser_worker.snapshot().as_dict()


@router.post("/parser/process")
def process_parser_queue(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    processed = request.app.state.parser_worker.process_available(limit=limit)
    return {"processed": processed, "status": request.app.state.parser_worker.snapshot().as_dict()}


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
