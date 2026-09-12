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
    parser_worker_enabled: bool = True
    parser_worker_interval_seconds: float = 0.75
    knowledge_worker_enabled: bool = True
    knowledge_worker_interval_seconds: float = 1.0
    memory_worker_enabled: bool = True
    memory_worker_interval_seconds: float = 1.25
    agent_worker_enabled: bool = True
    agent_worker_interval_seconds: float = 1.0
    work_plan_worker_enabled: bool = True
    work_plan_worker_interval_seconds: float = 1.0

    @classmethod
    def load(cls, *, session_token: str | None = None) -> "Settings":
        return cls(
            data_dir=default_data_dir(),
            session_token=session_token or os.environ.get("DESKAI_SESSION_TOKEN"),
            watcher_enabled=os.environ.get("DESKAI_WATCHER_ENABLED", "1") not in {"0", "false", "False"},
            watcher_interval_seconds=max(float(os.environ.get("DESKAI_WATCHER_INTERVAL", "2.0")), 0.5),
            parser_worker_enabled=os.environ.get("DESKAI_PARSER_WORKER_ENABLED", "1")
            not in {"0", "false", "False"},
            parser_worker_interval_seconds=max(
                float(os.environ.get("DESKAI_PARSER_WORKER_INTERVAL", "0.75")), 0.25
            ),
            knowledge_worker_enabled=os.environ.get("DESKAI_KNOWLEDGE_WORKER_ENABLED", "1")
            not in {"0", "false", "False"},
            knowledge_worker_interval_seconds=max(
                float(os.environ.get("DESKAI_KNOWLEDGE_WORKER_INTERVAL", "1.0")), 0.25
            ),
            memory_worker_enabled=os.environ.get("DESKAI_MEMORY_WORKER_ENABLED", "1")
            not in {"0", "false", "False"},
            memory_worker_interval_seconds=max(
                float(os.environ.get("DESKAI_MEMORY_WORKER_INTERVAL", "1.25")), 0.5
            ),
            agent_worker_enabled=os.environ.get("DESKAI_AGENT_WORKER_ENABLED", "1")
            not in {"0", "false", "False"},
            agent_worker_interval_seconds=max(
                float(os.environ.get("DESKAI_AGENT_WORKER_INTERVAL", "1.0")), 0.5
            ),
            work_plan_worker_enabled=os.environ.get(
                "DESKAI_WORK_PLAN_WORKER_ENABLED",
                "1",
            )
            not in {"0", "false", "False"},
            work_plan_worker_interval_seconds=max(
                float(os.environ.get("DESKAI_WORK_PLAN_WORKER_INTERVAL", "1.0")),
                0.5,
            ),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "data" / "deskai.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"

    @property
    def vector_path(self) -> Path:
        return self.data_dir / "vectors" / "lancedb"

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
