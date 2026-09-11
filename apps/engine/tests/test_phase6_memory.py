from __future__ import annotations

import json

from app.ai.provider import ProviderEvent
from app.security.secrets import SecretStatus


class MemorySecretStore:
    def get_openai_api_key(self) -> str | None:
        return "sk-test-memory-123456789012345678901234"

    def status(self) -> SecretStatus:
        return SecretStatus(
            configured=True,
            source="credential_manager",
            credential_store_available=True,
            writable=True,
        )


class MemoryProvider:
    def __init__(self) -> None:
        self.candidates: list[dict] = []
        self.extract_calls: list[dict] = []
        self.chat_calls: list[dict] = []

    async def extract_memories(self, **kwargs):
        self.extract_calls.append(kwargs)
        return list(self.candidates)

    async def stream_response(self, **kwargs):
        self.chat_calls.append(kwargs)
        yield ProviderEvent(type="delta", text="好的。")
        yield ProviderEvent(
            type="completed",
            response_id="resp_memory",
            model=kwargs["model"],
            input_tokens=20,
            output_tokens=3,
        )


def _setup(client) -> MemoryProvider:
    provider = MemoryProvider()
    client.app.state.secret_store = MemorySecretStore()
    client.app.state.openai_provider = provider
    client.app.state.memory_worker.provider = provider
    client.app.state.memory_worker.secret_store = client.app.state.secret_store
    return provider


