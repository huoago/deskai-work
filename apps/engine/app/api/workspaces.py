from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, Request, status

from app.database.models import Workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    description: str | None
    archived: bool


@router.get("", response_model=list[WorkspaceResponse])
def list_workspaces(request: Request) -> list[Workspace]:
    with request.app.state.database.session() as session:
        return list(session.scalars(select(Workspace).order_by(Workspace.created_at)).all())


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, request: Request) -> Workspace:
    workspace = Workspace(name=payload.name.strip(), description=payload.description)
    with request.app.state.database.session() as session:
        session.add(workspace)
        session.flush()
        session.refresh(workspace)
    return workspace
