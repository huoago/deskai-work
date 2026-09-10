from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.database.models import Workspace, WorkspaceRoot
from app.indexing.scanner import scan_root
from app.security.paths import PathAccessError, normalize_root

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["sources"])


class RootCreate(BaseModel):
    path: str
    read_allowed: bool = True
    write_allowed: bool = False
    watch_enabled: bool = True
    scan_now: bool = True


class RootResponse(BaseModel):
    id: str
    workspace_id: str
    path: str
    read_allowed: bool
    write_allowed: bool
    watch_enabled: bool


@router.get("/roots", response_model=list[RootResponse])
def list_roots(workspace_id: str, request: Request) -> list[WorkspaceRoot]:
    with request.app.state.database.session() as session:
        return list(
            session.scalars(
                select(WorkspaceRoot)
                .where(WorkspaceRoot.workspace_id == workspace_id)
                .order_by(WorkspaceRoot.created_at)
            ).all()
        )


@router.post("/roots", status_code=status.HTTP_201_CREATED)
def add_root(workspace_id: str, payload: RootCreate, request: Request) -> dict:
    try:
        root_path = normalize_root(payload.path)
    except (OSError, PathAccessError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with request.app.state.database.session() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")

        canonical = str(root_path)
        root = session.scalar(
            select(WorkspaceRoot).where(
                WorkspaceRoot.workspace_id == workspace_id,
                WorkspaceRoot.path == canonical,
            )
        )
        if root is None:
            root = WorkspaceRoot(
                workspace_id=workspace_id,
                path=canonical,
                read_allowed=payload.read_allowed,
                write_allowed=payload.write_allowed,
                watch_enabled=payload.watch_enabled,
            )
            session.add(root)
            session.flush()
        else:
            root.read_allowed = payload.read_allowed
            root.write_allowed = payload.write_allowed
            root.watch_enabled = payload.watch_enabled

        scan = scan_root(session, root).as_dict() if payload.scan_now and root.read_allowed else None
        return {
            "root": RootResponse.model_validate(root, from_attributes=True).model_dump(),
            "scan": scan,
        }


@router.post("/scan")
def scan_workspace(workspace_id: str, request: Request) -> dict:
    with request.app.state.database.session() as session:
        roots = session.scalars(
            select(WorkspaceRoot).where(
                WorkspaceRoot.workspace_id == workspace_id,
                WorkspaceRoot.read_allowed.is_(True),
            )
        ).all()
        if not roots:
            return {"roots": 0, "summary": {"discovered": 0, "queued": 0, "unchanged": 0, "unsupported": 0, "skipped": 0, "deleted": 0}}
        summary = {"discovered": 0, "queued": 0, "unchanged": 0, "unsupported": 0, "skipped": 0, "deleted": 0}
        for root in roots:
            stats = scan_root(session, root).as_dict()
            for key, value in stats.items():
                summary[key] += value
        return {"roots": len(roots), "summary": summary}
