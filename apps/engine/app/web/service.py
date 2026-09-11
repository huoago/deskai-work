from __future__ import annotations

import asyncio
import re
from typing import Any

from app.api.settings import DEFAULTS
from app.database.models import Setting
from app.database.session import Database

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S{8,}"),
)


class WebResearchService:
    def __init__(self, database: Database, secret_store, provider) -> None:
        self.database = database
        self.secret_store = secret_store
        self.provider = provider

    def search(self, *, query: str, max_sources: int = 6) -> dict[str, Any]:
        normalized_query = " ".join(str(query or "").split())
        if not normalized_query:
            raise ValueError("Web search query is required")
        if len(normalized_query) > 500:
            raise ValueError("Web search query exceeds the 500 character limit")
        if any(pattern.search(normalized_query) for pattern in _SECRET_PATTERNS):
            raise ValueError("Web search query appears to contain credential-like secret material")

        settings = dict(DEFAULTS)
        with self.database.session() as session:
            for key in ("privacy_mode", "default_model", "reasoning_level"):
                item = session.get(Setting, key)
                if item is not None:
                    settings[key] = item.value_json

        if str(settings["privacy_mode"]) == "local":
            raise ValueError("Web research is unavailable in Local Only mode")

        api_key = self.secret_store.get_openai_api_key()
        if not api_key:
            raise ValueError("Web research requires a configured OpenAI API key")

        source_limit = max(1, min(int(max_sources), 10))
        result = asyncio.run(
            self.provider.web_search(
                api_key=api_key,
                model=str(settings["default_model"]),
                reasoning_effort=str(settings["reasoning_level"]),
                query=normalized_query,
                max_sources=source_limit,
            )
        )
        sources = result.get("sources")
        if not isinstance(sources, list):
            raise ValueError("Web research provider returned an invalid sources payload")
        result["sources"] = sources[:source_limit]
        return result
