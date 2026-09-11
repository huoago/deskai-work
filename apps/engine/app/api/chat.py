from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.context import SYSTEM_INSTRUCTIONS, build_input_items, build_retrieved_context
from app.ai.provider import ProviderError
from app.api.settings import DEFAULTS
from app.database.models import Citation, Conversation, File, Message, Setting, Workspace, utcnow
from app.memory.context import build_memory_context

router = APIRouter(tags=["chat"])
CITATION_RE = re.compile(r"\[(\d{1,3})\]")


class ConversationCreate(BaseModel):
    workspace_id: str | None = None
    title: str | None = Field(default=None, max_length=1024)


class ConversationResponse(BaseModel):
    id: str
    workspace_id: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageCitationResponse(BaseModel):
    source_index: int | None = None
    chunk_id: str | None = None
    file_id: str | None = None
    label: str
    locator: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime
    citations: list[MessageCitationResponse] = Field(default_factory=list)


class ChatRequest(BaseModel):
    workspace_id: str | None = None
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=100_000)


def _conversation_response(item: Conversation) -> ConversationResponse:
    return ConversationResponse.model_validate(item, from_attributes=True)


def _citation_responses(session: Session, message_id: str) -> list[MessageCitationResponse]:
    citations = session.scalars(
        select(Citation)
        .where(Citation.message_id == message_id)
        .order_by(Citation.created_at, Citation.id)
    ).all()
    result: list[MessageCitationResponse] = []
    for citation in citations:
        locator_json = dict(citation.locator_json or {})
        source_index = locator_json.pop("source_index", None)
        label = str(locator_json.pop("citation_label", "") or "")
        if not label and citation.file_id:
            file = session.get(File, citation.file_id)
            label = file.filename if file is not None else "Local source"
        result.append(
            MessageCitationResponse(
                source_index=int(source_index) if source_index is not None else None,
                chunk_id=citation.chunk_id,
                file_id=citation.file_id,
                label=label or "Local source",
                locator=locator_json,
            )
        )
    return result


def _message_response(session: Session, item: Message) -> MessageResponse:
    return MessageResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        role=item.role,
        content=item.content,
        created_at=item.created_at,
        citations=_citation_responses(session, item.id),
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat_setting(session: Session, key: str) -> Any:
    item = session.get(Setting, key)
    return item.value_json if item is not None else DEFAULTS[key]


def _prepare_conversation(
    session: Session,
    *,
    workspace_id: str | None,
    conversation_id: str | None,
    message_text: str,
) -> tuple[Conversation | None, list[tuple[str, str]]]:
    if workspace_id and session.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    conversation: Conversation | None = None
    history: list[tuple[str, str]] = []
    if conversation_id:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if workspace_id and conversation.workspace_id != workspace_id:
            raise HTTPException(status_code=409, detail="Conversation belongs to another workspace")
        items = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.id)
        ).all()
        history = [(item.role, item.content) for item in items]

    return conversation, history


def _used_source_indices(answer: str, source_count: int) -> list[int]:
    return sorted(
        {
            int(match)
            for match in CITATION_RE.findall(answer)
            if 1 <= int(match) <= source_count
        }
    )


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    request: Request,
    workspace_id: str | None = Query(default=None),
) -> list[ConversationResponse]:
    with request.app.state.database.session() as session:
        statement = select(Conversation)
        if workspace_id is not None:
            statement = statement.where(Conversation.workspace_id == workspace_id)
        statement = statement.order_by(Conversation.updated_at.desc())
        return [_conversation_response(item) for item in session.scalars(statement).all()]


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(payload: ConversationCreate, request: Request) -> ConversationResponse:
    with request.app.state.database.session() as session:
        if payload.workspace_id and session.get(Workspace, payload.workspace_id) is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        item = Conversation(workspace_id=payload.workspace_id, title=payload.title or "新对话")
        session.add(item)
        session.flush()
        return _conversation_response(item)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
def list_messages(conversation_id: str, request: Request) -> list[MessageResponse]:
    with request.app.state.database.session() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        items = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        ).all()
        return [_message_response(session, item) for item in items]


