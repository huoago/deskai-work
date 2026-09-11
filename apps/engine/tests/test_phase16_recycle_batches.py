from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ai.provider import AgentResponse, AgentToolRequest
from app.database.models import FileRecycleBatch, FileRecycleProposal
from app.security.secrets import SecretStatus


class AgentSecretStore:
    def get_openai_api_key(self) -> str | None:
        return "sk-test-agent-123456789012345678901234"

    def status(self) -> SecretStatus:
        return SecretStatus(
            configured=True,
            source="credential_manager",
            credential_store_available=True,
            writable=True,
        )


class FakeAgentProvider:
    def __init__(self, responses: list[AgentResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def agent_response(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Fake Agent provider ran out of responses")
        return self.responses.pop(0)


def _tool_step(name: str, arguments: dict, *, call_id: str) -> AgentResponse:
    return AgentResponse(
        text="",
        tool_calls=[AgentToolRequest(call_id=call_id, name=name, arguments=arguments)],
        output_items=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        ],
        response_id=f"resp_{call_id}",
        model="gpt-5.6-sol",
        input_tokens=50,
        output_tokens=15,
    )


def _final(text: str) -> AgentResponse:
    return AgentResponse(
        text=text,
        tool_calls=[],
        output_items=[{"type": "message", "role": "assistant"}],
        response_id="resp_final",
        model="gpt-5.6-sol",
        input_tokens=60,
        output_tokens=20,
    )


def _wire_agent(client, provider: FakeAgentProvider) -> None:
    secret_store = AgentSecretStore()
    client.app.state.secret_store = secret_store
    client.app.state.agent_orchestrator.secret_store = secret_store
    client.app.state.agent_orchestrator.provider = provider


def _workspace_with_files(client, tmp_path: Path):
    workspace = client.post("/workspaces", json={"name": "Phase 16 batch recycle"}).json()
    root_path = tmp_path / "phase16-root"
    root_path.mkdir(parents=True)
    alpha = root_path / "alpha.md"
    beta = root_path / "beta.md"
    alpha.write_text("alpha content\n", encoding="utf-8")
    beta.write_text("beta content\n", encoding="utf-8")

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
    by_name = {item["filename"]: item for item in files}
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "Recycle alpha and beta together",
        },
    ).json()
    return workspace, by_name, task, root_path, alpha, beta


def _batch(client, workspace, files, task):
    return client.app.state.file_recycle_batch_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        summary="Recycle two obsolete files together",
        items=[
            {
                "file_id": files["alpha.md"]["id"],
                "summary": "Recycle alpha",
            },
            {
                "file_id": files["beta.md"]["id"],
                "summary": "Recycle beta",
            },
        ],
    )


