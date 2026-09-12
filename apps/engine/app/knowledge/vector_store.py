from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import lancedb

LEGACY_TABLE_NAME = "chunks"


def _table_name(provider_signature: str) -> str:
    if provider_signature == "local-hash-384-v1":
        return LEGACY_TABLE_NAME
    digest = hashlib.sha256(provider_signature.encode("utf-8")).hexdigest()[:16]
    return f"chunks_{digest}"


class LanceVectorStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(path))

    def _table_names(self) -> set[str]:
        return set(self.db.table_names())

    def replace_file(
        self,
        file_id: str,
        rows: list[dict[str, Any]],
        *,
        provider_signature: str = "local-hash-384-v1",
    ) -> None:
        table_name = _table_name(provider_signature)
        names = self._table_names()
        if table_name in names:
            table = self.db.open_table(table_name)
            table.delete(f"file_id = '{file_id}'")
            if rows:
                table.add(rows)
        elif rows:
            self.db.create_table(table_name, data=rows, mode="create")

    def search(
        self,
        workspace_id: str,
        vector: list[float],
        limit: int,
        *,
        provider_signature: str = "local-hash-384-v1",
    ) -> list[dict[str, Any]]:
        table_name = _table_name(provider_signature)
        if table_name not in self._table_names():
            return []
        table = self.db.open_table(table_name)
        try:
            query = table.search(vector, vector_column_name="vector")
        except TypeError:
            query = table.search(vector)
        rows = query.where(f"workspace_id = '{workspace_id}'").limit(limit).to_list()
        return list(rows)

    def has_provider_index(self, provider_signature: str) -> bool:
        return _table_name(provider_signature) in self._table_names()