@router.post("/chat/stream")
def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    message_text = payload.message.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    with request.app.state.database.session() as session:
        conversation, history = _prepare_conversation(
            session,
            workspace_id=payload.workspace_id,
            conversation_id=payload.conversation_id,
            message_text=message_text,
        )
        privacy_mode = str(_chat_setting(session, "privacy_mode"))
        model = str(_chat_setting(session, "default_model"))
        reasoning_effort = str(_chat_setting(session, "reasoning_level"))
        memory_auto_learn = bool(_chat_setting(session, "memory_auto_learn"))
        effective_workspace_id = payload.workspace_id or (
            conversation.workspace_id if conversation is not None else None
        )

    if privacy_mode == "local":
        raise HTTPException(
            status_code=409,
            detail="Local Only mode is enabled, but a local language model is not configured yet.",
        )

    api_key = request.app.state.secret_store.get_openai_api_key()
    if not api_key:
        raise HTTPException(
            status_code=409,
            detail="OpenAI API key is required for Hybrid/Cloud chat. Configure it in Settings.",
        )

    hits: list[dict[str, Any]] = []
    if effective_workspace_id:
        hits = request.app.state.hybrid_search.search(effective_workspace_id, message_text, limit=8)
    retrieved_context = build_retrieved_context(hits)
    memory_hits = request.app.state.memory_service.retrieve(
        workspace_id=effective_workspace_id,
        query=message_text,
        limit=8,
    )
    memory_context = build_memory_context(memory_hits)
    input_items = build_input_items(
        history,
        message_text,
        retrieved_context + memory_context,
    )

    with request.app.state.database.session() as session:
        if conversation is None:
            title = message_text.replace("\n", " ")[:60]
            conversation = Conversation(workspace_id=effective_workspace_id, title=title)
            session.add(conversation)
            session.flush()

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=message_text,
        )
        conversation.updated_at = utcnow()
        session.add(user_message)
        session.flush()
        conversation_id = conversation.id
        user_message_id = user_message.id

    source_meta = [
        {
            "source_index": index,
            "chunk_id": hit.get("chunk_id"),
            "file_id": hit.get("file_id"),
            "label": hit.get("citation_label") or hit.get("filename"),
            "locator": hit.get("locator") or {},
        }
        for index, hit in enumerate(hits, start=1)
    ]

    async def generate():
        yield _sse(
            "meta",
            {
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "transport": "openai-responses",
                "model": model,
                "privacy_mode": privacy_mode,
                "source_count": len(source_meta),
                "memory_count": len(memory_hits),
            },
        )
        if source_meta:
            yield _sse("sources", {"sources": source_meta})

        assistant_parts: list[str] = []
        completion: dict[str, Any] = {
            "response_id": None,
            "model": model,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        try:
            async for event in request.app.state.openai_provider.stream_response(
                api_key=api_key,
                model=model,
                reasoning_effort=reasoning_effort,
                instructions=SYSTEM_INSTRUCTIONS,
                input_items=input_items,
            ):
                if event.type == "delta":
                    assistant_parts.append(event.text)
                    yield _sse("delta", {"text": event.text})
                elif event.type == "completed":
                    completion = {
                        "response_id": event.response_id,
                        "model": event.model or model,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                    }
        except ProviderError as exc:
            safe_message = str(exc).replace(api_key, "***")
            yield _sse(
                "error",
                {
                    "code": "OPENAI_PROVIDER_ERROR",
                    "message": safe_message,
                    "recoverable": True,
                },
            )
            return
        except Exception as exc:
            yield _sse(
                "error",
                {
                    "code": "AI_STREAM_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "recoverable": True,
                },
            )
            return

        assistant_text = "".join(assistant_parts).strip()
        if not assistant_text:
            yield _sse(
                "error",
                {
                    "code": "EMPTY_MODEL_RESPONSE",
                    "message": "The model completed without returning answer text.",
                    "recoverable": True,
                },
            )
            return

        used_indices = _used_source_indices(assistant_text, len(hits))
        stored_citations: list[dict[str, Any]] = []
        with request.app.state.database.session() as session:
            assistant_message = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text,
            )
            conversation_record = session.get(Conversation, conversation_id)
            if conversation_record is not None:
                conversation_record.updated_at = utcnow()
            session.add(assistant_message)
            session.flush()

            for source_index in used_indices:
                hit = hits[source_index - 1]
                locator = dict(hit.get("locator") or {})
                label = str(hit.get("citation_label") or hit.get("filename") or "Local source")
                citation = Citation(
                    message_id=assistant_message.id,
                    chunk_id=hit.get("chunk_id"),
                    file_id=hit.get("file_id"),
                    locator_json={
                        **locator,
                        "source_index": source_index,
                        "citation_label": label,
                    },
                )
                session.add(citation)
                stored_citations.append(
                    {
                        "source_index": source_index,
                        "chunk_id": hit.get("chunk_id"),
                        "file_id": hit.get("file_id"),
                        "label": label,
                        "locator": locator,
                    }
                )
            assistant_message_id = assistant_message.id

        memory_job_id = None
        if memory_auto_learn:
            memory_job = request.app.state.memory_service.enqueue_learning(
                workspace_id=effective_workspace_id,
                conversation_id=conversation_id,
                source_message_id=user_message_id,
            )
            memory_job_id = memory_job.id
            request.app.state.memory_worker.wake()

        yield _sse(
            "done",
            {
                "assistant_message_id": assistant_message_id,
                "citations": stored_citations,
                "memory_job_id": memory_job_id,
                **completion,
            },
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
