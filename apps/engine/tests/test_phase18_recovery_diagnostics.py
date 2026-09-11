from __future__ import annotations

from typing import Any


def _patch_service_lists(
    client,
    monkeypatch,
    payloads: dict[str, list[dict[str, Any]]],
):
    mapping = {
        "source_edit_service": "source_edit",
        "source_edit_batch_service": "source_edit_batch",
        "file_organization_service": "file_organization",
        "file_organization_batch_service": "file_organization_batch",
        "file_recycle_service": "file_recycle",
        "file_recycle_batch_service": "file_recycle_batch",
    }

    for state_name, payload_key in mapping.items():
        service = getattr(client.app.state, state_name)

        def fake_list(
            *,
            workspace_id: str | None = None,
            limit: int = 100,
            _payload_key: str = payload_key,
            **_kwargs,
        ):
            return payloads.get(_payload_key, [])

        monkeypatch.setattr(service, "list", fake_list)


def _entry(
    *,
    entry_id: str,
    status: str = "recovery_required",
    error_message: str | None = None,
    transactional: bool = False,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "task_id": f"task-{entry_id}",
        "workspace_id": "ws-18",
        "status": status,
        "summary": f"Recovery test {entry_id}",
        "created_at": "2026-09-12T01:00:00+00:00",
        "confirmed_at": "2026-09-12T01:01:00+00:00",
        "error_message": error_message,
        "recovery_required": status == "recovery_required",
        "transactional": transactional,
    }


def test_phase18_partial_transaction_gets_blocked_guidance(client, monkeypatch):
    batch = _entry(
        entry_id="partial-batch",
        error_message=(
            "Batch rollback failed and reapply was incomplete: PermissionError; "
            "report.docx: recovery failed"
        ),
        transactional=True,
    )
    batch.update(
        {
            "edit_count": 2,
            "can_rollback": False,
            "edits": [
                {"filename": "report.docx"},
                {"filename": "budget.xlsx"},
            ],
        }
    )
    _patch_service_lists(
        client,
        monkeypatch,
        {"source_edit_batch": [batch]},
    )

    response = client.get("/recovery?workspace_id=ws-18")
    assert response.status_code == 200
    [item] = response.json()
    diagnostic = item["diagnostic"]

    assert item["action"] is None
    assert diagnostic["code"] == "partial_transaction"
    assert diagnostic["severity"] == "blocked"
    assert diagnostic["confidence"] == "high"
    assert diagnostic["automatic_repair_available"] is False
    assert any("整个事务" in step for step in diagnostic["prohibited_actions"])
    assert any("批次成员现状清单" in step for step in diagnostic["guided_checks"])


def test_phase18_ambiguous_recycle_state_preserves_quarantine_guidance(
    client,
    monkeypatch,
):
    batch = _entry(
        entry_id="ambiguous-recycle",
        error_message=(
            "Startup recovery found ambiguous recycle batch members: a.csv; b.csv"
        ),
        transactional=True,
    )
    batch.update(
        {
            "item_count": 2,
            "can_restore": False,
            "items": [
                {"filename": "a.csv", "original_path": "C:/work/a.csv"},
                {"filename": "b.csv", "original_path": "C:/work/b.csv"},
            ],
        }
    )
    _patch_service_lists(
        client,
        monkeypatch,
        {"file_recycle_batch": [batch]},
    )

    [item] = client.get("/recovery").json()
    diagnostic = item["diagnostic"]

    assert diagnostic["code"] == "ambiguous_state"
    assert any("隔离副本" in step for step in diagnostic["guided_checks"])
    assert any("不要删除自动备份或回收隔离副本" in step for step in diagnostic["prohibited_actions"])
    assert any("persisted_error=" in value for value in diagnostic["evidence"])


def test_phase18_hash_mismatch_is_classified_without_force_action(
    client,
    monkeypatch,
):
    edit = _entry(
        entry_id="hash-edit",
        error_message="Rollback hash verification failed for notes.md",
    )
    edit.pop("recovery_required")
    edit.update(
        {
            "batch_id": None,
            "filename": "notes.md",
            "can_rollback": False,
        }
    )
    _patch_service_lists(client, monkeypatch, {"source_edit": [edit]})

    [item] = client.get("/recovery").json()
    diagnostic = item["diagnostic"]

    assert diagnostic["code"] == "hash_mismatch"
    assert item["recovery_required"] is True
    assert item["action"] is None
    assert any("SHA-256" in step for step in diagnostic["guided_checks"])
    assert any("force overwrite" in step for step in diagnostic["prohibited_actions"])


def test_phase18_non_blocked_recovery_entries_have_no_diagnostic(
    client,
    monkeypatch,
):
    edit = _entry(
        entry_id="safe-edit",
        status="applied",
        error_message=None,
    )
    edit.update(
        {
            "batch_id": None,
            "filename": "safe.md",
            "can_rollback": True,
        }
    )
    _patch_service_lists(client, monkeypatch, {"source_edit": [edit]})

    [item] = client.get("/recovery").json()
    assert item["action"] == "rollback"
    assert item["diagnostic"] is None


def test_phase18_unknown_failure_falls_back_to_manual_review(
    client,
    monkeypatch,
):
    proposal = _entry(
        entry_id="unknown-path",
        error_message="Unexpected external filesystem state",
    )
    proposal.update(
        {
            "batch_id": None,
            "filename": "report.md",
            "original_path": "C:/work/report.md",
            "target_path": "C:/work/archive/report.md",
            "can_rollback": False,
        }
    )
    _patch_service_lists(
        client,
        monkeypatch,
        {"file_organization": [proposal]},
    )

    [item] = client.get("/recovery").json()
    diagnostic = item["diagnostic"]

    assert diagnostic["code"] == "manual_review"
    assert diagnostic["confidence"] == "low"
    assert diagnostic["automatic_repair_available"] is False
    assert len(diagnostic["guided_checks"]) >= 4
