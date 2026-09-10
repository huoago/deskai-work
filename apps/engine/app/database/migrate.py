from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def project_engine_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def run_migrations(database_url: str) -> None:
    engine_dir = project_engine_dir()
    config = Config(str(engine_dir / "alembic.ini"))
    config.set_main_option("script_location", str(engine_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
