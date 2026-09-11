from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select

from app.database.models import Permission
from app.database.session import Database

AGENT_TOOL_POLICY: dict[str, tuple[int, bool]] = {
    "search_knowledge": (1, False),
    "search_memory": (1, False),
    "list_workspace_files": (2, False),
    "read_parsed_document": (2, False),
    "create_word_document": (3, False),
    "create_spreadsheet": (3, False),
    "calculate_expression": (1, False),
    "inspect_table": (1, False),
    "summarize_table": (1, False),
    "aggregate_table": (1, False),
    "search_web": (2, False),
    "propose_source_file_edit": (3, False),
    "propose_source_file_edit_batch": (3, False),
    "propose_file_organization": (3, False),
    "propose_file_organization_batch": (3, False),
    "propose_file_recycle": (4, False),
}


@dataclass(slots=True)
class PermissionDecision:
    allowed: bool
    risk_level: int
    requires_confirmation: bool
    reason: str


class PermissionGate:
    def __init__(self, database: Database) -> None:
        self.database = database

    def check(self, *, workspace_id: str | None, tool_name: str) -> PermissionDecision:
        policy = AGENT_TOOL_POLICY.get(tool_name)
        if policy is None:
            return PermissionDecision(False, 8, True, "Tool is not enabled by the current Agent policy")
        risk_level, requires_confirmation = policy
        if not workspace_id:
            return PermissionDecision(
                False,
                risk_level,
                requires_confirmation,
                "Agent tools require an active Workspace",
            )

        capability = f"agent.tool.{tool_name}"
        with self.database.session() as session:
            records = list(
                session.scalars(
                    select(Permission).where(
                        Permission.capability == capability,
                        or_(
                            Permission.workspace_id == workspace_id,
                            Permission.workspace_id.is_(None),
                        ),
                    )
                ).all()
            )

        explicit: Any | None = None
        workspace_match = next(
            (item for item in records if item.workspace_id == workspace_id),
            None,
        )
        global_match = next((item for item in records if item.workspace_id is None), None)
        selected = workspace_match or global_match
        if selected is not None:
            explicit = selected.value_json

        allowed = _permission_value(explicit, default=True)
        return PermissionDecision(
            allowed=allowed,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            reason="Allowed by the current scoped Agent policy" if allowed else "Explicitly disabled",
        )


def _permission_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        raw = value.get("allowed")
        return bool(raw) if raw is not None else default
    return default
