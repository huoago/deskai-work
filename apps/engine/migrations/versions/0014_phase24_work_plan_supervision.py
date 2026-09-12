"""Phase 24 work plan supervision and human control.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    plan_columns = {column["name"] for column in inspector.get_columns("work_plans")}
    additions = [
        ("max_auto_steps", sa.Integer(), "12"),
        ("auto_steps_used", sa.Integer(), "0"),
        ("runtime_budget_seconds", sa.Integer(), "900"),
        ("runtime_seconds_used", sa.Float(), "0"),
        ("step_timeout_seconds", sa.Integer(), "120"),
        ("failure_policy", sa.String(length=32), "pause"),
        ("approval_risk_threshold", sa.Integer(), "4"),
        ("pause_reason", sa.Text(), None),
        ("paused_at", sa.DateTime(timezone=True), None),
    ]
    for name, type_, default in additions:
        if name in plan_columns:
            continue
        kwargs = {"nullable": default is None}
        if default is not None:
            kwargs.update({"nullable": False, "server_default": default})
        op.add_column("work_plans", sa.Column(name, type_, **kwargs))

    inspector = inspect(bind)
    step_columns = {column["name"] for column in inspector.get_columns("work_plan_steps")}
    step_additions = [
        ("approved_at", sa.DateTime(timezone=True), None),
        ("skipped_at", sa.DateTime(timezone=True), None),
        ("skip_reason", sa.Text(), None),
        ("duration_seconds", sa.Float(), None),
        ("timeout_exceeded", sa.Boolean(), "0"),
    ]
    for name, type_, default in step_additions:
        if name in step_columns:
            continue
        kwargs = {"nullable": default is None}
        if default is not None:
            kwargs.update({"nullable": False, "server_default": default})
        op.add_column("work_plan_steps", sa.Column(name, type_, **kwargs))

    inspector = inspect(bind)
    if not inspector.has_table("work_plan_events"):
        op.create_table(
            "work_plan_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "plan_id",
                sa.String(length=36),
                sa.ForeignKey("work_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "step_id",
                sa.String(length=36),
                sa.ForeignKey("work_plan_steps.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=32), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("data_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ("plan_id", "step_id", "event_type", "severity", "created_at"):
            op.create_index(
                f"ix_work_plan_events_{column}",
                "work_plan_events",
                [column],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("work_plan_events"):
        for column in ("created_at", "severity", "event_type", "step_id", "plan_id"):
            op.drop_index(
                f"ix_work_plan_events_{column}",
                table_name="work_plan_events",
            )
        op.drop_table("work_plan_events")

    inspector = inspect(bind)
    step_columns = {column["name"] for column in inspector.get_columns("work_plan_steps")}
    for name in (
        "timeout_exceeded",
        "duration_seconds",
        "skip_reason",
        "skipped_at",
        "approved_at",
    ):
        if name in step_columns:
            with op.batch_alter_table("work_plan_steps") as batch_op:
                batch_op.drop_column(name)

    inspector = inspect(bind)
    plan_columns = {column["name"] for column in inspector.get_columns("work_plans")}
    for name in (
        "paused_at",
        "pause_reason",
        "approval_risk_threshold",
        "failure_policy",
        "step_timeout_seconds",
        "runtime_seconds_used",
        "runtime_budget_seconds",
        "auto_steps_used",
        "max_auto_steps",
    ):
        if name in plan_columns:
            with op.batch_alter_table("work_plans") as batch_op:
                batch_op.drop_column(name)
