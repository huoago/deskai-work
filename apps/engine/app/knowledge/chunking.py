from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

MAX_CHARS = 1600
OVERLAP_CHARS = 180
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_]+|[\u3400-\u9fff]")


@dataclass(slots=True)
class ChunkDraft:
    content: str
    page_start: int | None = None
    page_end: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    slide_start: int | None = None
    slide_end: int | None = None
    section_title: str | None = None
    language: str | None = None
    token_count: int = 0
    content_hash: str = ""


def _split(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= MAX_CHARS:
        return [text]

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHARS, len(text))
        if end < len(text):
            candidates = [
                text.rfind("\n\n", start + MAX_CHARS // 2, end),
                text.rfind("\n", start + MAX_CHARS // 2, end),
                text.rfind("。", start + MAX_CHARS // 2, end),
                text.rfind(". ", start + MAX_CHARS // 2, end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return parts


def _language(text: str) -> str:
    if not text:
        return "und"
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    if cjk / max(len(text), 1) >= 0.08:
        return "zh"
    return "und"


def _section(unit: dict[str, Any]) -> str | None:
    locator = unit.get("locator") or {}
    metadata = unit.get("metadata") or {}
    style = str(metadata.get("style") or "")
    if style.lower().startswith("heading"):
        value = str(unit.get("text") or "").strip()
        return value[:180] if value else style
    if "paragraph" in locator:
        return f"Paragraph {locator['paragraph']}"
    if "line_start" in locator:
        return f"Lines {locator['line_start']}-{locator.get('line_end', locator['line_start'])}"
    if unit.get("kind") == "csv_rows":
        return f"Rows {locator.get('row_start', '?')}-{locator.get('row_end', '?')}"
    if unit.get("kind") == "image":
        return "Image metadata"
    return None


def build_chunks(parsed: dict[str, Any]) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    units = list(parsed.get("units") or [])
    if not units and parsed.get("text"):
        units = [{"kind": "document", "text": parsed["text"], "locator": {}, "metadata": {}}]

    for unit in units:
        locator = unit.get("locator") or {}
        for piece in _split(str(unit.get("text") or "")):
            page = locator.get("page")
            slide = locator.get("slide")
            sheet = locator.get("sheet")
            cell_range = None
            if "row_start" in locator:
                cell_range = f"rows {locator['row_start']}-{locator.get('row_end', locator['row_start'])}"
            drafts.append(
                ChunkDraft(
                    content=piece,
                    page_start=int(page) if page is not None else None,
                    page_end=int(page) if page is not None else None,
                    sheet_name=str(sheet) if sheet is not None else None,
                    cell_range=cell_range,
                    slide_start=int(slide) if slide is not None else None,
                    slide_end=int(slide) if slide is not None else None,
                    section_title=_section(unit),
                    language=_language(piece),
                    token_count=len(TOKEN_RE.findall(piece)),
                    content_hash=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                )
            )
    return drafts
