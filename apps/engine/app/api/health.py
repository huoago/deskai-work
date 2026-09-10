from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__

router = APIRouter(tags=["system"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    database = request.app.state.database
    db_ok = database.healthcheck()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": "deskai-engine",
        "version": __version__,
        "database": "ok" if db_ok else "error",
        "database_path": str(database.database_path),
    }
