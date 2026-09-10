from __future__ import annotations

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
