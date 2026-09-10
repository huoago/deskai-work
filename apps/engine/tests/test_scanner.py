from __future__ import annotations

import os
from pathlib import Path


def _workspace(client) -> str:
    response = client.post("/workspaces", json={"name": "Scanner Test"})
    assert response.status_code == 201
    return response.json()["id"]


def test_authorized_root_scans_supported_files_and_queues_jobs(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("RRP-04 pressure test", encoding="utf-8")
    (source / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (source / "program.exe").write_bytes(b"MZ-not-real")
    excluded = source / "node_modules"
    excluded.mkdir()
    (excluded / "hidden.txt").write_text("must not index", encoding="utf-8")

    added = client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "read_allowed": True, "scan_now": True},
    )
    assert added.status_code == 201
    scan = added.json()["scan"]
    assert scan["queued"] == 2
    assert scan["unsupported"] == 1

    files = client.get("/files", params={"workspace_id": workspace_id}).json()
    assert {item["filename"] for item in files} == {"note.txt", "table.csv", "program.exe"}
    assert next(item for item in files if item["filename"] == "note.txt")["status"] == "pending"
    assert next(item for item in files if item["filename"] == "program.exe")["status"] == "unsupported"

    jobs = client.get("/index-jobs", params={"workspace_id": workspace_id}).json()
    assert len(jobs) == 2
    assert all(job["status"] == "queued" for job in jobs)


def test_rescan_uses_sha256_and_does_not_duplicate_active_job(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "note.md"
    file_path.write_text("version one", encoding="utf-8")

    client.post(f"/workspaces/{workspace_id}/roots", json={"path": str(source), "scan_now": True})
    second = client.post(f"/workspaces/{workspace_id}/scan")
    assert second.json()["summary"]["unchanged"] == 1
    assert second.json()["summary"]["queued"] == 0

    file_path.write_text("version two", encoding="utf-8")
    changed = client.post(f"/workspaces/{workspace_id}/scan")
    assert changed.json()["summary"]["queued"] == 1
    jobs = client.get("/index-jobs", params={"workspace_id": workspace_id}).json()
    assert len(jobs) == 1


def test_deleted_file_is_marked_deleted(client, tmp_path: Path):
    workspace_id = _workspace(client)
    source = tmp_path / "source"
    source.mkdir()
    file_path = source / "note.txt"
    file_path.write_text("temporary", encoding="utf-8")
    client.post(f"/workspaces/{workspace_id}/roots", json={"path": str(source), "scan_now": True})

    file_path.unlink()
    scan = client.post(f"/workspaces/{workspace_id}/scan").json()
    assert scan["summary"]["deleted"] == 1
    files = client.get("/files", params={"workspace_id": workspace_id}).json()
    assert files[0]["status"] == "deleted"


def test_symlink_outside_root_is_not_scanned(client, tmp_path: Path):
    if os.name == "nt":
        return
    workspace_id = _workspace(client)
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "escape.txt").symlink_to(outside)

    added = client.post(f"/workspaces/{workspace_id}/roots", json={"path": str(source), "scan_now": True})
    assert added.json()["scan"]["skipped"] == 1
    assert client.get("/files", params={"workspace_id": workspace_id}).json() == []
