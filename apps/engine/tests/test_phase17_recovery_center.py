from __future__ import annotations

from typing import Any


def _patch_service_lists(client, monkeypatch, payloads: dict[str, list[dict[str, Any]]]):
    mapping = {
        "source_edit_service": "source_edit",
        "source_edit_batch_service": "source_edit_batch",
        "file_organization_service": "file_organization",
        "file_organization_batch_service": "file_organization_batch",
        "file_recycle_service": "file_recycle",
        "file_recycle_batch_service": "file_recycle_batch",
    }
    seen: list[tuple[str, str | None, int]] = []

    for state_name, payload_key in mapping.items():
        service = getattr(client.app.state, state_name)

        def fake_list(
            *,
            workspace_id: str | None = None,
            limit: int = 100,
            _payload_key: str = payload_key,
            **_kwargs,
        ):
            seen.append((_payload_key, workspace_id, limit))
            return payloads.get(_payload_key, [])

        monkeypatch.setattr(service, "list", fake_list)

    return seen


def test_phase17_recovery_aggregates_without_duplicate_batch_members(
    client,
    monkeypatch,
):
    payloads = {
        "source_edit": [
            {
                "id": "edit-single",
                "task_id": "task-1",
                "workspace_id": "ws-1",
                "file_id": "file-1",
                "batch_id": None,
                "filename": "single.docx",
                "status": "applied",
                "summary": "Edit one document",
                "created_at": "2026-09-11T10:00:00+00:00",
                "applied_at": "2026-09-11T10:05:00+00:00",
                "error_message": None,
                "can_rollback": True,
            },
            {
                "id": "edit-member",
                "task_id": "task-2",
                "workspace_id": "ws-1",
                "file_id": "file-2",
                "batch_id": "edit-batch",
                "filename": "member.docx",
                "status": "applied",
                "summary": "Batch member",
                "created_at": "2026-09-11T11:00:00+00:00",
                "applied_at": "2026-09-11T11:05:00+00:00",
                "error_message": None,
                "can_rollback": True,
            },
        ],
        "source_edit_batch": [
            {
                "id": "edit-batch",
                "task_id": "task-2",
                "workspace_id": "ws-1",
                "status": "applied",
                "summary": "Edit two files",
                "edit_count": 2,
                "created_at": "2026-09-11T11:00:00+00:00",
                "applied_at": "2026-09-11T11:05:00+00:00",
                "error_message": None,
                "can_rollback": True,
                "recovery_required": False,
                "transactional": True,
                "edits": [
                    {"filename": "member.docx"},
                    {"filename": "other.xlsx"},
                ],
            }
        ],
        "file_organization": [
            {
                "id": "org-history",
                "task_id": "task-3",
                "workspace_id": "ws-1",
                "batch_id": None,
                "filename": "report.md",
                "status": "rolled_back",
                "summary": "Move report",
                "original_path": "C:/work/report.md",
                "target_path": "C:/work/archive/report.md",
                "created_at": "2026-09-11T09:00:00+00:00",
                "rolled_back_at": "2026-09-11T12:00:00+00:00",
                "error_message": None,
                "can_rollback": False,
                "recovery_required": False,
            }
        ],
        "file_recycle": [
            {
                "id": "recycle-single",
                "task_id": "task-4",
                "workspace_id": "ws-1",
                "file_id": "file-4",
                "batch_id": None,
                "filename": "old.md",
                "status": "recycled",
                "summary": "Recycle old note",
                "original_path": "C:/work/old.md",
                "created_at": "2026-09-11T13:00:00+00:00",
                "recycled_at": "2026-09-11T13:05:00+00:00",
                "error_message": None,
                "can_restore": True,
                "recovery_required": False,
            }
        ],
        "file_recycle_batch": [
            {
                "id": "recycle-attention",
                "task_id": "task-5",
                "workspace_id": "ws-1",
                "status": "recovery_required",
                "summary": "Recycle old exports",
                "item_count": 2,
                "created_at": "2026-09-11T14:00:00+00:00",
                "confirmed_at": "2026-09-11T14:01:00+00:00",
                "error_message": "Ambiguous original path",
                "can_restore": False,
                "recovery_required": True,
                "transactional": True,
                "items": [
                    {"filename": "a.csv", "original_path": "C:/work/a.csv"},
                    {"filename": "b.csv", "original_path": "C:/work/b.csv"},
                ],
            }
        ],
    }
    _patch_service_lists(client, monkeypatch, payloads)

    response = client.get("/recovery?workspace_id=ws-1")
    assert response.status_code == 200
    entries = response.json()
    by_id = {item["id"]: item for item in entries}

    assert "edit-member" not in by_id
    assert set(by_id) == {
        "edit-single",
        "edit-batch",
        "org-history",
        "recycle-single",
        "recycle-attention",
    }
    assert by_id["edit-single"]["action"] == "rollback"
    assert by_id["edit-batch"]["action"] == "rollback"
    assert by_id["edit-batch"]["filenames"] == ["member.docx", "other.xlsx"]
    assert by_id["recycle-single"]["action"] == "restore"
    assert by_id["org-history"]["action"] is None
    assert by_id["recycle-attention"]["action"] is None
    assert by_id["recycle-attention"]["recovery_required"] is True


