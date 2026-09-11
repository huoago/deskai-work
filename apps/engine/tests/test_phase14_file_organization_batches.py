from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ai.provider import AgentResponse, AgentToolRequest
from app.database.models import FileOrganizationBatch, FileOrganizationProposal
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
    workspace = client.post("/workspaces", json={"name": "Phase 14 organize"}).json()
    root_path = tmp_path / "phase14-root"
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
            "request": "Organize alpha and beta together",
        },
    ).json()
    return workspace, by_name, task, root_path, alpha, beta


def _rename_batch(client, workspace, files, task):
    return client.app.state.file_organization_batch_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        summary="Rename two project files together",
        operations=[
            {
                "file_id": files["alpha.md"]["id"],
                "operation": "rename",
                "summary": "Rename alpha",
                "new_name": "alpha-final.md",
                "target_relative_dir": "",
            },
            {
                "file_id": files["beta.md"]["id"],
                "operation": "rename",
                "summary": "Rename beta",
                "new_name": "beta-final.md",
                "target_relative_dir": "",
            },
        ],
    )


def test_phase14_batch_confirm_and_rollback_are_all_or_nothing(client, tmp_path):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _rename_batch(client, workspace, files, task)

    assert batch["status"] == "pending"
    assert batch["operation_count"] == 2
    assert batch["transactional"] is True
    assert alpha.exists() and beta.exists()

    detail = client.get(f"/tasks/{task['id']}").json()
    assert len(detail["file_operation_batches"]) == 1
    assert all(item["batch_id"] == batch["id"] for item in batch["operations"])

    member_id = batch["operations"][0]["id"]
    single_confirm = client.post(f"/file-operations/{member_id}/confirm")
    assert single_confirm.status_code == 409
    assert "transactional batch" in single_confirm.json()["detail"]

    response = client.post(f"/file-operation-batches/{batch['id']}/confirm")
    assert response.status_code == 200
    applied = response.json()
    assert applied["status"] == "applied"
    assert all(item["status"] == "applied" for item in applied["operations"])
    assert not alpha.exists()
    assert not beta.exists()
    assert (root_path / "alpha-final.md").read_text(encoding="utf-8") == "alpha content\n"
    assert (root_path / "beta-final.md").read_text(encoding="utf-8") == "beta content\n"

    current_files = client.get(f"/files?workspace_id={workspace['id']}").json()
    current_by_id = {item["id"]: item for item in current_files}
    assert current_by_id[files["alpha.md"]["id"]]["filename"] == "alpha-final.md"
    assert current_by_id[files["beta.md"]["id"]]["filename"] == "beta-final.md"
    assert current_by_id[files["alpha.md"]["id"]]["sha256"] == files["alpha.md"]["sha256"]
    assert current_by_id[files["beta.md"]["id"]]["sha256"] == files["beta.md"]["sha256"]

    rollback = client.post(f"/file-operation-batches/{batch['id']}/rollback")
    assert rollback.status_code == 200
    assert rollback.json()["status"] == "rolled_back"
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    assert not (root_path / "alpha-final.md").exists()
    assert not (root_path / "beta-final.md").exists()


def test_phase14_stale_member_blocks_entire_batch_before_any_move(client, tmp_path):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _rename_batch(client, workspace, files, task)
    beta.write_text("external beta update\n", encoding="utf-8")

    response = client.post(f"/file-operation-batches/{batch['id']}/confirm")
    assert response.status_code == 409
    assert "changed after the organization proposal" in response.json()["detail"]
    assert alpha.exists()
    assert beta.read_text(encoding="utf-8") == "external beta update\n"
    assert not (root_path / "alpha-final.md").exists()
    assert not (root_path / "beta-final.md").exists()
    assert client.get(f"/file-operation-batches/{batch['id']}").json()["status"] == "pending"


