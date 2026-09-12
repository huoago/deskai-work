from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.database.models import Setting

router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULTS = {
    "privacy_mode": "hybrid",
    "default_model": "gpt-5.6-sol",
    "reasoning_level": "medium",
    "auto_index": True,
    "memory_auto_learn": True,
    "memory_min_confidence": 0.78,
    "embedding_provider": "local_hash",
    "embedding_model": "text-embedding-3-small",
}


class DesktopSettings(BaseModel):
    privacy_mode: Literal["local", "hybrid", "cloud"] = "hybrid"
    default_model: str = Field(default="gpt-5.6-sol", min_length=1, max_length=255)
    reasoning_level: Literal["low", "medium", "high"] = "medium"
    auto_index: bool = True
    memory_auto_learn: bool = True
    memory_min_confidence: float = Field(default=0.78, ge=0.5, le=1.0)
    embedding_provider: Literal["local_hash", "openai"] = "local_hash"
    embedding_model: Literal["text-embedding-3-small", "text-embedding-3-large"] = "text-embedding-3-small"


class DesktopSettingsUpdate(BaseModel):
    privacy_mode: Literal["local", "hybrid", "cloud"] | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=255)
    reasoning_level: Literal["low", "medium", "high"] | None = None
    auto_index: bool | None = None
    memory_auto_learn: bool | None = None
    memory_min_confidence: float | None = Field(default=None, ge=0.5, le=1.0)
    embedding_provider: Literal["local_hash", "openai"] | None = None
    embedding_model: Literal["text-embedding-3-small", "text-embedding-3-large"] | None = None


def _read_settings(request: Request) -> DesktopSettings:
    values = dict(DEFAULTS)
    with request.app.state.database.session() as session:
        for key in DEFAULTS:
            item = session.get(Setting, key)
            if item is not None:
                values[key] = item.value_json
    return DesktopSettings.model_validate(values)


@router.get("", response_model=DesktopSettings)
def get_settings(request: Request) -> DesktopSettings:
    return _read_settings(request)


@router.patch("", response_model=DesktopSettings)
def update_settings(payload: DesktopSettingsUpdate, request: Request) -> DesktopSettings:
    changes = payload.model_dump(exclude_none=True)
    with request.app.state.database.session() as session:
        for key, value in changes.items():
            item = session.get(Setting, key)
            if item is None:
                session.add(Setting(key=key, value_json=value))
            else:
                item.value_json = value
    return _read_settings(request)