def test_phase17_recovery_filters_non_recovery_pending_and_rejected(
    client,
    monkeypatch,
):
    payloads = {
        "source_edit": [
            {
                "id": "pending-edit",
                "batch_id": None,
                "status": "pending",
                "filename": "a.md",
            }
        ],
        "file_recycle": [
            {
                "id": "rejected-recycle",
                "batch_id": None,
                "status": "rejected",
                "filename": "b.md",
            }
        ],
    }
    _patch_service_lists(client, monkeypatch, payloads)

    response = client.get("/recovery")
    assert response.status_code == 200
    assert response.json() == []


def test_phase17_recovery_passes_workspace_scope_to_every_service(
    client,
    monkeypatch,
):
    seen = _patch_service_lists(client, monkeypatch, {})

    response = client.get("/recovery?workspace_id=workspace-17&limit=17")
    assert response.status_code == 200
    assert len(seen) == 6
    assert all(workspace_id == "workspace-17" for _, workspace_id, _ in seen)
    assert {name for name, _, _ in seen} == {
        "source_edit",
        "source_edit_batch",
        "file_organization",
        "file_organization_batch",
        "file_recycle",
        "file_recycle_batch",
    }


def test_phase17_recovery_sorts_by_latest_state_and_applies_global_limit(
    client,
    monkeypatch,
):
    payloads = {
        "source_edit": [
            {
                "id": "older",
                "task_id": "task-old",
                "workspace_id": "ws",
                "batch_id": None,
                "filename": "old.md",
                "status": "applied",
                "summary": "Old",
                "created_at": "2026-09-10T10:00:00+00:00",
                "applied_at": "2026-09-10T10:05:00+00:00",
                "can_rollback": True,
                "error_message": None,
            }
        ],
        "file_recycle": [
            {
                "id": "newer",
                "task_id": "task-new",
                "workspace_id": "ws",
                "batch_id": None,
                "filename": "new.md",
                "status": "recycled",
                "summary": "New",
                "original_path": "C:/new.md",
                "created_at": "2026-09-11T10:00:00+00:00",
                "recycled_at": "2026-09-11T10:05:00+00:00",
                "can_restore": True,
                "recovery_required": False,
                "error_message": None,
            }
        ],
    }
    _patch_service_lists(client, monkeypatch, payloads)

    response = client.get("/recovery?limit=1")
    assert response.status_code == 200
    entries = response.json()
    assert [item["id"] for item in entries] == ["newer"]


def test_phase17_recovery_endpoint_is_read_only(client, monkeypatch):
    _patch_service_lists(client, monkeypatch, {})

    assert client.post("/recovery").status_code == 405
    assert client.delete("/recovery").status_code == 405
