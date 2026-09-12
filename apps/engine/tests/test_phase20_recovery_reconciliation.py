from __future__ import annotations

import shutil
from pathlib import Path

from app.database.models import (
    File,
    FileOrganizationProposal,
    FileRecycleProposal,
    RecoveryReconciliationProposal,
    SourceEditBatch,
    SourceFileEdit,
)


def _workspace_with_files(client, tmp_path: Path, names: list[str]):
    workspace = client.post(
        "/workspaces",
        json={"name": "Phase 20 Reconciliation"},
    ).json()
    root_path = tmp_path / "phase20-root"
    root_path.mkdir()
    for index, name in enumerate(names, start=1):
        (root_path / name).write_text(
            f"phase twenty file {index} old\n",
            encoding="utf-8",
        )
    response = client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root_path),
            "read_allowed": True,
            "write_allowed": True,
            "watch_enabled": False,
            "scan_now": True,
        },
    )
    assert response.status_code == 201
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "Phase 20 recovery reconciliation test",
        },
    ).json()
    return workspace, {item["filename"]: item for item in files}, task, root_path


def _applied_source_edit(client, workspace, file, task):
    proposal = client.app.state.source_edit_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        mode="text_replace",
        summary="Phase 20 source edit",
        replacements=[
            {
                "find": "old",
                "replace": "new",
                "replace_all": True,
            }
        ],
        cell_edits=[],
    )
    response = client.post(f"/file-edits/{proposal['id']}/confirm")
    assert response.status_code == 200
    return response.json()


