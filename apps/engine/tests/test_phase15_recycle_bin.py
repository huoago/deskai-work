from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from docx import Document

from app.ai.provider import AgentResponse, AgentToolRequest
from app.database.models import File, FileRecycleProposal
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


def _workspace_with_file(
    client,
    tmp_path: Path,
    *,
    filename: str = "notes.md",
    content: str = "phase fifteen\n",
    write_allowed: bool = True,
):
    workspace = client.post("/workspaces", json={"name": f"Recycle {filename}"}).json()
    root_path = tmp_path / f"root-{filename.replace('.', '-')}"
    root_path.mkdir(parents=True)
    source = root_path / filename
    source.write_text(content, encoding="utf-8")
    root_response = client.post(
        f"/workspaces/{workspace['id']}/roots",
        json={
            "path": str(root_path),
            "read_allowed": True,
            "write_allowed": write_allowed,
            "watch_enabled": False,
            "scan_now": True,
        },
    )
    assert root_response.status_code == 201
    root = root_response.json()["root"]
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    file = next(item for item in files if item["filename"] == filename)
    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": f"Recycle {filename}",
        },
    ).json()
    return workspace, root, file, task, source, root_path


def _proposal(client, workspace, file, task):
    return client.app.state.file_recycle_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        summary=f"Remove {file['filename']} from the active Workspace",
    )


def test_phase15_recycle_requires_confirmation_and_restore_preserves_identity(
    client,
    tmp_path,
):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)
    quarantine = Path(proposal["quarantine_path"])

    assert proposal["status"] == "pending"
    assert proposal["requires_user_confirmation"] is True
    assert proposal["permanent_delete_available"] is False
    assert source.exists()
    assert not quarantine.exists()

    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["recycle_proposals"][0]["id"] == proposal["id"]

    confirmed = client.post(f"/recycle-proposals/{proposal['id']}/confirm")
    assert confirmed.status_code == 200
    recycled = confirmed.json()
    assert recycled["status"] == "recycled"
    assert recycled["can_restore"] is True
    assert not source.exists()
    assert quarantine.is_file()
    assert quarantine.read_text(encoding="utf-8") == "phase fifteen\n"

    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    current = next(item for item in files if item["id"] == file["id"])
    assert current["status"] == "recycled"
    assert current["path"] == file["path"]
    assert current["sha256"] == file["sha256"]

    restored_response = client.post(f"/recycle-proposals/{proposal['id']}/restore")
    assert restored_response.status_code == 200
    restored = restored_response.json()
    assert restored["status"] == "restored"
    assert source.read_text(encoding="utf-8") == "phase fifteen\n"
    assert quarantine.is_file()

    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    restored_file = next(item for item in files if item["id"] == file["id"])
    assert restored_file["sha256"] == file["sha256"]
    assert restored_file["status"] == proposal["previous_file_status"]


def test_phase15_requires_write_permission(client, tmp_path):
    workspace, root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
        write_allowed=False,
    )
    with pytest.raises(ValueError, match="write_allowed"):
        _proposal(client, workspace, file, task)
    assert source.exists()

    update = client.patch(
        f"/workspaces/{workspace['id']}/roots/{root['id']}",
        json={"write_allowed": True},
    )
    assert update.status_code == 200
    proposal = _proposal(client, workspace, file, task)
    assert proposal["status"] == "pending"


def test_phase15_stale_source_blocks_recycle_before_source_removal(client, tmp_path):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)
    source.write_text("external update\n", encoding="utf-8")

    response = client.post(f"/recycle-proposals/{proposal['id']}/confirm")
    assert response.status_code == 409
    assert "changed after the recycle proposal" in response.json()["detail"]
    assert source.read_text(encoding="utf-8") == "external update\n"
    assert not Path(proposal["quarantine_path"]).exists()


def test_phase15_quarantine_verification_failure_never_removes_source(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)
    service = client.app.state.file_recycle_service

    def fail_copy(*_args, **_kwargs):
        raise ValueError("simulated quarantine verification failure")

    monkeypatch.setattr(service, "_ensure_verified_quarantine_copy", fail_copy)

    response = client.post(f"/recycle-proposals/{proposal['id']}/confirm")
    assert response.status_code == 409
    assert "quarantine verification failure" in response.json()["detail"]
    assert source.read_text(encoding="utf-8") == "phase fifteen\n"
    current = client.get(f"/recycle-proposals/{proposal['id']}").json()
    assert current["status"] == "pending"


def test_phase15_revalidates_source_after_quarantine_copy(
    client,
    tmp_path,
    monkeypatch,
):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)
    service = client.app.state.file_recycle_service
    original_copy = service._ensure_verified_quarantine_copy

    def mutate_after_copy(src: Path, quarantine: Path, expected_sha: str) -> None:
        original_copy(src, quarantine, expected_sha)
        src.write_text("changed during copy\n", encoding="utf-8")

    monkeypatch.setattr(service, "_ensure_verified_quarantine_copy", mutate_after_copy)

    response = client.post(f"/recycle-proposals/{proposal['id']}/confirm")
    assert response.status_code == 409
    assert "changed while the quarantine copy" in response.json()["detail"]
    assert source.read_text(encoding="utf-8") == "changed during copy\n"
    assert Path(proposal["quarantine_path"]).is_file()
    current = client.get(f"/recycle-proposals/{proposal['id']}").json()
    assert current["status"] == "pending"


