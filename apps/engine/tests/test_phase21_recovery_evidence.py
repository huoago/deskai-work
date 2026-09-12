from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.database.models import AuditLog, SourceFileEdit
from sqlalchemy import select


def _workspace(client, tmp_path: Path):
    workspace = client.post("/workspaces", json={"name": "Phase 21 Evidence"}).json()
    root = tmp_path / "phase21-root"
    root.mkdir()
    secret = "USER-CONTENT-MUST-NOT-BE-IN-EVIDENCE-PACKAGE"
    (root / "notes.md").write_text(f"old {secret}\n", encoding="utf-8")
    response = client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root),
            "read_allowed": True,
            "write_allowed": True,
            "watch_enabled": False,
            "scan_now": True,
        },
    )
    assert response.status_code == 201
    file = next(
        item
        for item in client.get(f"/files?workspace_id={workspace['id']}").json()
        if item["filename"] == "notes.md"
    )
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "Phase 21 evidence export test",
        },
    ).json()
    return workspace, file, task, root, secret


def _frozen_applied_edit(client, workspace, file, task):
    proposal = client.app.state.source_edit_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        mode="text_replace",
        summary="Phase 21 source edit",
        replacements=[{"find": "old", "replace": "new", "replace_all": True}],
        cell_edits=[],
    )
    applied = client.post(f"/file-edits/{proposal['id']}/confirm")
    assert applied.status_code == 200
    with client.app.state.database.session() as session:
        record = session.get(SourceFileEdit, proposal["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated recovery evidence incident"
    return proposal["id"]


def test_phase21_evidence_package_contains_metadata_not_user_bytes(client, tmp_path):
    workspace, file, task, root, secret = _workspace(client, tmp_path)
    edit_id = _frozen_applied_edit(client, workspace, file, task)
    before = (root / "notes.md").read_bytes()

    response = client.post(f"/recovery/source_edit/{edit_id}/evidence-package")
    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only_evidence"] is True
    assert payload["user_file_content_included"] is False
    assert payload["user_files_modified"] is False
    assert (root / "notes.md").read_bytes() == before

    artifact = payload["artifact"]
    assert artifact["kind"] == "recovery_evidence"
    assert artifact["mime_type"] == "application/zip"
    bundle = Path(artifact["path"])
    assert bundle.is_file()

    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert names == {
            "audit-log.json",
            "diagnostic.json",
            "manifest.json",
            "reconciliations.json",
            "snapshot.json",
            "summary.md",
            "transaction.json",
        }
        manifest = json.loads(archive.read("manifest.json"))
        snapshot = json.loads(archive.read("snapshot.json"))
        assert manifest["user_file_content_included"] is False
        assert manifest["user_files_modified"] is False
        assert manifest["live_snapshot"] is True
        assert snapshot["assessment"]["state"] == "consistent_applied"
        all_bytes = b"".join(archive.read(name) for name in names)
        assert secret.encode("utf-8") not in all_bytes

    assert client.get(f"/file-edits/{edit_id}").json()["status"] == "recovery_required"


def test_phase21_export_after_reconciliation_keeps_history_without_live_snapshot(
    client,
    tmp_path,
):
    workspace, file, task, root, _ = _workspace(client, tmp_path)
    edit_id = _frozen_applied_edit(client, workspace, file, task)
    proposal = client.post(
        f"/recovery-reconciliations/transactions/source_edit/{edit_id}"
    ).json()
    confirmed = client.post(
        f"/recovery-reconciliations/{proposal['id']}/confirm"
    )
    assert confirmed.status_code == 200

    before = (root / "notes.md").read_bytes()
    response = client.post(f"/recovery/source_edit/{edit_id}/evidence-package")
    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["live_snapshot"] is False
    assert payload["manifest"]["reconciliation_records"] >= 1
    assert (root / "notes.md").read_bytes() == before

    with zipfile.ZipFile(Path(payload["artifact"]["path"])) as archive:
        snapshot = json.loads(archive.read("snapshot.json"))
        history = json.loads(archive.read("reconciliations.json"))
        assert snapshot["available"] is False
        assert snapshot["reason"] == "transaction_not_recovery_required"
        assert any(item["status"] == "confirmed" for item in history)


def test_phase21_batch_member_cannot_be_exported_as_independent_transaction(
    client,
    tmp_path,
):
    workspace = client.post("/workspaces", json={"name": "Phase 21 Batch"}).json()
    root = tmp_path / "phase21-batch-root"
    root.mkdir()
    for name in ("one.md", "two.md"):
        (root / name).write_text("old\n", encoding="utf-8")
    assert client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root),
            "read_allowed": True,
            "write_allowed": True,
            "watch_enabled": False,
            "scan_now": True,
        },
    ).status_code == 201
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    task = client.post(
        "/tasks",
        json={"workspace_id": workspace["id"], "request": "batch evidence"},
    ).json()
    batch = client.app.state.source_edit_batch_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        summary="Phase 21 batch",
        edits=[
            {
                "file_id": file["id"],
                "mode": "text_replace",
                "summary": "member",
                "replacements": [
                    {"find": "old", "replace": "new", "replace_all": True}
                ],
                "cell_edits": [],
            }
            for file in files
        ],
    )
    member_id = batch["edits"][0]["id"]
    response = client.post(f"/recovery/source_edit/{member_id}/evidence-package")
    assert response.status_code == 409
    assert "parent recovery transaction" in response.json()["detail"]


def test_phase21_evidence_export_is_audited(client, tmp_path):
    workspace, file, task, _, _ = _workspace(client, tmp_path)
    edit_id = _frozen_applied_edit(client, workspace, file, task)
    response = client.post(f"/recovery/source_edit/{edit_id}/evidence-package")
    assert response.status_code == 200
    artifact_id = response.json()["artifact"]["id"]

    with client.app.state.database.session() as session:
        row = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.task_id == task["id"],
                AuditLog.action == "recovery_evidence_exported",
            )
            .order_by(AuditLog.timestamp.desc())
        )
        assert row is not None
        assert row.target == artifact_id
        assert "user_file_content_included=false" in (row.result or "")