def test_phase14_mid_batch_failure_restores_already_moved_files(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _rename_batch(client, workspace, files, task)
    service = client.app.state.file_organization_service
    original_move = service._move_no_overwrite
    calls = 0

    def flaky_move(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second move failure")
        original_move(source, target)

    monkeypatch.setattr(service, "_move_no_overwrite", flaky_move)

    response = client.post(f"/file-operation-batches/{batch['id']}/confirm")
    assert response.status_code == 409
    assert "already-moved files were restored" in response.json()["detail"]
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    assert not (root_path / "alpha-final.md").exists()
    assert not (root_path / "beta-final.md").exists()

    current = client.get(f"/file-operation-batches/{batch['id']}").json()
    assert current["status"] == "pending"
    assert "every moved file was restored" in current["error_message"]


def test_phase14_duplicate_targets_are_rejected_without_path_changes(client, tmp_path):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )

    with pytest.raises(ValueError, match="duplicate target paths"):
        client.app.state.file_organization_batch_service.propose(
            task_id=task["id"],
            workspace_id=workspace["id"],
            summary="Invalid duplicate targets",
            operations=[
                {
                    "file_id": files["alpha.md"]["id"],
                    "operation": "rename",
                    "summary": "Rename alpha",
                    "new_name": "same.md",
                    "target_relative_dir": "",
                },
                {
                    "file_id": files["beta.md"]["id"],
                    "operation": "rename",
                    "summary": "Rename beta",
                    "new_name": "same.md",
                    "target_relative_dir": "",
                },
            ],
        )

    assert (root_path / "alpha.md").exists()
    assert (root_path / "beta.md").exists()
    assert not (root_path / "same.md").exists()


def test_phase14_rollback_preflight_blocks_whole_batch_if_original_is_occupied(
    client,
    tmp_path,
):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch = _rename_batch(client, workspace, files, task)
    assert client.post(f"/file-operation-batches/{batch['id']}/confirm").status_code == 200

    alpha.write_text("new occupant\n", encoding="utf-8")
    rollback = client.post(f"/file-operation-batches/{batch['id']}/rollback")
    assert rollback.status_code == 409
    assert "original path is occupied" in rollback.json()["detail"].lower()
    assert alpha.read_text(encoding="utf-8") == "new occupant\n"
    assert (root_path / "alpha-final.md").exists()
    assert (root_path / "beta-final.md").exists()
    assert not beta.exists()


def test_phase14_startup_recovery_restores_partial_apply_to_pending(client, tmp_path):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch_service = client.app.state.file_organization_batch_service
    organization_service = client.app.state.file_organization_service
    batch = _rename_batch(client, workspace, files, task)
    batch_record = batch_service._batch_record(batch["id"], expected_status="pending")
    snapshots = batch_service._snapshots(batch_record, expected_status="pending")
    batch_service._preflight_apply(snapshots)

    now = datetime.now(timezone.utc)
    with client.app.state.database.session() as session:
        record = session.get(FileOrganizationBatch, batch["id"])
        assert record is not None
        record.status = "applying"
        record.confirmed_at = now
        members = list(
            session.scalars(
                select(FileOrganizationProposal).where(
                    FileOrganizationProposal.batch_id == batch["id"]
                )
            ).all()
        )
        for item in members:
            item.status = "applying"
            item.confirmed_at = now

    organization_service._move_no_overwrite(
        snapshots[0]["source"],
        snapshots[0]["target"],
    )
    assert not alpha.exists()
    assert (root_path / "alpha-final.md").exists()
    assert beta.exists()

    recovered = batch_service.recover_incomplete_batches()
    assert recovered == {"recovered": 1, "blocked": 0}
    assert alpha.read_text(encoding="utf-8") == "alpha content\n"
    assert beta.read_text(encoding="utf-8") == "beta content\n"
    assert not (root_path / "alpha-final.md").exists()
    assert client.get(f"/file-operation-batches/{batch['id']}").json()["status"] == "pending"


def test_phase14_startup_recovery_freezes_ambiguous_member(client, tmp_path):
    workspace, files, task, root_path, alpha, _beta = _workspace_with_files(
        client,
        tmp_path,
    )
    batch_service = client.app.state.file_organization_batch_service
    batch = _rename_batch(client, workspace, files, task)
    target = root_path / "alpha-final.md"
    target.write_bytes(alpha.read_bytes())

    with client.app.state.database.session() as session:
        record = session.get(FileOrganizationBatch, batch["id"])
        assert record is not None
        record.status = "applying"
        members = list(
            session.scalars(
                select(FileOrganizationProposal).where(
                    FileOrganizationProposal.batch_id == batch["id"]
                )
            ).all()
        )
        for item in members:
            item.status = "applying"

    recovered = batch_service.recover_incomplete_batches()
    assert recovered == {"recovered": 0, "blocked": 1}
    current = client.get(f"/file-operation-batches/{batch['id']}").json()
    assert current["status"] == "recovery_required"
    assert alpha.exists()
    assert target.exists()


def test_phase14_rejects_duplicate_file_ids(client, tmp_path):
    workspace, files, task, _root_path, _alpha, _beta = _workspace_with_files(
        client,
        tmp_path,
    )
    operation = {
        "file_id": files["alpha.md"]["id"],
        "operation": "rename",
        "summary": "Duplicate alpha",
        "new_name": "alpha-new.md",
        "target_relative_dir": "",
    }
    with pytest.raises(ValueError, match="same file more than once"):
        client.app.state.file_organization_batch_service.propose(
            task_id=task["id"],
            workspace_id=workspace["id"],
            summary="Duplicate file batch",
            operations=[operation, operation],
        )


def test_phase14_agent_can_only_stage_transactional_organization_batch(
    client,
    tmp_path,
):
    workspace, files, task, root_path, alpha, beta = _workspace_with_files(
        client,
        tmp_path,
    )
    arguments = {
        "summary": "Organize both files",
        "operations": [
            {
                "file_id": files["alpha.md"]["id"],
                "operation": "rename",
                "summary": "Rename alpha",
                "new_name": "alpha-agent.md",
                "target_relative_dir": "",
            },
            {
                "file_id": files["beta.md"]["id"],
                "operation": "rename",
                "summary": "Rename beta",
                "new_name": "beta-agent.md",
                "target_relative_dir": "",
            },
        ],
    }
    provider = FakeAgentProvider(
        [
            _tool_step(
                "propose_file_organization_batch",
                arguments,
                call_id="organize_batch_1",
            ),
            _final("批量文件整理事务已生成，正在等待你一次确认整个变更集。"),
        ]
    )
    _wire_agent(client, provider)

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert alpha.exists() and beta.exists()
    assert not (root_path / "alpha-agent.md").exists()
    assert not (root_path / "beta-agent.md").exists()
    assert len(detail["file_operation_batches"]) == 1
    batch = detail["file_operation_batches"][0]
    assert batch["status"] == "pending"
    assert batch["operation_count"] == 2
    assert batch["transactional"] is True
    assert all(item["batch_id"] == batch["id"] for item in batch["operations"])
    assert [call["tool_name"] for call in detail["tool_calls"]] == [
        "propose_file_organization_batch"
    ]
    assert detail["tool_calls"][0]["risk_level"] == 3

    second_items = provider.calls[1]["input_items"]
    output = next(
        item
        for item in second_items
        if item.get("type") == "function_call_output"
        and item.get("call_id") == "organize_batch_1"
    )
    payload = json.loads(output["output"])
    assert payload["result"]["requires_user_confirmation"] is True
    assert payload["result"]["transactional"] is True
