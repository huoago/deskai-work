from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from docx import Document
from app.ai.provider import AgentResponse, AgentToolRequest
from app.database.models import FileOrganizationProposal
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
    content: str = "phase thirteen\n",
    write_allowed: bool = True,
):
    workspace = client.post("/workspaces", json={"name": f"Organize {filename}"}).json()
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
            "request": f"Organize {filename}",
        },
    ).json()
    return workspace, root, file, task, source, root_path


def test_phase13_rename_requires_confirmation_preserves_file_id_and_rolls_back(
    client,
    tmp_path,
):
    workspace, _root, file, task, source, root_path = _workspace_with_file(
        client,
        tmp_path,
    )

    proposal = client.app.state.file_organization_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        operation="rename",
        summary="Use a clearer filename",
        new_name="project-notes.md",
        target_relative_dir="",
    )
    target = root_path / "project-notes.md"

    assert proposal["status"] == "pending"
    assert proposal["requires_user_confirmation"] is True
    assert source.exists()
    assert not target.exists()

    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["file_operations"][0]["id"] == proposal["id"]

    applied_response = client.post(f"/file-operations/{proposal['id']}/confirm")
    assert applied_response.status_code == 200
    applied = applied_response.json()
    assert applied["status"] == "applied"
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "phase thirteen\n"

    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    current = next(item for item in files if item["id"] == file["id"])
    assert current["filename"] == "project-notes.md"
    assert Path(current["path"]) == target.resolve()
    assert current["sha256"] == file["sha256"]

    rollback_response = client.post(f"/file-operations/{proposal['id']}/rollback")
    assert rollback_response.status_code == 200
    assert rollback_response.json()["status"] == "rolled_back"
    assert source.read_text(encoding="utf-8") == "phase thirteen\n"
    assert not target.exists()

    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    restored = next(item for item in files if item["id"] == file["id"])
    assert restored["filename"] == "notes.md"
    assert Path(restored["path"]) == source.resolve()


def test_phase13_requires_write_permission_and_preserves_extension(client, tmp_path):
    workspace, root, file, task, _source, _root_path = _workspace_with_file(
        client,
        tmp_path,
        write_allowed=False,
    )

    with pytest.raises(ValueError, match="write_allowed"):
        client.app.state.file_organization_service.propose(
            task_id=task["id"],
            workspace_id=workspace["id"],
            file_id=file["id"],
            operation="rename",
            summary="Rename notes",
            new_name="renamed.md",
            target_relative_dir="",
        )

    update = client.patch(
        f"/workspaces/{workspace['id']}/roots/{root['id']}",
        json={"write_allowed": True},
    )
    assert update.status_code == 200

    with pytest.raises(ValueError, match="preserve the file extension"):
        client.app.state.file_organization_service.propose(
            task_id=task["id"],
            workspace_id=workspace["id"],
            file_id=file["id"],
            operation="rename",
            summary="Unsafe type change",
            new_name="renamed.txt",
            target_relative_dir="",
        )


def test_phase13_target_collision_and_stale_source_block_confirmation(client, tmp_path):
    workspace, _root, file, task, source, root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    collision = root_path / "collision.md"
    collision.write_text("occupied", encoding="utf-8")

    with pytest.raises(ValueError, match="Target path already exists"):
        client.app.state.file_organization_service.propose(
            task_id=task["id"],
            workspace_id=workspace["id"],
            file_id=file["id"],
            operation="rename",
            summary="Collision",
            new_name="collision.md",
            target_relative_dir="",
        )

    proposal = client.app.state.file_organization_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        operation="rename",
        summary="Rename after review",
        new_name="fresh.md",
        target_relative_dir="",
    )
    source.write_text("external modification", encoding="utf-8")

    response = client.post(f"/file-operations/{proposal['id']}/confirm")
    assert response.status_code == 409
    assert "changed after the organization proposal" in response.json()["detail"]
    assert source.read_text(encoding="utf-8") == "external modification"
    assert not (root_path / "fresh.md").exists()


def test_phase13_move_is_limited_to_existing_directory_in_same_root(client, tmp_path):
    workspace, _root, file, task, source, root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    archive = root_path / "archive"
    archive.mkdir()

    proposal = client.app.state.file_organization_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        operation="move",
        summary="Move notes into archive",
        new_name="",
        target_relative_dir="archive",
    )
    target = archive / source.name
    assert client.post(f"/file-operations/{proposal['id']}/confirm").status_code == 200
    assert target.exists()
    assert not source.exists()

    second_workspace, _root2, file2, task2, _source2, _root_path2 = _workspace_with_file(
        client,
        tmp_path / "second-parent",
        filename="second.md",
    )
    with pytest.raises(ValueError, match="same Workspace root"):
        client.app.state.file_organization_service.propose(
            task_id=task2["id"],
            workspace_id=second_workspace["id"],
            file_id=file2["id"],
            operation="move",
            summary="Escape root",
            new_name="",
            target_relative_dir="../other",
        )


