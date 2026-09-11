from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(
        Settings(
            data_dir=tmp_path / "DeskAI",
            watcher_enabled=False,
            parser_worker_enabled=False,
            knowledge_worker_enabled=False,
            memory_worker_enabled=False,
            agent_worker_enabled=False,
        )
    )
    with TestClient(app) as test_client:
        yield test_client
