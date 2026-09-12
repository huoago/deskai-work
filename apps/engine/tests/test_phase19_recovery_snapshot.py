from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.database.models import (
    FileOrganizationProposal,
    FileRecycleProposal,
    SourceEditBatch,
    SourceFileEdit,
)
from app.recovery.snapshot import RecoverySnapshotService


def _workspace_with_files(client, tmp_path: Path, names: list[str]):
    workspace = client.post(
        "/workspaces",
        json={"name": "Phase 19 Snapshot"},
    ).json()
    root_path = tmp_path / "phase19-root"
    root_path.mkdir()
    for index, name in enumerate(names, start=1):
        (root_path / name).write_text(
            f"phase nineteen file {index} old\n",
            encoding="utf-8",
        )
    root_response = client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root_path),
            "read_allowed": True,
            "write_allowed": True,
            "watch_enabled": False,
            "scan_now": True,
        },
    )
    assert root_response.status_code == 201
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "Phase 19 recovery snapshot test",
        },
    ).json()
    return workspace, {item["filename"]: item for item in files}, task, root_path


def test_phase19_source_edit_snapshot_detects_safe_applied_state(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["notes.md"],
    )
    proposal = client.app.state.source_edit_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=files["notes.md"]["id"],
        mode="text_replace",
        summary="Update notes",
        replacements=[
            {
                "find": "old",
                "replace": "new",
                "replace_all": True,
            }
        ],
        cell_edits=[],
    )
    assert client.post(f"/file-edits/{proposal['id']}/confirm").status_code == 200

    with client.app.state.database.session() as session:
        record = session.get(SourceFileEdit, proposal["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated recovery freeze after apply"

    response = client.get(
        f"/recovery/source_edit/{proposal['id']}/snapshot"
    )
    assert response.status_code == 200
    snapshot = response.json()
    assessment = snapshot["assessment"]

    assert snapshot["read_only"] is True
    assert snapshot["persisted_status"] == "recovery_required"
    assert assessment["state"] == "consistent_applied"
    assert assessment["safe_state_detected"] is True
    assert assessment["technical_action_preconditions_satisfied"] is True
    assert assessment["safe_to_retry_existing_action"] is False
    assert assessment["action_after_reconciliation"] == "rollback"

    member = snapshot["members"][0]
    assert member["state"] == "applied"
    observations = {item["role"]: item for item in member["observations"]}
    assert "applied" in observations["workspace_source"]["matches"]
    assert "original" in observations["automatic_backup"]["matches"]
    assert "candidate" in observations["staged_candidate"]["matches"]
    assert any(
        event["action"] == "source_edit_applied"
        for event in snapshot["audit_timeline"]
    )
    assert (root / "notes.md").read_text(encoding="utf-8").endswith("new\n")


def test_phase19_recycle_snapshot_rechecks_after_manual_disk_resolution(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["obsolete.md"],
    )
    proposal = client.app.state.file_recycle_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=files["obsolete.md"]["id"],
        summary="Recycle obsolete note",
    )
    assert (
        client.post(f"/recycle-proposals/{proposal['id']}/confirm").status_code
        == 200
    )

    with client.app.state.database.session() as session:
        record = session.get(FileRecycleProposal, proposal["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated interrupted restore"

    url = f"/recovery/file_recycle/{proposal['id']}/snapshot"
    first = client.get(url).json()
    assert first["assessment"]["state"] == "consistent_recycled"
    assert first["assessment"]["safe_state_detected"] is True
    assert (
        first["assessment"]["technical_action_preconditions_satisfied"]
        is True
    )
    assert first["assessment"]["action_after_reconciliation"] == "restore"

    source = root / "obsolete.md"
    quarantine = Path(proposal["quarantine_path"])
    assert not source.exists()
    shutil.copy2(quarantine, source)

    second = client.get(url).json()
    assert second["assessment"]["state"] == "consistent_restored"
    assert second["assessment"]["safe_state_detected"] is True
    assert (
        second["assessment"]["technical_action_preconditions_satisfied"]
        is False
    )
    assert second["assessment"]["action_after_reconciliation"] is None
    assert second["persisted_status"] == "recovery_required"


def test_phase19_organization_snapshot_detects_conflicting_paths(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["report.md"],
    )
    proposal = client.app.state.file_organization_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=files["report.md"]["id"],
        operation="rename",
        summary="Rename report",
        new_name="report-final.md",
        target_relative_dir="",
    )
    assert (
        client.post(f"/file-operations/{proposal['id']}/confirm").status_code
        == 200
    )

    with client.app.state.database.session() as session:
        record = session.get(FileOrganizationProposal, proposal["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated ambiguous organization state"

    target = root / "report-final.md"
    original = root / "report.md"
    original.write_bytes(target.read_bytes())

    snapshot = client.get(
        f"/recovery/file_organization/{proposal['id']}/snapshot"
    ).json()
    assert snapshot["members"][0]["state"] == "conflict"
    assert snapshot["assessment"]["state"] == "ambiguous"
    assert snapshot["assessment"]["safe_state_detected"] is False
    assert (
        snapshot["assessment"]["technical_action_preconditions_satisfied"]
        is False
    )


def test_phase19_batch_snapshot_detects_mixed_transaction_state(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["a.md", "b.md"],
    )
    batch = client.app.state.source_edit_batch_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        summary="Update two notes",
        edits=[
            {
                "file_id": files["a.md"]["id"],
                "mode": "text_replace",
                "summary": "Update a",
                "replacements": [
                    {
                        "find": "old",
                        "replace": "new",
                        "replace_all": True,
                    }
                ],
                "cell_edits": [],
            },
            {
                "file_id": files["b.md"]["id"],
                "mode": "text_replace",
                "summary": "Update b",
                "replacements": [
                    {
                        "find": "old",
                        "replace": "new",
                        "replace_all": True,
                    }
                ],
                "cell_edits": [],
            },
        ],
    )
    assert (
        client.post(f"/file-edit-batches/{batch['id']}/confirm").status_code
        == 200
    )

    with client.app.state.database.session() as session:
        batch_record = session.get(SourceEditBatch, batch["id"])
        assert batch_record is not None
        batch_record.status = "recovery_required"
        members = list(
            session.query(SourceFileEdit)
            .filter(SourceFileEdit.batch_id == batch["id"])
            .order_by(SourceFileEdit.created_at)
            .all()
        )
        assert len(members) == 2
        backup = Path(members[0].backup_path or "")
        first_file_id = members[0].file_id

    first_path = next(
        root / name
        for name, file in files.items()
        if file["id"] == first_file_id
    )
    shutil.copy2(backup, first_path)

    snapshot = client.get(
        f"/recovery/source_edit_batch/{batch['id']}/snapshot"
    ).json()
    assert snapshot["transactional"] is True
    assert set(snapshot["assessment"]["member_states"]) == {
        "original",
        "applied",
    }
    assert snapshot["assessment"]["state"] == "mixed_transaction"
    assert snapshot["assessment"]["safe_state_detected"] is False
    assert snapshot["assessment"]["action_after_reconciliation"] is None


def test_phase19_snapshot_is_read_only_and_recovery_only(client, tmp_path):
    workspace, files, task, _root = _workspace_with_files(
        client,
        tmp_path,
        ["pending.md"],
    )
    proposal = client.app.state.source_edit_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=files["pending.md"]["id"],
        mode="text_replace",
        summary="Pending edit",
        replacements=[
            {
                "find": "old",
                "replace": "new",
                "replace_all": True,
            }
        ],
        cell_edits=[],
    )
    url = f"/recovery/source_edit/{proposal['id']}/snapshot"

    assert client.get(url).status_code == 409
    assert client.post(url).status_code == 405
    assert client.delete(url).status_code == 405
    assert (
        client.get(f"/recovery/unknown/{proposal['id']}/snapshot").status_code
        == 409
    )



def test_phase19_snapshot_does_not_follow_linked_parent(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    secret = real_dir / "outside.md"
    secret.write_text("outside data", encoding="utf-8")
    linked_dir = tmp_path / "linked"
    try:
        linked_dir.symlink_to(real_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    observed = RecoverySnapshotService._observe_path(
        str(linked_dir / "outside.md"),
        "workspace_source",
        {"original": "not-used"},
    )

    assert observed["is_symlink"] is True
    assert observed["is_file"] is False
    assert observed["sha256"] is None
    assert observed["matches"] == []
    assert str(linked_dir) in observed["error"]
