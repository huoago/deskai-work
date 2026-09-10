from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_data_dir() -> Path:
    configured = os.environ.get("DESKAI_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "DeskAI"
    return Path.home() / ".local" / "share" / "DeskAI"


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    session_token: str | None = None
    watcher_enabled: bool = True
    watcher_interval_seconds: float = 2.0

    @classmethod
    def load(cls, *, session_token: str | None = None) -> "Settings":
        return cls(
            data_dir=default_data_dir(),
            session_token=session_token or os.environ.get("DESKAI_SESSION_TOKEN"),
            watcher_enabled=os.environ.get("DESKAI_WATCHER_ENABLED", "1") not in {"0", "false", "False"},
            watcher_interval_seconds=max(float(os.environ.get("DESKAI_WATCHER_INTERVAL", "2.0")), 0.5),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "data" / "deskai.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"

    def ensure_directories(self) -> None:
        for child in (
            "data",
            "vectors/lancedb",
            "models",
            "cache/document",
            "cache/preview",
            "cache/image",
            "cache/temp",
            "exports",
            "generated",
            "logs",
            "backups",
            "config",
            "sandbox",
        ):
            (self.data_dir / child).mkdir(parents=True, exist_ok=True)
