from __future__ import annotations

import json

from app.ai.provider import AgentResponse, AgentToolRequest
from app.database.models import File
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


def _wire_provider(client, provider: FakeAgentProvider) -> None:
    secret_store = AgentSecretStore()
    client.app.state.secret_store = secret_store
    client.app.state.agent_orchestrator.secret_store = secret_store
    client.app.state.agent_orchestrator.provider = provider


def _tool_step(name: str, arguments: dict, *, call_id: str = "call_1") -> AgentResponse:
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
        response_id="resp_tool",
        model="gpt-5.6-sol",
        input_tokens=50,
        output_tokens=10,
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


def test_phase7_agent_uses_memory_tool_and_persists_audit_chain(client):
    workspace = client.post("/workspaces", json={"name": "Agent Project"}).json()
    memory = client.post(
        "/memories",
        json={
            "workspace_id": workspace["id"],
            "type": "project_state",
            "subject": "324水表",
            "predicate": "总数",
            "value": "4447",
            "importance": 0.95,
        },
    )
    assert memory.status_code == 201

    provider = FakeAgentProvider(
        [
            _tool_step("search_memory", {"query": "324水表总数", "limit": 5}),
            _final("324水表总数为4447。"),
        ]
    )
    _wire_provider(client, provider)

    created = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "查一下已确认的324水表总数并给我结论。",
        },
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    processed = client.post("/agent/process", params={"limit": 5}).json()
    assert processed["processed"] == 1

    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["status"] == "completed"
    assert detail["progress"] == 1.0
    assert "4447" in detail["result_text"]
    assert len(detail["runs"]) == 1
    assert detail["runs"][0]["input_tokens"] == 110
    assert detail["runs"][0]["output_tokens"] == 30
    assert len(detail["tool_calls"]) == 1
    assert detail["tool_calls"][0]["tool_name"] == "search_memory"
    assert detail["tool_calls"][0]["status"] == "completed"

    assert len(provider.calls) == 2
    second_input = json.dumps(provider.calls[1]["input_items"], ensure_ascii=False)
    assert "function_call_output" in second_input
    assert "4447" in second_input

    activity = client.get("/activity", params={"workspace_id": workspace["id"]}).json()
    actions = {item["action"] for item in activity}
    assert {"agent_started", "tool_completed", "agent_completed"} <= actions


def test_phase7_workspace_file_tool_never_crosses_workspace_boundary(client):
    first = client.post("/workspaces", json={"name": "Project A"}).json()
    second = client.post("/workspaces", json={"name": "Project B"}).json()
    with client.app.state.database.session() as session:
        session.add(
            File(
                workspace_id=first["id"],
                path="/authorized/project-a/a.txt",
                filename="A-only.txt",
                extension=".txt",
                size=10,
                status="indexed",
            )
        )
        session.add(
            File(
                workspace_id=second["id"],
                path="/authorized/project-b/b.txt",
                filename="B-secret.txt",
                extension=".txt",
                size=10,
                status="indexed",
            )
        )

    provider = FakeAgentProvider(
        [
            _tool_step("list_workspace_files", {"status": "any", "limit": 100}),
            _final("已检查当前项目文件。"),
        ]
    )
    _wire_provider(client, provider)
    task = client.post(
        "/tasks",
        json={"workspace_id": first["id"], "request": "列出当前项目可见资料。"},
    ).json()

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    assert client.get(f"/tasks/{task['id']}").json()["status"] == "completed"

    second_input = json.dumps(provider.calls[1]["input_items"], ensure_ascii=False)
    assert "A-only.txt" in second_input
    assert "B-secret.txt" not in second_input


def test_phase7_permission_gate_denies_unregistered_destructive_tool(client):
    workspace = client.post("/workspaces", json={"name": "Safety Agent"}).json()
    provider = FakeAgentProvider(
        [
            _tool_step("delete_file", {"file_id": "dangerous-id"}),
            _final("删除操作未执行，因为该工具没有授权。"),
        ]
    )
    _wire_provider(client, provider)
    task = client.post(
        "/tasks",
        json={"workspace_id": workspace["id"], "request": "删除一个文件。"},
    ).json()

    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1
    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert len(detail["tool_calls"]) == 1
    call = detail["tool_calls"][0]
    assert call["tool_name"] == "delete_file"
    assert call["status"] == "denied"
    assert call["risk_level"] == 8
    assert call["confirmation_required"] is True

    second_input = json.dumps(provider.calls[1]["input_items"], ensure_ascii=False)
    assert "not enabled by the current Agent policy" in second_input
    activity = client.get("/activity", params={"workspace_id": workspace["id"]}).json()
    assert any(item["action"] == "tool_denied" for item in activity)


def test_phase7_local_only_blocks_agent_then_retry_succeeds(client):
    workspace = client.post("/workspaces", json={"name": "Local Gate"}).json()
    provider = FakeAgentProvider([_final("任务已完成。")])
    _wire_provider(client, provider)

    client.patch("/settings", json={"privacy_mode": "local"})
    task = client.post(
        "/tasks",
        json={"workspace_id": workspace["id"], "request": "检查一下项目情况。"},
    ).json()
    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1

    blocked = client.get(f"/tasks/{task['id']}").json()
    assert blocked["status"] == "blocked"
    assert "Local Only" in blocked["error_message"]
    assert provider.calls == []

    client.patch("/settings", json={"privacy_mode": "hybrid"})
    retried = client.post(f"/tasks/{task['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert client.post("/agent/process", params={"limit": 1}).json()["processed"] == 1

    completed = client.get(f"/tasks/{task['id']}").json()
    assert completed["status"] == "completed"
    assert completed["result_text"] == "任务已完成。"
    assert len(provider.calls) == 1


def test_phase7_task_list_and_agent_status_are_workspace_scoped(client):
    first = client.post("/workspaces", json={"name": "Task A"}).json()
    second = client.post("/workspaces", json={"name": "Task B"}).json()
    client.post("/tasks", json={"workspace_id": first["id"], "request": "A task"})
    client.post("/tasks", json={"workspace_id": second["id"], "request": "B task"})

    tasks = client.get("/tasks", params={"workspace_id": first["id"]}).json()
    assert len(tasks) == 1
    assert tasks[0]["user_request"] == "A task"

    status = client.get("/agent/status", params={"workspace_id": first["id"]}).json()
    assert status["workspace_id"] == first["id"]
    assert status["task_counts"]["pending"] == 1
