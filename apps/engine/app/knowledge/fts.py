from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database.models import Chunk, File

CJK_RE = re.compile(r"[\u3400-\u9fff]+")


def replace_file_fts(
    session: Session,
    *,
    old_chunk_ids: list[str],
    workspace_id: str,
    filename: str,
    chunks: list[Chunk],
) -> None:
    if old_chunk_ids:
        session.execute(
            text("DELETE FROM chunks_fts_v2 WHERE chunk_id = :chunk_id"),
            [{"chunk_id": chunk_id} for chunk_id in old_chunk_ids],
        )
    if chunks:
        session.execute(
            text(
                """
                INSERT INTO chunks_fts_v2(chunk_id, workspace_id, filename, section, content)
                VALUES (:chunk_id, :workspace_id, :filename, :section, :content)
                """
            ),
            [
                {
                    "chunk_id": chunk.id,
                    "workspace_id": workspace_id,
                    "filename": filename,
                    "section": chunk.section_title or "",
                    "content": chunk.content,
                }
                for chunk in chunks
            ],
        )


def _expression(query: str) -> str:
    query = " ".join(query.split())
    cjk_segments = CJK_RE.findall(query)
    terms: list[str] = []
    for segment in cjk_segments:
        if len(segment) >= 3:
            terms.extend(segment[index:index + 3] for index in range(min(len(segment) - 2, 10)))
        elif segment:
            terms.append(segment)
    non_cjk = CJK_RE.sub(" ", query)
    terms.extend(term for term in re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ_./:+-]+", non_cjk) if len(term) >= 2)
    if not terms:
        terms = [query]
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:16] if term)


def search_fts(session: Session, workspace_id: str, query: str, limit: int) -> list[tuple[str, float]]:
    expression = _expression(query)
    try:
        rows = session.execute(
            text(
                """
                SELECT chunk_id, bm25(chunks_fts_v2) AS rank
                FROM chunks_fts_v2
                WHERE chunks_fts_v2 MATCH :query
                  AND workspace_id = :workspace_id
                ORDER BY rank
                LIMIT :limit
                """
            ),
            {"query": expression, "workspace_id": workspace_id, "limit": limit},
        ).all()
        if rows:
            return [(str(row[0]), float(row[1])) for row in rows]
    except OperationalError:
        pass

    # Safe fallback for very short queries or older FTS tokenizer behavior.
    pattern = f"%{query}%"
    rows = session.execute(
        text(
            """
            SELECT chunks.id
            FROM chunks
            JOIN files ON files.id = chunks.file_id
            WHERE chunks.workspace_id = :workspace_id
              AND chunks.active = 1
              AND files.status = 'indexed'
              AND chunks.file_version_id = files.current_version_id
              AND (chunks.content LIKE :pattern OR chunks.section_title LIKE :pattern OR files.filename LIKE :pattern)
            LIMIT :limit
            """
        ),
        {"workspace_id": workspace_id, "pattern": pattern, "limit": limit},
    ).all()
    return [(str(row[0]), float(index)) for index, row in enumerate(rows, start=1)]
