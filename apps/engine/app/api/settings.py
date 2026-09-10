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
}


class DesktopSettings(BaseModel):
    privacy_mode: Literal["local", "hybrid", "cloud"] = "hybrid"
    default_model: str = Field(default="gpt-5.6-sol", min_length=1, max_length=255)
    reasoning_level: Literal["low", "medium", "high"] = "medium"
    auto_index: bool = True


class DesktopSettingsUpdate(BaseModel):
    privacy_mode: Literal["local", "hybrid", "cloud"] | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=255)
    reasoning_level: Literal["low", "medium", "high"] | None = None
    auto_index: bool | None = None


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
