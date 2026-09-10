from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database.models import Conversation, Message, Workspace, utcnow

router = APIRouter(tags=["chat"])


class ConversationCreate(BaseModel):
    workspace_id: str | None = None
    title: str | None = Field(default=None, max_length=1024)


class ConversationResponse(BaseModel):
    id: str
    workspace_id: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime


class ChatRequest(BaseModel):
    workspace_id: str | None = None
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=100_000)


def _conversation_response(item: Conversation) -> ConversationResponse:
    return ConversationResponse.model_validate(item, from_attributes=True)


def _message_response(item: Message) -> MessageResponse:
    return MessageResponse.model_validate(item, from_attributes=True)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
        return [_message_response(item) for item in items]


@router.post("/chat/stream")
def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    message_text = payload.message.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    with request.app.state.database.session() as session:
        if payload.workspace_id and session.get(Workspace, payload.workspace_id) is None:
            raise HTTPException(status_code=404, detail="Workspace not found")

        conversation: Conversation | None = None
        if payload.conversation_id:
            conversation = session.get(Conversation, payload.conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            if payload.workspace_id and conversation.workspace_id != payload.workspace_id:
                raise HTTPException(status_code=409, detail="Conversation belongs to another workspace")
        else:
            title = message_text.replace("\n", " ")[:60]
            conversation = Conversation(workspace_id=payload.workspace_id, title=title)
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

    assistant_text = (
        "本地聊天通道已正常工作，本次消息已经保存到当前会话。"
        "当前 Phase 1 只负责真实的会话持久化与流式传输；"
        "知识检索与 OpenAI Responses API 会在后续阶段接入。\n\n"
        f"收到的内容：{message_text}"
    )

    async def generate():
        yield _sse(
            "meta",
            {
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "transport": "local-phase1",
            },
        )
        chunk_size = 18
        for start in range(0, len(assistant_text), chunk_size):
            yield _sse("delta", {"text": assistant_text[start : start + chunk_size]})
            await asyncio.sleep(0.005)

        with request.app.state.database.session() as session:
            assistant_message = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_text,
            )
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.updated_at = utcnow()
            session.add(assistant_message)
            session.flush()
            assistant_message_id = assistant_message.id

        yield _sse("done", {"assistant_message_id": assistant_message_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
