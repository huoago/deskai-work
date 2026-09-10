from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.database.models import Workspace, WorkspaceRoot
from app.indexing.scanner import ScanStats, reconcile_workspace_access, scan_root
from app.security.paths import PathAccessError, normalize_root

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["sources"])


class RootCreate(BaseModel):
    path: str
    read_allowed: bool = True
    write_allowed: bool = False
    watch_enabled: bool = True
    scan_now: bool = True


class RootUpdate(BaseModel):
    read_allowed: bool | None = None
    write_allowed: bool | None = None
    watch_enabled: bool | None = None
    scan_now: bool = False


class RootResponse(BaseModel):
    id: str
    workspace_id: str
    path: str
    read_allowed: bool
    write_allowed: bool
    watch_enabled: bool


def _wake_watcher(request: Request) -> None:
    watcher = getattr(request.app.state, "workspace_watcher", None)
    if watcher is not None:
        watcher.wake()


def _root_or_404(session, workspace_id: str, root_id: str) -> WorkspaceRoot:
    root = session.get(WorkspaceRoot, root_id)
    if root is None or root.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Workspace root not found")
    return root


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
        if not root.read_allowed:
            reconcile_workspace_access(session, workspace_id)
        response = {
            "root": RootResponse.model_validate(root, from_attributes=True).model_dump(),
            "scan": scan,
        }

    _wake_watcher(request)
    return response


@router.patch("/roots/{root_id}")
def update_root(workspace_id: str, root_id: str, payload: RootUpdate, request: Request) -> dict:
    with request.app.state.database.session() as session:
        root = _root_or_404(session, workspace_id, root_id)
        changes = payload.model_dump(exclude_none=True, exclude={"scan_now"})
        for key, value in changes.items():
            setattr(root, key, value)

        scan = scan_root(session, root).as_dict() if payload.scan_now and root.read_allowed else None
        revoked = reconcile_workspace_access(session, workspace_id) if not root.read_allowed else 0
        response = {
            "root": RootResponse.model_validate(root, from_attributes=True).model_dump(),
            "scan": scan,
            "revoked_files": revoked,
        }

    _wake_watcher(request)
    return response


@router.delete("/roots/{root_id}")
def revoke_root(workspace_id: str, root_id: str, request: Request) -> dict:
    with request.app.state.database.session() as session:
        root = _root_or_404(session, workspace_id, root_id)
        path = root.path
        session.delete(root)
        session.flush()
        revoked = reconcile_workspace_access(session, workspace_id)

    _wake_watcher(request)
    return {"revoked": True, "path": path, "revoked_files": revoked}


@router.post("/scan")
def scan_workspace(workspace_id: str, request: Request) -> dict:
    with request.app.state.database.session() as session:
        roots = session.scalars(
            select(WorkspaceRoot).where(
                WorkspaceRoot.workspace_id == workspace_id,
                WorkspaceRoot.read_allowed.is_(True),
            )
        ).all()
        summary = ScanStats()
        for root in roots:
            summary.merge(scan_root(session, root, queue_changes=True, metadata_shortcut=False))
        return {"roots": len(roots), "summary": summary.as_dict()}


@router.get("/watcher")
def watcher_status(workspace_id: str, request: Request) -> dict:
    with request.app.state.database.session() as session:
        watched_roots = session.scalar(
            select(WorkspaceRoot)
            .where(
                WorkspaceRoot.workspace_id == workspace_id,
                WorkspaceRoot.read_allowed.is_(True),
                WorkspaceRoot.watch_enabled.is_(True),
            )
            .limit(1)
        )
        count = len(
            session.scalars(
                select(WorkspaceRoot).where(
                    WorkspaceRoot.workspace_id == workspace_id,
                    WorkspaceRoot.read_allowed.is_(True),
                    WorkspaceRoot.watch_enabled.is_(True),
                )
            ).all()
        )

    watcher = getattr(request.app.state, "workspace_watcher", None)
    snapshot = watcher.snapshot().as_dict() if watcher is not None else {
        "running": False,
        "watched_roots": 0,
        "cycles": 0,
        "last_cycle_at": None,
        "last_error": None,
        "last_summary": ScanStats().as_dict(),
    }
    snapshot["workspace_watched_roots"] = count
    snapshot["workspace_watching"] = watched_roots is not None and snapshot["running"]
    return snapshot