def test_phase16_batch_confirm_and_restore_are_transactional(client, tmp_path):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)

    assert batch["status"] == "pending"
    assert batch["item_count"] == 2
    assert batch["transactional"] is True
    assert batch["permanent_delete_available"] is False
    assert alpha.exists() and beta.exists()
    assert all(not Path(item["quarantine_path"]).exists() for item in batch["items"])

    detail = client.get(f"/tasks/{task['id']}").json()
    assert len(detail["recycle_batches"]) == 1
    assert all(item["batch_id"] == batch["id"] for item in batch["items"])

    member_id = batch["items"][0]["id"]
    single_confirm = client.post(f"/recycle-proposals/{member_id}/confirm")
    assert single_confirm.status_code == 409
    assert "transactional batch" in single_confirm.json()["detail"]

    confirmed = client.post(f"/recycle-batches/{batch['id']}/confirm")
    assert confirmed.status_code == 200
    recycled = confirmed.json()
    assert recycled["status"] == "recycled"
    assert all(item["status"] == "recycled" for item in recycled["items"])
    assert not alpha.exists()
    assert not beta.exists()
    assert all(Path(item["quarantine_path"]).is_file() for item in recycled["items"])

    current_files = client.get(f"/files?workspace_id={workspace['id']}").json()
    current_by_id = {item["id"]: item for item in current_files}
    assert current_by_id[files["alpha.md"]["id"]]["status"] == "recycled"
    assert current_by_id[files["beta.md"]["id"]]["status"] == "recycled"
    assert current_by_id[files["alpha.md"]["id"]]["sha256"] == files["alpha.md"]["sha256"]
    assert current_by_id[files["beta.md"]["id"]]["sha256"] == files["beta.md"]["sha256"]

    single_restore = client.post(f"/recycle-proposals/{member_id}/restore")
    assert single_restore.status_code == 409
    assert "transactional batch" in single_restore.json()["detail"]

    restored_response = client.post(f"/recycle-batches/{batch['id']}/restore")
    assert restored_response.status_code == 200
    restored = restored_response.json()
    assert restored["status"] == "restored"
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    assert all(Path(item["quarantine_path"]).is_file() for item in restored["items"])

    restored_files = client.get(f"/files?workspace_id={workspace['id']}").json()
    restored_by_id = {item["id"]: item for item in restored_files}
    assert restored_by_id[files["alpha.md"]["id"]]["id"] == files["alpha.md"]["id"]
    assert restored_by_id[files["beta.md"]["id"]]["id"] == files["beta.md"]["id"]


def test_phase16_stale_member_blocks_batch_before_quarantine_or_removal(client, tmp_path):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)
    beta.write_text("external beta update\n", encoding="utf-8")

    response = client.post(f"/recycle-batches/{batch['id']}/confirm")
    assert response.status_code == 409
    assert "changed after the recycle proposal" in response.json()["detail"]
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "external beta update\n"
    assert all(not Path(item["quarantine_path"]).exists() for item in batch["items"])
    assert client.get(f"/recycle-batches/{batch['id']}").json()["status"] == "pending"


def test_phase16_all_quarantines_verified_before_first_source_removal(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)
    service = client.app.state.file_recycle_batch_service
    original_remove = service._remove_source
    calls = 0

    def checked_remove(snapshot: dict) -> None:
        nonlocal calls
        calls += 1
        current = service.get(batch["id"])
        assert all(Path(item["quarantine_path"]).is_file() for item in current["items"])
        for item in current["items"]:
            service.recycle_service._verify_quarantine(
                Path(item["quarantine_path"]),
                item["original_sha256"],
            )
        original_remove(snapshot)

    monkeypatch.setattr(service, "_remove_source", checked_remove)

    response = client.post(f"/recycle-batches/{batch['id']}/confirm")
    assert response.status_code == 200
    assert calls == 2
    assert not alpha.exists() and not beta.exists()


