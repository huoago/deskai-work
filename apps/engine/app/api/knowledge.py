from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.database.models import Chunk, File
from app.knowledge.embedding import EmbeddingError

router = APIRouter(tags=["knowledge"])


class SearchRequest(BaseModel):
    workspace_id: str
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=50)


@router.get("/knowledge/status")
def knowledge_status(request: Request, workspace_id: str | None = Query(default=None)) -> dict:
    snapshot = request.app.state.knowledge_indexer.snapshot().as_dict()
    descriptor = request.app.state.embedding_service.descriptor()
    with request.app.state.database.session() as session:
        file_query = select(File.status, func.count(File.id)).group_by(File.status)
        chunk_query = select(func.count(Chunk.id)).where(Chunk.active.is_(True))
        if workspace_id:
            file_query = file_query.where(File.workspace_id == workspace_id)
            chunk_query = chunk_query.where(Chunk.workspace_id == workspace_id)
        rows = session.execute(file_query).all()
        counts = {status: count for status, count in rows}
        active_chunks = int(session.scalar(chunk_query) or 0)

    snapshot.update(
        {
            "workspace_id": workspace_id,
            "parsed_files": counts.get("parsed", 0),
            "indexed_files": counts.get("indexed", 0),
            "index_failed_files": counts.get("index_failed", 0),
            "active_chunks": active_chunks,
            "embedding": descriptor.as_dict(),
            "vector_index_ready": request.app.state.knowledge_indexer.vector_store.has_provider_index(
                descriptor.signature
            ),
        }
    )
    return snapshot


@router.get("/knowledge/embeddings/local-model")
def local_embedding_model_status(request: Request) -> dict:
    try:
        return request.app.state.embedding_service.local_model_status()
    except EmbeddingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/knowledge/embeddings/local-model/download")
def download_local_embedding_model(request: Request) -> dict:
    try:
        return request.app.state.embedding_service.install_local_model()
    except EmbeddingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/knowledge/embeddings/local-model")
def delete_local_embedding_model(request: Request) -> dict:
    try:
        return request.app.state.embedding_service.remove_local_model()
    except EmbeddingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/knowledge/process")
def process_knowledge_queue(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    processed = request.app.state.knowledge_indexer.process_available(limit=limit)
    return {
        "processed": processed,
        "status": request.app.state.knowledge_indexer.snapshot().as_dict(),
    }


@router.post("/knowledge/embeddings/rebuild")
def rebuild_embeddings(
    request: Request,
    workspace_id: str | None = Query(default=None),
    file_limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    try:
        return request.app.state.knowledge_indexer.rebuild_embeddings(
            workspace_id=workspace_id,
            file_limit=file_limit,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/knowledge/files/{file_id}/retry")
def retry_knowledge_file(file_id: str, request: Request) -> dict:
    if not request.app.state.knowledge_indexer.retry_file(file_id):
        raise HTTPException(status_code=409, detail="File is not eligible for knowledge-index retry")
    return {"queued": True, "file_id": file_id}


@router.post("/search")
def search_knowledge(payload: SearchRequest, request: Request) -> dict:
    results = request.app.state.hybrid_search.search(
        payload.workspace_id,
        payload.query,
        payload.limit,
    )
    return {
        "workspace_id": payload.workspace_id,
        "query": payload.query,
        "count": len(results),
        "results": results,
    }