def _chat(client, workspace_id: str, message: str, conversation_id: str | None = None) -> dict:
    payload = {"workspace_id": workspace_id, "message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    with client.stream("POST", "/chat/stream", json=payload) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    block = next(item for item in body.split("\n\n") if item.startswith("event: meta"))
    data = next(item for item in block.splitlines() if item.startswith("data: "))
    return json.loads(data.removeprefix("data: "))


def test_phase6_learns_global_preference_and_reuses_it_in_next_chat(client):
    provider = _setup(client)
    workspace = client.post("/workspaces", json={"name": "Memory Project"}).json()

    provider.candidates = [
        {
            "scope": "global",
            "type": "preference",
            "subject": "技术报告",
            "predicate": "默认语言",
            "value": "中文",
            "confidence": 0.98,
            "importance": 0.9,
        }
    ]
    first = _chat(client, workspace["id"], "以后技术报告默认用中文。")
    processed = client.post("/memory/process", params={"limit": 10}).json()
    assert processed["processed"] == 1

    memories = client.get("/memories", params={"workspace_id": workspace["id"]}).json()
    assert len(memories) == 1
    assert memories[0]["workspace_id"] is None
    assert memories[0]["type"] == "preference"
    assert memories[0]["value"] == "中文"

    provider.candidates = []
    _chat(client, workspace["id"], "帮我准备一份技术报告。", first["conversation_id"])
    rendered = json.dumps(provider.chat_calls[-1]["input_items"], ensure_ascii=False)
    assert "<memory_context>" in rendered
    assert "技术报告" in rendered
    assert "默认语言" in rendered
    assert "中文" in rendered


def test_phase6_correction_versions_existing_memory(client):
    provider = _setup(client)
    workspace = client.post("/workspaces", json={"name": "Correction Project"}).json()

    provider.candidates = [
        {
            "scope": "workspace",
            "type": "project_state",
            "subject": "324水表",
            "predicate": "总数",
            "value": "4475",
            "confidence": 0.95,
            "importance": 0.95,
        }
    ]
    first = _chat(client, workspace["id"], "324水表总数是4475。")
    assert client.post("/memory/process", params={"limit": 10}).json()["processed"] == 1

    provider.candidates = [
        {
            "scope": "workspace",
            "type": "correction",
            "subject": "324水表",
            "predicate": "总数",
            "value": "4447",
            "confidence": 0.99,
            "importance": 1.0,
        }
    ]
    _chat(client, workspace["id"], "纠正：324水表最终总数是4447，不是4475。", first["conversation_id"])
    assert client.post("/memory/process", params={"limit": 10}).json()["processed"] == 1

    memories = client.get(
        "/memories",
        params={"workspace_id": workspace["id"], "include_global": False},
    ).json()
    assert len(memories) == 1
    memory = memories[0]
    assert memory["value"] == "4447"
    assert memory["type"] == "correction"

    versions = client.get(f"/memories/{memory['id']}/versions").json()
    assert len(versions) == 1
    assert versions[0]["value"] == "4475"


def test_phase6_sensitive_candidate_is_rejected_even_if_model_returns_it(client):
    provider = _setup(client)
    workspace = client.post("/workspaces", json={"name": "Safety Project"}).json()

    provider.candidates = [
        {
            "scope": "global",
            "type": "preference",
            "subject": "OpenAI",
            "predicate": "API key",
            "value": "sk-test-very-secret-key-12345678901234567890",
            "confidence": 1.0,
            "importance": 1.0,
        }
    ]
    _chat(client, workspace["id"], "记住这个API Key。")
    assert client.post("/memory/process", params={"limit": 10}).json()["processed"] == 1
    assert client.get("/memories", params={"workspace_id": workspace["id"]}).json() == []


def test_phase6_auto_learning_can_be_disabled(client):
    provider = _setup(client)
    workspace = client.post("/workspaces", json={"name": "No Learn"}).json()
    update = client.patch("/settings", json={"memory_auto_learn": False})
    assert update.status_code == 200
    assert update.json()["memory_auto_learn"] is False

    provider.candidates = [
        {
            "scope": "global",
            "type": "preference",
            "subject": "报告",
            "predicate": "风格",
            "value": "简洁",
            "confidence": 0.99,
            "importance": 0.9,
        }
    ]
    _chat(client, workspace["id"], "以后报告写简洁一点。")
    assert client.post("/memory/process", params={"limit": 10}).json()["processed"] == 0
    assert provider.extract_calls == []


def test_phase6_manual_memory_edit_deactivate_and_versions(client):
    workspace = client.post("/workspaces", json={"name": "Manual Memory"}).json()
    created = client.post(
        "/memories",
        json={
            "workspace_id": workspace["id"],
            "type": "decision",
            "subject": "移交计划",
            "predicate": "目标日期",
            "value": "2026-10-01",
            "importance": 0.9,
        },
    )
    assert created.status_code == 201
    memory = created.json()
    assert memory["confidence"] == 1.0

    updated = client.patch(
        f"/memories/{memory['id']}",
        json={"value": "2026-10-05", "reason": "user corrected date"},
    )
    assert updated.status_code == 200
    assert updated.json()["value"] == "2026-10-05"

    versions = client.get(f"/memories/{memory['id']}/versions").json()
    assert versions[0]["value"] == "2026-10-01"
    assert versions[0]["reason"] == "user corrected date"

    deactivated = client.delete(f"/memories/{memory['id']}")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    assert client.get(
        "/memories",
        params={"workspace_id": workspace["id"], "include_inactive": False},
    ).json() == []


def test_phase6_workspace_memory_does_not_cross_projects(client):
    first = client.post("/workspaces", json={"name": "Project A"}).json()
    second = client.post("/workspaces", json={"name": "Project B"}).json()

    created = client.post(
        "/memories",
        json={
            "workspace_id": first["id"],
            "type": "project_state",
            "subject": "专属编号",
            "predicate": "当前值",
            "value": "A-778899",
            "importance": 0.95,
        },
    )
    assert created.status_code == 201

    hits_a = client.get(
        "/memories/search",
        params={"workspace_id": first["id"], "query": "A-778899"},
    ).json()["results"]
    hits_b = client.get(
        "/memories/search",
        params={"workspace_id": second["id"], "query": "A-778899"},
    ).json()["results"]
    assert hits_a
    assert hits_b == []