def test_phase16_second_source_removal_failure_restores_first_member(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)
    service = client.app.state.file_recycle_batch_service
    original_remove = service._remove_source
    calls = 0

    def flaky_remove(snapshot: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second source removal failure")
        original_remove(snapshot)

    monkeypatch.setattr(service, "_remove_source", flaky_remove)

    response = client.post(f"/recycle-batches/{batch['id']}/confirm")
    assert response.status_code == 409
    assert "all Workspace originals remain or were restored" in response.json()["detail"]
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    current = client.get(f"/recycle-batches/{batch['id']}").json()
    assert current["status"] == "pending"
    assert all(Path(item["quarantine_path"]).is_file() for item in current["items"])


def test_phase16_duplicate_file_ids_are_rejected(client, tmp_path):
    workspace, files, task, _root_path, _alpha, _beta = _workspace_with_files(
        client,
        tmp_path,
    )
    item = {
        "file_id": files["alpha.md"]["id"],
        "summary": "Duplicate alpha",
    }

    with pytest.raises(ValueError, match="same file more than once"):
        client.app.state.file_recycle_batch_service.propose(
            task_id=task["id"],
            workspace_id=workspace["id"],
            summary="Invalid duplicate batch",
            items=[item, item],
        )


def test_phase16_restore_preflight_blocks_whole_batch_if_original_is_occupied(
    client,
    tmp_path,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)
    assert client.post(f"/recycle-batches/{batch['id']}/confirm").status_code == 200

    alpha.write_text("new occupant\n", encoding="utf-8")
    restore = client.post(f"/recycle-batches/{batch['id']}/restore")
    assert restore.status_code == 409
    assert "Original path is occupied" in restore.json()["detail"]
    assert alpha.read_text(encoding="utf-8") == "new occupant\n"
    assert not beta.exists()
    assert client.get(f"/recycle-batches/{batch['id']}").json()["status"] == "recycled"


def test_phase16_second_restore_failure_returns_first_member_to_recycled_state(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)
    assert client.post(f"/recycle-batches/{batch['id']}/confirm").status_code == 200

    recycle_service = client.app.state.file_recycle_service
    original_restore = recycle_service._restore_copy_no_overwrite
    calls = 0

    def flaky_restore(quarantine: Path, original: Path, expected_sha: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second restore failure")
        original_restore(quarantine, original, expected_sha)

    monkeypatch.setattr(recycle_service, "_restore_copy_no_overwrite", flaky_restore)

    response = client.post(f"/recycle-batches/{batch['id']}/restore")
    assert response.status_code == 409
    assert "complete batch remains safely recycled" in response.json()["detail"]
    assert not alpha.exists()
    assert not beta.exists()
    current = client.get(f"/recycle-batches/{batch['id']}").json()
    assert current["status"] == "recycled"
    assert all(Path(item["quarantine_path"]).is_file() for item in current["items"])


def test_phase16_restore_failure_with_unexpected_current_member_requires_recovery(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)
    assert client.post(f"/recycle-batches/{batch['id']}/confirm").status_code == 200

    recycle_service = client.app.state.file_recycle_service
    original_restore = recycle_service._restore_copy_no_overwrite
    calls = 0

    def corrupting_restore(
        quarantine: Path,
        original: Path,
        expected_sha: str,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            original.write_text("unexpected concurrent content\n", encoding="utf-8")
            raise OSError("simulated restore race after original path appeared")
        original_restore(quarantine, original, expected_sha)

    monkeypatch.setattr(
        recycle_service,
        "_restore_copy_no_overwrite",
        corrupting_restore,
    )

    response = client.post(f"/recycle-batches/{batch['id']}/restore")
    assert response.status_code == 409
    assert "manual recovery is required" in response.json()["detail"]

    # The first safely restored member is rolled back to recycled state.
    assert not alpha.exists()
    # Unexpected content is never deleted automatically.
    assert beta.read_text(encoding="utf-8") == "unexpected concurrent content\n"

    current = client.get(f"/recycle-batches/{batch['id']}").json()
    assert current["status"] == "recovery_required"
    assert all(Path(item["quarantine_path"]).is_file() for item in current["items"])


def test_phase16_startup_recovery_restores_partial_recycle_to_pending(
    client,
    tmp_path,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    service = client.app.state.file_recycle_batch_service
    batch = _batch(client, workspace, files, task)
    record = service._batch_record(batch["id"], expected_status="pending")
    snapshots = service._recycle_snapshots(record)
    service._preflight_recycle(snapshots)

    for snapshot in snapshots:
        service.recycle_service._ensure_verified_quarantine_copy(
            snapshot["source"],
            snapshot["quarantine"],
            snapshot["original_sha256"],
        )
    service._set_recycling(batch["id"], datetime.now(timezone.utc))
    service._remove_source(snapshots[0])
    assert not alpha.exists()
    assert beta.exists()

    recovered = service.recover_incomplete_batches()
    assert recovered == {"recovered": 1, "blocked": 0}
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    current = client.get(f"/recycle-batches/{batch['id']}").json()
    assert current["status"] == "pending"


def test_phase16_startup_recovery_completes_partial_restore(
    client,
    tmp_path,
):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch_service = client.app.state.file_recycle_batch_service
    recycle_service = client.app.state.file_recycle_service
    batch = _batch(client, workspace, files, task)
    assert client.post(f"/recycle-batches/{batch['id']}/confirm").status_code == 200

    record = batch_service._batch_record(batch["id"], expected_status="recycled")
    snapshots = batch_service._restore_snapshots(record)
    batch_service._preflight_restore(snapshots)
    batch_service._set_restoring(batch["id"])
    recycle_service._restore_copy_no_overwrite(
        snapshots[0]["quarantine"],
        snapshots[0]["original"],
        snapshots[0]["original_sha256"],
    )
    assert alpha.exists()
    assert not beta.exists()

    recovered = batch_service.recover_incomplete_batches()
    assert recovered == {"recovered": 1, "blocked": 0}
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    current = client.get(f"/recycle-batches/{batch['id']}").json()
    assert current["status"] == "restored"


def test_phase16_startup_recovery_freezes_ambiguous_member(client, tmp_path):
    workspace, files, task, _root_path, alpha, _beta = _workspace_with_files(
        client,
        tmp_path,
    )
    service = client.app.state.file_recycle_batch_service
    batch = _batch(client, workspace, files, task)
    with client.app.state.database.session() as session:
        record = session.get(FileRecycleBatch, batch["id"])
        assert record is not None
        record.status = "recycling"
        members = list(
            session.scalars(
                select(FileRecycleProposal).where(
                    FileRecycleProposal.batch_id == batch["id"]
                )
            ).all()
        )
        for item in members:
            item.status = "recycling"

    alpha.write_text("unexpected content\n", encoding="utf-8")

    recovered = service.recover_incomplete_batches()
    assert recovered == {"recovered": 0, "blocked": 1}
    current = client.get(f"/recycle-batches/{batch['id']}").json()
    assert current["status"] == "recovery_required"


def test_phase16_agent_can_only_stage_transactional_recycle_batch(client, tmp_path):
    workspace, files, task, _root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    arguments = {
        "summary": "Recycle both obsolete files",
        "items": [
            {
                "file_id": files["alpha.md"]["id"],
                "summary": "Recycle alpha",
            },
            {
                "file_id": files["beta.md"]["id"],
                "summary": "Recycle beta",
            },
        ],
    }
    provider = FakeAgentProvider(
        [
            _tool_step(
                "propose_file_recycle_batch",
                arguments,
                call_id="recycle_batch_1",
            ),
            _final("批量可恢复回收事务已生成，正在等待你一次确认整个变更集。"),
        ]
    )
    _wire_agent(client, provider)

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert alpha.exists() and beta.exists()
    assert len(detail["recycle_batches"]) == 1
    batch = detail["recycle_batches"][0]
    assert batch["status"] == "pending"
    assert batch["item_count"] == 2
    assert batch["transactional"] is True
    assert batch["permanent_delete_available"] is False
    assert all(item["batch_id"] == batch["id"] for item in batch["items"])
    assert [call["tool_name"] for call in detail["tool_calls"]] == [
        "propose_file_recycle_batch"
    ]
    assert detail["tool_calls"][0]["risk_level"] == 5

    second_items = provider.calls[1]["input_items"]
    output = next(
        item
        for item in second_items
        if item.get("type") == "function_call_output"
        and item.get("call_id") == "recycle_batch_1"
    )
    payload = json.loads(output["output"])
    assert payload["result"]["requires_user_confirmation"] is True
    assert payload["result"]["transactional"] is True
    assert payload["result"]["permanent_delete_available"] is False


def test_phase16_no_permanent_delete_batch_endpoint(client, tmp_path):
    workspace, files, task, _root_path, _alpha, _beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _batch(client, workspace, files, task)

    response = client.delete(f"/recycle-batches/{batch['id']}")
    assert response.status_code == 405
