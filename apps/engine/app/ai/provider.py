from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator, Any

from openai import AsyncOpenAI, OpenAIError


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class ProviderEvent:
    type: str
    text: str = ""
    response_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class OpenAIChatProvider:
    async def test_connection(self, *, api_key: str, model: str) -> dict[str, Any]:
        try:
            client = AsyncOpenAI(api_key=api_key)
            result = await client.models.retrieve(model)
            return {"ok": True, "model": getattr(result, "id", model)}
        except OpenAIError as exc:
            raise ProviderError(str(exc)) from exc


    async def extract_memories(
        self,
        *,
        api_key: str,
        model: str,
        current_user_message: str,
        conversation_context: str,
    ) -> list[dict[str, Any]]:
        schema = {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "scope": {"type": "string", "enum": ["global", "workspace"]},
                            "type": {
                                "type": "string",
                                "enum": [
                                    "preference",
                                    "decision",
                                    "constraint",
                                    "correction",
                                    "project_state",
                                    "workflow",
                                    "person_role",
                                ],
                            },
                            "subject": {"type": "string", "maxLength": 500},
                            "predicate": {"type": "string", "maxLength": 255},
                            "value": {"type": "string", "maxLength": 4000},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "importance": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": [
                            "scope",
                            "type",
                            "subject",
                            "predicate",
                            "value",
                            "confidence",
                            "importance",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["memories"],
            "additionalProperties": False,
        }
        instructions = """Extract only durable user-provided memories from the CURRENT USER MESSAGE.

The previous conversation is context only for resolving references such as 'that' or 'from now on'. Never turn assistant-generated claims into memory unless the current user message explicitly confirms or corrects them.

Store only durable preferences, decisions, constraints, corrections, project state, workflows, and person/role relationships. Do not store transient requests, greetings, file contents, model answers, or facts that merely appear in retrieved documents.

Never extract passwords, API keys, tokens, financial credentials, government identifiers, health/medical information, race/ethnicity, religion, political affiliation, sexual orientation/sex life, or trade-union membership.

Use scope=global only for durable cross-project preferences/constraints/workflows. Use scope=workspace for project-specific items. If nothing clearly deserves long-term memory, return an empty memories array."""
        user_input = (
            "<previous_context>\n"
            + conversation_context
            + "\n</previous_context>\n\n"
            + "<current_user_message>\n"
            + current_user_message
            + "\n</current_user_message>"
        )
        try:
            client = AsyncOpenAI(api_key=api_key)
            response = await client.responses.create(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": user_input}],
                reasoning={"effort": "low"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "deskai_memory_extraction",
                        "strict": True,
                        "schema": schema,
                    }
                },
                store=False,
            )
            payload = json.loads(response.output_text or '{"memories":[]}')
            memories = payload.get("memories", [])
            return memories if isinstance(memories, list) else []
        except (OpenAIError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProviderError(f"Memory extraction failed: {exc}") from exc

    async def stream_response(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        instructions: str,
        input_items: list[dict[str, Any]],
    ) -> AsyncIterator[ProviderEvent]:
        try:
            client = AsyncOpenAI(api_key=api_key)
            stream = await client.responses.create(
                model=model,
                instructions=instructions,
                input=input_items,
                reasoning={"effort": reasoning_effort},
                stream=True,
                store=False,
            )
            async for event in stream:
                event_type = str(getattr(event, "type", ""))
                if event_type == "response.output_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if delta:
                        yield ProviderEvent(type="delta", text=delta)
                elif event_type == "response.completed":
                    response = getattr(event, "response", None)
                    usage = getattr(response, "usage", None)
                    yield ProviderEvent(
                        type="completed",
                        response_id=getattr(response, "id", None),
                        model=getattr(response, "model", model),
                        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                    )
                elif event_type == "response.failed":
                    response = getattr(event, "response", None)
                    error = getattr(response, "error", None)
                    message = getattr(error, "message", None) or "OpenAI response failed"
                    raise ProviderError(str(message))
        except OpenAIError as exc:
            raise ProviderError(str(exc)) from exc
