from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb

TABLE_NAME = "chunks"


class LanceVectorStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(path))

    def _table_names(self) -> set[str]:
        return set(self.db.table_names())

    def replace_file(self, file_id: str, rows: list[dict[str, Any]]) -> None:
        names = self._table_names()
        if TABLE_NAME in names:
            table = self.db.open_table(TABLE_NAME)
            table.delete(f"file_id = '{file_id}'")
            if rows:
                table.add(rows)
        elif rows:
            self.db.create_table(TABLE_NAME, data=rows, mode="create")

    def search(self, workspace_id: str, vector: list[float], limit: int) -> list[dict[str, Any]]:
        if TABLE_NAME not in self._table_names():
            return []
        table = self.db.open_table(TABLE_NAME)
        try:
            query = table.search(vector, vector_column_name="vector")
        except TypeError:
            query = table.search(vector)
        rows = (
            query.where(f"workspace_id = '{workspace_id}'")
            .limit(limit)
            .to_list()
        )
        return list(rows)
