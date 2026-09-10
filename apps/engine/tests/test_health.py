from __future__ import annotations

import sqlite3


def test_health_reports_sqlite_ready(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["app"] == "deskai-engine"


def test_migration_creates_required_tables_and_fts5(client):
    database_path = client.get("/health").json()["database_path"]
    connection = sqlite3.connect(database_path)
    try:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
    finally:
        connection.close()

    required = {
        "workspaces",
        "workspace_roots",
        "files",
        "file_versions",
        "chunks",
        "entities",
        "entity_aliases",
        "relations",
        "memories",
        "memory_versions",
        "conversations",
        "messages",
        "tasks",
        "agent_runs",
        "tool_calls",
        "citations",
        "index_jobs",
        "permissions",
        "settings",
        "audit_logs",
        "chunks_fts",
    }
    assert required.issubset(names)
