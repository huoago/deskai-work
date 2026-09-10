from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.database.models import Chunk, File
from app.database.session import Database
from app.knowledge.embedding import EMBEDDING_PROVIDER, embed_text
from app.knowledge.fts import search_fts
from app.knowledge.vector_store import LanceVectorStore


@dataclass(slots=True)
class SearchHit:
    chunk_id: str
    file_id: str
    filename: str
    score: float
    content: str
    snippet: str
    locator: dict[str, Any]
    citation_label: str
    lexical_rank: int | None
    vector_rank: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "file_id": self.file_id,
            "filename": self.filename,
            "score": round(self.score, 8),
            "content": self.content,
            "snippet": self.snippet,
            "locator": self.locator,
            "citation_label": self.citation_label,
            "lexical_rank": self.lexical_rank,
            "vector_rank": self.vector_rank,
            "embedding_provider": EMBEDDING_PROVIDER,
        }


class HybridSearch:
    def __init__(self, database: Database, vector_store: LanceVectorStore) -> None:
        self.database = database
        self.vector_store = vector_store

    def search(self, workspace_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query = " ".join(query.split())
        if not query:
            return []
        candidate_limit = max(limit * 5, 30)

        with self.database.session() as session:
            lexical = search_fts(session, workspace_id, query, candidate_limit)

        vector_rows = self.vector_store.search(
            workspace_id,
            embed_text(query),
            candidate_limit,
        )

        lexical_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(lexical, start=1)}
        vector_rank: dict[str, int] = {}
        for rank, row in enumerate(vector_rows, start=1):
            chunk_id = str(row.get("chunk_id") or "")
            if chunk_id and chunk_id not in vector_rank:
                vector_rank[chunk_id] = rank

        candidate_ids = set(lexical_rank) | set(vector_rank)
        if not candidate_ids:
            return []

        with self.database.session() as session:
            rows = session.execute(
                select(Chunk, File)
                .join(File, File.id == Chunk.file_id)
                .where(
                    Chunk.id.in_(candidate_ids),
                    Chunk.workspace_id == workspace_id,
                    Chunk.active.is_(True),
                    File.workspace_id == workspace_id,
                    File.status == "indexed",
                    Chunk.file_version_id == File.current_version_id,
                )
            ).all()

        hits: list[SearchHit] = []
        query_lower = query.lower()
        for chunk, file in rows:
            l_rank = lexical_rank.get(chunk.id)
            v_rank = vector_rank.get(chunk.id)
            score = 0.0
            if l_rank is not None:
                score += 1.0 / (60.0 + l_rank)
            if v_rank is not None:
                score += 0.85 / (60.0 + v_rank)

            content_lower = chunk.content.lower()
            filename_lower = file.filename.lower()
            if query_lower in content_lower:
                score += 0.01
            if query_lower in filename_lower:
                score += 0.006

            locator = _locator(chunk)
            hits.append(
                SearchHit(
                    chunk_id=chunk.id,
                    file_id=file.id,
                    filename=file.filename,
                    score=score,
                    content=chunk.content,
                    snippet=_snippet(chunk.content, query),
                    locator=locator,
                    citation_label=_citation_label(file.filename, locator),
                    lexical_rank=l_rank,
                    vector_rank=v_rank,
                )
            )

        hits.sort(key=lambda item: (-item.score, item.filename.lower(), item.chunk_id))
        return [hit.as_dict() for hit in hits[:limit]]


def _locator(chunk: Chunk) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    if chunk.page_start is not None:
        locator["page_start"] = chunk.page_start
        locator["page_end"] = chunk.page_end or chunk.page_start
    if chunk.sheet_name:
        locator["sheet_name"] = chunk.sheet_name
    if chunk.cell_range:
        locator["cell_range"] = chunk.cell_range
    if chunk.slide_start is not None:
        locator["slide_start"] = chunk.slide_start
        locator["slide_end"] = chunk.slide_end or chunk.slide_start
    if chunk.section_title:
        locator["section_title"] = chunk.section_title
    return locator


def _citation_label(filename: str, locator: dict[str, Any]) -> str:
    parts = [filename]
    if "page_start" in locator:
        start = locator["page_start"]
        end = locator.get("page_end", start)
        parts.append(f"p.{start}" if start == end else f"pp.{start}-{end}")
    elif "sheet_name" in locator:
        detail = str(locator["sheet_name"])
        if locator.get("cell_range"):
            detail += f" · {locator['cell_range']}"
        parts.append(detail)
    elif "slide_start" in locator:
        start = locator["slide_start"]
        end = locator.get("slide_end", start)
        parts.append(f"slide {start}" if start == end else f"slides {start}-{end}")
    elif locator.get("section_title"):
        parts.append(str(locator["section_title"]))
    return " · ".join(parts)


def _snippet(content: str, query: str, width: int = 360) -> str:
    if len(content) <= width:
        return content
    position = content.lower().find(query.lower())
    if position < 0:
        return content[:width].rstrip() + "…"
    start = max(position - width // 3, 0)
    end = min(start + width, len(content))
    prefix = "…" if start else ""
    suffix = "…" if end < len(content) else ""
    return prefix + content[start:end].strip() + suffix
