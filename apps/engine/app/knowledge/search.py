from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.database.models import Chunk, File
from app.database.session import Database
from app.knowledge.embedding import EmbeddingError, EmbeddingService
from app.knowledge.fts import search_fts
from app.knowledge.vector_store import LanceVectorStore

MIN_VECTOR_SIMILARITY = 0.18
RRF_K = 60.0
TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ_./:+-]+|[\u3400-\u9fff]")


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
    vector_similarity: float | None
    rerank_score: float
    embedding_provider: str

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
            "vector_similarity": None if self.vector_similarity is None else round(self.vector_similarity, 6),
            "rerank_score": round(self.rerank_score, 6),
            "embedding_provider": self.embedding_provider,
        }


class HybridSearch:
    def __init__(
        self,
        database: Database,
        vector_store: LanceVectorStore,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.database = database
        self.vector_store = vector_store
        self.embedding_service = embedding_service or EmbeddingService()

    def search(self, workspace_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query = " ".join(query.split())
        if not query:
            return []
        candidate_limit = max(limit * 5, 30)

        with self.database.session() as session:
            lexical = search_fts(session, workspace_id, query, candidate_limit)

        descriptor = self.embedding_service.descriptor()
        query_vector: list[float] | None = None
        vector_rows: list[dict[str, Any]] = []
        try:
            query_vector = self.embedding_service.embed_query(query)
            vector_rows = self.vector_store.search(
                workspace_id,
                query_vector,
                candidate_limit,
                provider_signature=descriptor.signature,
            )
        except EmbeddingError:
            # Retrieval fails soft to authoritative FTS when a cloud semantic
            # provider is unavailable; callers still receive grounded local hits.
            query_vector = None
            vector_rows = []

        lexical_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(lexical, start=1)}
        vector_rank: dict[str, int] = {}
        vector_by_chunk: dict[str, list[float]] = {}
        for rank, row in enumerate(vector_rows, start=1):
            chunk_id = str(row.get("chunk_id") or "")
            vector = row.get("vector")
            if chunk_id and chunk_id not in vector_rank:
                vector_rank[chunk_id] = rank
                if vector is not None and not isinstance(vector, (str, bytes, dict)):
                    try:
                        vector_by_chunk[chunk_id] = [float(item) for item in vector]
                    except (TypeError, ValueError):
                        pass

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
        query_terms = _terms(query)
        for chunk, file in rows:
            l_rank = lexical_rank.get(chunk.id)
            v_rank = vector_rank.get(chunk.id)
            similarity: float | None = None
            candidate_vector = vector_by_chunk.get(chunk.id)
            if query_vector is not None and candidate_vector is not None:
                similarity = _cosine_similarity(query_vector, candidate_vector)
            if l_rank is None and (similarity is None or similarity < MIN_VECTOR_SIMILARITY):
                continue

            score = 0.0
            if l_rank is not None:
                score += 1.0 / (RRF_K + l_rank)
            if v_rank is not None and similarity is not None and similarity >= MIN_VECTOR_SIMILARITY:
                score += 0.9 / (RRF_K + v_rank)
                score += similarity * 0.01

            content_lower = chunk.content.lower()
            filename_lower = file.filename.lower()
            section_lower = (chunk.section_title or "").lower()
            if query_lower in content_lower:
                score += 0.012
            if query_lower in filename_lower:
                score += 0.008
            if query_lower and query_lower in section_lower:
                score += 0.006

            rerank_score = _rerank(query_terms, chunk.content, file.filename, chunk.section_title)
            score += rerank_score * 0.012

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
                    vector_similarity=similarity,
                    rerank_score=rerank_score,
                    embedding_provider=descriptor.signature,
                )
            )

        hits.sort(key=lambda item: (-item.score, item.filename.lower(), item.chunk_id))
        return [hit.as_dict() for hit in hits[:limit]]


def _terms(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(text) if match.group(0).strip()}


def _rerank(query_terms: set[str], content: str, filename: str, section: str | None) -> float:
    if not query_terms:
        return 0.0
    content_terms = _terms(content)
    filename_terms = _terms(filename)
    section_terms = _terms(section or "")
    overlap = len(query_terms & content_terms) / len(query_terms)
    filename_overlap = len(query_terms & filename_terms) / len(query_terms)
    section_overlap = len(query_terms & section_terms) / len(query_terms)
    return min(1.0, overlap * 0.72 + filename_overlap * 0.18 + section_overlap * 0.10)


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


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
