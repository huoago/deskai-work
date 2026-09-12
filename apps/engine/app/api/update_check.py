from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.errors import AppError
from app.database.models import Setting
from app.update_check.service import ReleaseVerificationService, UpdateCheckError

router = APIRouter(prefix="/updates", tags=["updates"])


class UpdateCheckRequest(BaseModel):
    current_version: str = Field(min_length=5, max_length=32, pattern=r"^\d+\.\d+\.\d+$")


def _privacy_mode(request: Request) -> str:
    with request.app.state.database.session() as session:
        item = session.get(Setting, "privacy_mode")
        if item is None:
            return "hybrid"
        return str(item.value_json)


@router.post("/check")
def check_for_updates(payload: UpdateCheckRequest, request: Request) -> dict[str, object]:
    if _privacy_mode(request) == "local":
        raise AppError(
            "UPDATE_CHECK_BLOCKED_LOCAL_ONLY",
            "Update checks are disabled while DeskAI is in Local Only mode.",
            details={"privacy_mode": "local"},
        )
    service = ReleaseVerificationService()
    try:
        result = service.check_latest(payload.current_version)
    except UpdateCheckError as exc:
        raise AppError(
            "UPDATE_CHECK_FAILED",
            str(exc),
            details={},
        ) from exc
    return result.to_dict()