def test_phase20_source_edit_reconciliation_requires_second_confirmation(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["notes.md"],
    )
    applied = _applied_source_edit(
        client,
        workspace,
        files["notes.md"],
        task,
    )
    source = root / "notes.md"
    before = source.read_bytes()

    with client.app.state.database.session() as session:
        record = session.get(SourceFileEdit, applied["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated frozen applied edit"

    proposed = client.post(
        f"/recovery-reconciliations/source_edit/{applied['id']}"
    )
    assert proposed.status_code == 200
    proposal = proposed.json()
    assert proposal["status"] == "pending"
    assert proposal["target_status"] == "applied"
    assert proposal["historical_action"] == "rollback"
    assert proposal["filesystem_mutation"] is False
    assert source.read_bytes() == before
    assert (
        client.get(f"/file-edits/{applied['id']}").json()["status"]
        == "recovery_required"
    )

    confirmed = client.post(
        f"/recovery-reconciliations/{proposal['id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert source.read_bytes() == before

    edit = client.get(f"/file-edits/{applied['id']}").json()
    assert edit["status"] == "applied"
    assert edit["can_rollback"] is True

    recovery = client.get("/recovery").json()
    entry = next(item for item in recovery if item["id"] == applied["id"])
    assert entry["action"] == "rollback"
    assert entry["recovery_required"] is False

    rollback = client.post(f"/file-edits/{applied['id']}/rollback")
    assert rollback.status_code == 200
    assert source.read_text(encoding="utf-8").endswith("old\n")


def test_phase20_reconciliation_becomes_stale_when_snapshot_changes(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["obsolete.md"],
    )
    recycle = client.app.state.file_recycle_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=files["obsolete.md"]["id"],
        summary="Recycle obsolete file",
    )
    assert (
        client.post(f"/recycle-proposals/{recycle['id']}/confirm").status_code
        == 200
    )
    with client.app.state.database.session() as session:
        record = session.get(FileRecycleProposal, recycle["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated frozen recycle"

    proposed = client.post(
        f"/recovery-reconciliations/file_recycle/{recycle['id']}"
    ).json()
    assert proposed["target_status"] == "recycled"

    source = root / "obsolete.md"
    shutil.copy2(Path(recycle["quarantine_path"]), source)

    response = client.post(
        f"/recovery-reconciliations/{proposed['id']}/confirm"
    )
    assert response.status_code == 409
    current = client.get(
        f"/recovery-reconciliations/{proposed['id']}"
    ).json()
    assert current["status"] == "stale"
    assert (
        client.get(f"/recycle-proposals/{recycle['id']}").json()["status"]
        == "recovery_required"
    )
    assert source.is_file()


def test_phase20_organization_reconciliation_repairs_metadata_only(
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
    target = root / "report-final.md"
    target_bytes = target.read_bytes()

    with client.app.state.database.session() as session:
        record = session.get(FileOrganizationProposal, proposal["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated metadata finalization mismatch"
        file = session.get(File, record.file_id)
        assert file is not None
        file.path = record.original_path
        file.filename = Path(record.original_path).name

    reconciliation = client.post(
        f"/recovery-reconciliations/file_organization/{proposal['id']}"
    ).json()
    assert reconciliation["snapshot_state"] == "consistent_applied"
    assert client.post(
        f"/recovery-reconciliations/{reconciliation['id']}/confirm"
    ).status_code == 200
    assert target.read_bytes() == target_bytes

    with client.app.state.database.session() as session:
        record = session.get(FileOrganizationProposal, proposal["id"])
        assert record is not None
        file = session.get(File, record.file_id)
        assert file is not None
        assert record.status == "applied"
        assert Path(file.path) == target
        assert file.filename == "report-final.md"

    rollback = client.post(f"/file-operations/{proposal['id']}/rollback")
    assert rollback.status_code == 200
    assert (root / "report.md").is_file()
    assert not target.exists()


def test_phase20_recycle_reconciliation_restores_original_restore_path(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["trash.md"],
    )
    recycle = client.app.state.file_recycle_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=files["trash.md"]["id"],
        summary="Recycle trash file",
    )
    assert (
        client.post(f"/recycle-proposals/{recycle['id']}/confirm").status_code
        == 200
    )

    with client.app.state.database.session() as session:
        record = session.get(FileRecycleProposal, recycle["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated recovery freeze"
        file = session.get(File, record.file_id)
        assert file is not None
        file.status = record.previous_file_status

    proposed = client.post(
        f"/recovery-reconciliations/file_recycle/{recycle['id']}"
    ).json()
    assert proposed["historical_action"] == "restore"
    assert client.post(
        f"/recovery-reconciliations/{proposed['id']}/confirm"
    ).status_code == 200

    with client.app.state.database.session() as session:
        record = session.get(FileRecycleProposal, recycle["id"])
        file = session.get(File, record.file_id)
        assert record.status == "recycled"
        assert file.status == "recycled"

    restored = client.post(f"/recycle-proposals/{recycle['id']}/restore")
    assert restored.status_code == 200
    assert (root / "trash.md").is_file()


def test_phase20_source_edit_batch_reconciliation_is_atomic(
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
        summary="Batch update",
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
        record = session.get(SourceEditBatch, batch["id"])
        assert record is not None
        record.status = "recovery_required"
        record.error_message = "Simulated batch freeze"
        members = list(
            session.query(SourceFileEdit)
            .filter(SourceFileEdit.batch_id == batch["id"])
            .all()
        )
        members[0].status = "recovery_required"

    proposed = client.post(
        f"/recovery-reconciliations/source_edit_batch/{batch['id']}"
    ).json()
    assert proposed["target_status"] == "applied"
    assert client.post(
        f"/recovery-reconciliations/{proposed['id']}/confirm"
    ).status_code == 200

    current = client.get(f"/file-edit-batches/{batch['id']}").json()
    assert current["status"] == "applied"
    assert all(item["status"] == "applied" for item in current["edits"])

    rollback = client.post(f"/file-edit-batches/{batch['id']}/rollback")
    assert rollback.status_code == 200
    assert (root / "a.md").read_text(encoding="utf-8").endswith("old\n")
    assert (root / "b.md").read_text(encoding="utf-8").endswith("old\n")


def test_phase20_non_actionable_safe_state_cannot_be_reconciled(
    client,
    tmp_path,
):
    workspace, files, task, _root = _workspace_with_files(
        client,
        tmp_path,
        ["original.md"],
    )
    applied = _applied_source_edit(
        client,
        workspace,
        files["original.md"],
        task,
    )
    with client.app.state.database.session() as session:
        record = session.get(SourceFileEdit, applied["id"])
        assert record is not None
        backup = Path(record.backup_path or "")
        source = Path(session.get(File, record.file_id).path)
        shutil.copy2(backup, source)
        record.status = "recovery_required"
        record.error_message = "Simulated freeze after original was restored"

    response = client.post(
        f"/recovery-reconciliations/source_edit/{applied['id']}"
    )
    assert response.status_code == 409
    assert "only reconciles verified applied or recycled states" in (
        response.json()["detail"]
    )


def test_phase20_reconciliation_reject_preserves_frozen_transaction(
    client,
    tmp_path,
):
    workspace, files, task, root = _workspace_with_files(
        client,
        tmp_path,
        ["reject.md"],
    )
    applied = _applied_source_edit(
        client,
        workspace,
        files["reject.md"],
        task,
    )
    before = (root / "reject.md").read_bytes()
    with client.app.state.database.session() as session:
        record = session.get(SourceFileEdit, applied["id"])
        record.status = "recovery_required"

    proposal = client.post(
        f"/recovery-reconciliations/source_edit/{applied['id']}"
    ).json()
    rejected = client.post(
        f"/recovery-reconciliations/{proposal['id']}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert (root / "reject.md").read_bytes() == before
    assert (
        client.get(f"/file-edits/{applied['id']}").json()["status"]
        == "recovery_required"
    )


def test_phase20_duplicate_proposal_reuses_same_snapshot(client, tmp_path):
    workspace, files, task, _root = _workspace_with_files(
        client,
        tmp_path,
        ["duplicate.md"],
    )
    applied = _applied_source_edit(
        client,
        workspace,
        files["duplicate.md"],
        task,
    )
    with client.app.state.database.session() as session:
        record = session.get(SourceFileEdit, applied["id"])
        record.status = "recovery_required"

    url = f"/recovery-reconciliations/source_edit/{applied['id']}"
    first = client.post(url).json()
    second = client.post(url).json()
    assert first["id"] == second["id"]

    with client.app.state.database.session() as session:
        records = list(
            session.query(RecoveryReconciliationProposal)
            .filter(
                RecoveryReconciliationProposal.transaction_id
                == applied["id"]
            )
            .all()
        )
        assert len(records) == 1
