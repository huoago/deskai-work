from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedUnit:
    kind: str
    text: str
    locator: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParsedDocument:
    parser: str
    parser_version: str
    file_type: str
    title: str | None
    text: str
    units: list[ParsedUnit]
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "file_type": self.file_type,
            "title": self.title,
            "text": self.text,
            "units": [unit.as_dict() for unit in self.units],
            "metadata": self.metadata,
        }
