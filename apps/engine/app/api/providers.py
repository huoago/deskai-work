from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.ai.provider import ProviderError
from app.database.models import Setting
from app.api.settings import DEFAULTS

router = APIRouter(prefix="/providers/openai", tags=["providers"])


class ApiKeyUpdate(BaseModel):
    api_key: str = Field(min_length=20, max_length=512)


def _current_model(request: Request) -> str:
    with request.app.state.database.session() as session:
        item = session.get(Setting, "default_model")
        return str(item.value_json) if item is not None else str(DEFAULTS["default_model"])


@router.get("/status")
def openai_status(request: Request) -> dict:
    status = request.app.state.secret_store.status().as_dict()
    status["model"] = _current_model(request)
    return status


@router.put("/api-key")
def save_openai_api_key(payload: ApiKeyUpdate, request: Request) -> dict:
    try:
        request.app.state.secret_store.set_openai_api_key(payload.api_key)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    status = request.app.state.secret_store.status().as_dict()
    status["model"] = _current_model(request)
    return status


@router.delete("/api-key")
def delete_openai_api_key(request: Request) -> dict:
    try:
        deleted = request.app.state.secret_store.delete_openai_api_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    status = request.app.state.secret_store.status().as_dict()
    status["deleted"] = deleted
    status["model"] = _current_model(request)
    return status


@router.post("/test")
async def test_openai_connection(request: Request) -> dict:
    api_key = request.app.state.secret_store.get_openai_api_key()
    if not api_key:
        raise HTTPException(status_code=409, detail="OpenAI API key is not configured")
    model = _current_model(request)
    try:
        result = await request.app.state.openai_provider.test_connection(
            api_key=api_key,
            model=model,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI connection failed: {exc}") from exc
    return result
