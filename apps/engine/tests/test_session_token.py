from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_session_token_protects_loopback_api(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path / "DeskAI", session_token="secret-token"))
    with TestClient(app) as client:
        denied = client.get("/health")
        assert denied.status_code == 401
        assert denied.json()["code"] == "INVALID_SESSION_TOKEN"

        allowed = client.get("/health", headers={"X-DeskAI-Token": "secret-token"})
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "ok"