def test_phase15_office_lock_blocks_recycle(client, tmp_path):
    workspace = client.post("/workspaces", json={"name": "Recycle Office"}).json()
    root_path = tmp_path / "office-recycle"
    root_path.mkdir()
    source = root_path / "report.docx"
    document = Document()
    document.add_paragraph("DeskAI recycle")
    document.save(source)
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
    file = next(
        item
        for item in client.get(f"/files?workspace_id={workspace['id']}").json()
        if item["filename"] == "report.docx"
    )
    task = client.post(
        "/tasks",
        json={"workspace_id": workspace["id"], "request": "Recycle report"},
    ).json()
    proposal = _proposal(client, workspace, file, task)
    (root_path / "~$report.docx").write_text("locked", encoding="utf-8")

    confirm = client.post(f"/recycle-proposals/{proposal['id']}/confirm")
    assert confirm.status_code == 409
    assert "open in Microsoft Office" in confirm.json()["detail"]
    assert source.exists()
    assert not Path(proposal["quarantine_path"]).exists()


def test_phase15_restore_never_overwrites_occupied_original_path(client, tmp_path):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)
    assert client.post(f"/recycle-proposals/{proposal['id']}/confirm").status_code == 200
    source.write_text("new occupant\n", encoding="utf-8")

    restore = client.post(f"/recycle-proposals/{proposal['id']}/restore")
    assert restore.status_code == 409
    assert "Original path is occupied" in restore.json()["detail"]
    assert source.read_text(encoding="utf-8") == "new occupant\n"
    assert Path(proposal["quarantine_path"]).is_file()
    assert client.get(f"/recycle-proposals/{proposal['id']}").json()["status"] == "recycled"


def test_phase15_scanner_preserves_recycled_state(client, tmp_path):
    workspace, _root, file, task, _source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)
    assert client.post(f"/recycle-proposals/{proposal['id']}/confirm").status_code == 200

    scan = client.post(f"/workspaces/{workspace['id']}/scan")
    assert scan.status_code == 200
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    current = next(item for item in files if item["id"] == file["id"])
    assert current["status"] == "recycled"


def test_phase15_startup_recovery_finishes_source_removed_recycle(client, tmp_path):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    service = client.app.state.file_recycle_service
    proposal = _proposal(client, workspace, file, task)
    quarantine = Path(proposal["quarantine_path"])
    service._ensure_verified_quarantine_copy(
        source,
        quarantine,
        proposal["original_sha256"],
    )
    source.unlink()

    with client.app.state.database.session() as session:
        record = session.get(FileRecycleProposal, proposal["id"])
        assert record is not None
        record.status = "recycling"
        record.confirmed_at = datetime.now(timezone.utc)

    recovered = service.recover_incomplete_operations()
    assert recovered == {"recovered": 1, "blocked": 0}
    current = client.get(f"/recycle-proposals/{proposal['id']}").json()
    assert current["status"] == "recycled"
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    current_file = next(item for item in files if item["id"] == file["id"])
    assert current_file["status"] == "recycled"
    assert quarantine.is_file()


def test_phase15_startup_recovery_keeps_original_when_recycle_interrupted_early(
    client,
    tmp_path,
):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    service = client.app.state.file_recycle_service
    proposal = _proposal(client, workspace, file, task)
    quarantine = Path(proposal["quarantine_path"])
    service._ensure_verified_quarantine_copy(
        source,
        quarantine,
        proposal["original_sha256"],
    )

    with client.app.state.database.session() as session:
        record = session.get(FileRecycleProposal, proposal["id"])
        assert record is not None
        record.status = "recycling"

    recovered = service.recover_incomplete_operations()
    assert recovered == {"recovered": 1, "blocked": 0}
    assert source.exists()
    assert quarantine.exists()
    current = client.get(f"/recycle-proposals/{proposal['id']}").json()
    assert current["status"] == "pending"


def test_phase15_agent_can_only_stage_recoverable_recycle(client, tmp_path):
    workspace, _root, file, task, source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    arguments = {
        "file_id": file["id"],
        "summary": "Remove obsolete notes from the active Workspace",
    }
    provider = FakeAgentProvider(
        [
            _tool_step("propose_file_recycle", arguments, call_id="recycle_1"),
            _final("已生成可恢复回收提案，正在等待你确认；不会永久删除。"),
        ]
    )
    _wire_agent(client, provider)

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert source.exists()
    assert len(detail["recycle_proposals"]) == 1
    proposal = detail["recycle_proposals"][0]
    assert proposal["status"] == "pending"
    assert proposal["permanent_delete_available"] is False
    assert [call["tool_name"] for call in detail["tool_calls"]] == [
        "propose_file_recycle"
    ]
    assert detail["tool_calls"][0]["risk_level"] == 4

    second_items = provider.calls[1]["input_items"]
    output = next(
        item
        for item in second_items
        if item.get("type") == "function_call_output"
        and item.get("call_id") == "recycle_1"
    )
    payload = json.loads(output["output"])
    assert payload["result"]["requires_user_confirmation"] is True
    assert payload["result"]["permanent_delete_available"] is False


def test_phase15_no_permanent_delete_endpoint(client, tmp_path):
    workspace, _root, file, task, _source, _root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = _proposal(client, workspace, file, task)

    response = client.delete(f"/recycle-proposals/{proposal['id']}")
    assert response.status_code == 405
