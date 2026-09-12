from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["recovery-reconciliation"])


def _raise_api_error(exc: ValueError) -> None:
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 409
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/recovery-reconciliations")
def list_recovery_reconciliations(
    request: Request,
    workspace_id: str | None = Query(default=None),
    transaction_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return request.app.state.recovery_reconciliation_service.list(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        limit=limit,
    )


@router.get("/recovery-reconciliations/{proposal_id}")
def get_recovery_reconciliation(
    proposal_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.recovery_reconciliation_service.get(proposal_id)
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recovery-reconciliations/{entity_type}/{transaction_id}")
def propose_recovery_reconciliation(
    entity_type: str,
    transaction_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.recovery_reconciliation_service.propose(
            entity_type,
            transaction_id,
        )
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recovery-reconciliations/{proposal_id}/confirm")
def confirm_recovery_reconciliation(
    proposal_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.recovery_reconciliation_service.confirm(
            proposal_id
        )
    except ValueError as exc:
        _raise_api_error(exc)


@router.post("/recovery-reconciliations/{proposal_id}/reject")
def reject_recovery_reconciliation(
    proposal_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.recovery_reconciliation_service.reject(
            proposal_id
        )
    except ValueError as exc:
        _raise_api_error(exc)
