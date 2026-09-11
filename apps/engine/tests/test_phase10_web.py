from __future__ import annotations

import json

import pytest

from app.ai.provider import AgentResponse, AgentToolRequest, _extract_web_sources
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


class FakeWebProvider:
    def __init__(self, responses: list[AgentResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.agent_calls: list[dict] = []
        self.web_calls: list[dict] = []

    async def agent_response(self, **kwargs):
        self.agent_calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Fake Agent provider ran out of responses")
        return self.responses.pop(0)

    async def web_search(self, **kwargs):
        self.web_calls.append(kwargs)
        return {
            "query": kwargs["query"],
            "answer": "OpenAI's current model documentation lists web search support.",
            "sources": [
                {
                    "title": "OpenAI Models",
                    "url": "https://platform.openai.com/docs/models",
                },
                {
                    "title": "OpenAI Tools",
                    "url": "https://platform.openai.com/docs/guides/tools",
                },
            ][: kwargs["max_sources"]],
            "response_id": "resp_web_1",
            "model": kwargs["model"],
            "input_tokens": 25,
            "output_tokens": 30,
        }


class DumpItem:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class FakeResponse:
    def __init__(self, output: list[DumpItem]) -> None:
        self.output = output


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
        input_tokens=40,
        output_tokens=10,
    )


def _final(text: str) -> AgentResponse:
    return AgentResponse(
        text=text,
        tool_calls=[],
        output_items=[{"type": "message", "role": "assistant"}],
        response_id="resp_final",
        model="gpt-5.6-sol",
        input_tokens=50,
        output_tokens=20,
    )


def _wire_provider(client, provider: FakeWebProvider) -> None:
    secret_store = AgentSecretStore()
    client.app.state.secret_store = secret_store
    client.app.state.agent_orchestrator.secret_store = secret_store
    client.app.state.agent_orchestrator.provider = provider
    client.app.state.web_research_service.secret_store = secret_store
    client.app.state.web_research_service.provider = provider


def test_phase10_provider_extracts_and_deduplicates_web_sources():
    response = FakeResponse(
        [
            DumpItem(
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "title": "Primary",
                                "url": "https://example.com/a",
                            },
                            {
                                "title": "Duplicate",
                                "url": "https://example.com/a",
                            },
                        ]
                    },
                }
            ),
            DumpItem(
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Result",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "title": "Secondary",
                                    "url": "https://example.com/b",
                                }
                            ],
                        }
                    ],
                }
            ),
        ]
    )

    assert _extract_web_sources(response, max_sources=5) == [
        {"title": "Primary", "url": "https://example.com/a"},
        {"title": "Secondary", "url": "https://example.com/b"},
    ]


def test_phase10_web_service_rejects_secret_like_queries(client):
    provider = FakeWebProvider()
    _wire_provider(client, provider)

    with pytest.raises(ValueError, match="credential-like"):
        client.app.state.web_research_service.search(
            query="Search this token sk-abcdefghijklmnopqrstuvwxyz123456 on the internet",
            max_sources=5,
        )

    assert provider.web_calls == []


def test_phase10_web_service_respects_local_only_mode(client):
    provider = FakeWebProvider()
    _wire_provider(client, provider)
    settings = client.patch("/settings", json={"privacy_mode": "local"})
    assert settings.status_code == 200

    with pytest.raises(ValueError, match="Local Only"):
        client.app.state.web_research_service.search(
            query="current public web information",
            max_sources=5,
        )

    assert provider.web_calls == []


def test_phase10_agent_can_search_web_and_preserve_sources(client):
    workspace = client.post("/workspaces", json={"name": "Web Research"}).json()
    provider = FakeWebProvider(
        [
            _tool_step(
                "search_web",
                {
                    "query": "OpenAI current models web search support",
                    "max_sources": 5,
                },
                call_id="web_1",
            ),
            _final(
                "OpenAI's current model documentation lists web search support. "
                "Sources: https://platform.openai.com/docs/models and "
                "https://platform.openai.com/docs/guides/tools"
            ),
        ]
    )
    _wire_provider(client, provider)

    task = client.post(
        "/tasks",
        json={
            "workspace_id": workspace["id"],
            "request": "Search the public web and give me the current OpenAI web-search support with sources.",
        },
    ).json()

    processed = client.post("/agent/process", params={"limit": 1}).json()
    assert processed["processed"] == 1

    detail = client.get(f"/tasks/{task['id']}").json()
    assert detail["status"] == "completed"
    assert "https://platform.openai.com/docs/models" in detail["result_text"]
    assert [call["tool_name"] for call in detail["tool_calls"]] == ["search_web"]
    assert detail["tool_calls"][0]["risk_level"] == 2
    assert provider.web_calls[0]["query"] == "OpenAI current models web search support"
    assert provider.web_calls[0]["max_sources"] == 5

    second_items = provider.agent_calls[1]["input_items"]
    web_output = next(
        item
        for item in second_items
        if item.get("type") == "function_call_output" and item.get("call_id") == "web_1"
    )
    payload = json.loads(web_output["output"])
    assert payload["result"]["sources"][0]["title"] == "OpenAI Models"
    assert payload["result"]["sources"][0]["url"] == "https://platform.openai.com/docs/models"

    tool_names = {
        item["name"]
        for item in provider.agent_calls[0]["tools"]
        if item.get("type") == "function"
    }
    assert "search_web" in tool_names