def test_phase13_office_lock_blocks_confirm(client, tmp_path):
    workspace = client.post("/workspaces", json={"name": "Office organize"}).json()
    root_path = tmp_path / "office-organize"
    root_path.mkdir()
    source = root_path / "report.docx"
    document = Document()
    document.add_paragraph("DeskAI")
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
        json={"workspace_id": workspace["id"], "request": "Rename report"},
    ).json()

    proposal = client.app.state.file_organization_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        operation="rename",
        summary="Rename report",
        new_name="final-report.docx",
        target_relative_dir="",
    )
    (root_path / "~$report.docx").write_text("locked", encoding="utf-8")

    confirm = client.post(f"/file-operations/{proposal['id']}/confirm")
    assert confirm.status_code == 409
    assert "open in Microsoft Office" in confirm.json()["detail"]
    assert source.exists()
    assert not (root_path / "final-report.docx").exists()


def test_phase13_rollback_blocks_when_original_path_is_occupied(client, tmp_path):
    workspace, _root, file, task, source, root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    proposal = client.app.state.file_organization_service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        operation="rename",
        summary="Rename notes",
        new_name="renamed.md",
        target_relative_dir="",
    )
    assert client.post(f"/file-operations/{proposal['id']}/confirm").status_code == 200
    source.write_text("new occupant", encoding="utf-8")

    rollback = client.post(f"/file-operations/{proposal['id']}/rollback")
    assert rollback.status_code == 409
    assert "Original path is occupied" in rollback.json()["detail"]
    assert (root_path / "renamed.md").exists()
    assert source.read_text(encoding="utf-8") == "new occupant"


def test_phase13_startup_recovery_finishes_interrupted_apply(client, tmp_path):
    workspace, _root, file, task, source, root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    service = client.app.state.file_organization_service
    proposal = service.propose(
        task_id=task["id"],
        workspace_id=workspace["id"],
        file_id=file["id"],
        operation="rename",
        summary="Recover rename",
        new_name="recovered.md",
        target_relative_dir="",
    )
    target = root_path / "recovered.md"

    with client.app.state.database.session() as session:
        record = session.get(FileOrganizationProposal, proposal["id"])
        assert record is not None
        record.status = "applying"
        record.confirmed_at = datetime.now(timezone.utc)

    service._move_no_overwrite(source, target)
    assert target.exists() and not source.exists()

    recovered = service.recover_incomplete_operations()
    assert recovered == {"recovered": 1, "blocked": 0}

    current = client.get(f"/file-operations/{proposal['id']}").json()
    assert current["status"] == "applied"
    files = client.get(f"/files?workspace_id={workspace['id']}").json()
    same_file = next(item for item in files if item["id"] == file["id"])
    assert Path(same_file["path"]) == target.resolve()


def test_phase13_agent_can_only_stage_file_organization(client, tmp_path):
    workspace, _root, file, task, source, root_path = _workspace_with_file(
        client,
        tmp_path,
    )
    arguments = {
        "file_id": file["id"],
        "operation": "rename",
        "summary": "Rename project notes",
        "new_name": "agent-notes.md",
        "target_relative_dir": "",
    }
    provider = FakeAgentProvider(
        [
            _tool_step(
                "propose_file_organization",
                arguments,
                call_id="organize_1",
            ),
            _final("文件整理提案已经生成，正在等待你确认路径变更。"),
        ]
    )
    _wire_agent(client, provider)

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert source.exists()
    assert not (root_path / "agent-notes.md").exists()
    assert len(detail["file_operations"]) == 1
    operation = detail["file_operations"][0]
    assert operation["status"] == "pending"
    assert operation["requires_user_confirmation"] is True
    assert [call["tool_name"] for call in detail["tool_calls"]] == [
        "propose_file_organization"
    ]
    assert detail["tool_calls"][0]["risk_level"] == 3

    second_items = provider.calls[1]["input_items"]
    output = next(
        item
        for item in second_items
        if item.get("type") == "function_call_output"
        and item.get("call_id") == "organize_1"
    )
    payload = json.loads(output["output"])
    assert payload["result"]["status"] == "pending"
    assert payload["result"]["requires_user_confirmation"] is True
