from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ParsedDocumentCache:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "cache" / "document"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / f"{sha256}.json"

    def write(self, sha256: str, payload: dict[str, Any]) -> Path:
        target = self.path_for(sha256)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, target)
        return target

    def read(self, sha256: str) -> dict[str, Any] | None:
        target = self.path_for(sha256)
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))
