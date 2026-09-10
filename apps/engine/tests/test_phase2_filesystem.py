from __future__ import annotations

from pathlib import Path


def _workspace(client) -> str:
    response = client.post("/workspaces", json={"name": "Phase 2 Test"})
    assert response.status_code == 201
    return response.json()["id"]


def test_watcher_run_once_discovers_hashes_and_queues_new_file(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "watched"
    source.mkdir()
    note = source / "note.txt"
    note.write_text("phase two watcher", encoding="utf-8")

    added = client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "scan_now": False, "watch_enabled": True},
    )
    assert added.status_code == 201

    summary = client.app.state.workspace_watcher.run_once()
    assert summary["discovered"] == 1
    assert summary["queued"] == 1

    files = client.get("/files", params={"workspace_id": workspace_id}).json()
    assert len(files) == 1
    assert files[0]["filename"] == "note.txt"
    assert len(files[0]["sha256"]) == 64
    assert files[0]["queue_status"] == "queued"


def test_watcher_honors_auto_index_setting_and_manual_scan_can_queue(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "manual-only"
    source.mkdir()
    (source / "manual.md").write_text("manual indexing", encoding="utf-8")

    client.patch("/settings", json={"auto_index": False})
    client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "scan_now": False, "watch_enabled": True},
    )

    summary = client.app.state.workspace_watcher.run_once()
    assert summary["discovered"] == 1
    assert summary["queued"] == 0

    files = client.get("/files", params={"workspace_id": workspace_id}).json()
    assert files[0]["status"] == "pending"
    assert files[0]["queue_status"] is None
    assert client.get("/index-jobs", params={"workspace_id": workspace_id}).json() == []

    manual = client.post(f"/workspaces/{workspace_id}/scan").json()
    assert manual["summary"]["queued"] == 1
    jobs = client.get("/index-jobs", params={"workspace_id": workspace_id}).json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"


def test_root_watch_toggle_and_revoke_cancel_pending_jobs(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "source"
    source.mkdir()
    (source / "drawing.pdf").write_bytes(b"%PDF-phase2-fixture")

    added = client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "scan_now": True, "watch_enabled": True},
    ).json()
    root_id = added["root"]["id"]

    updated = client.patch(
        f"/workspaces/{workspace_id}/roots/{root_id}",
        json={"watch_enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["root"]["watch_enabled"] is False

    watcher = client.get(f"/workspaces/{workspace_id}/watcher").json()
    assert watcher["workspace_watched_roots"] == 0

    revoked = client.delete(f"/workspaces/{workspace_id}/roots/{root_id}")
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
    assert revoked.json()["revoked_files"] == 1

    files = client.get("/files", params={"workspace_id": workspace_id}).json()
    assert files[0]["status"] == "revoked"
    jobs = client.get("/index-jobs", params={"workspace_id": workspace_id}).json()
    assert jobs[0]["status"] == "cancelled"


def test_phase2_acceptance_extensions_do_not_expand_to_webp(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "formats"
    source.mkdir()
    (source / "photo.png").write_bytes(b"png-fixture")
    (source / "photo.webp").write_bytes(b"webp-fixture")

    scan = client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "scan_now": True},
    ).json()["scan"]
    assert scan["queued"] == 1
    assert scan["unsupported"] == 1

    files = client.get("/files", params={"workspace_id": workspace_id}).json()
    statuses = {item["filename"]: item["status"] for item in files}
    assert statuses == {"photo.png": "pending", "photo.webp": "unsupported"}
