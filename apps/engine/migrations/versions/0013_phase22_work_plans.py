"""Phase 22 controlled persistent work plans.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "execution_mode" not in task_columns:
        op.add_column(
            "tasks",
            sa.Column(
                "execution_mode",
                sa.String(length=32),
                nullable=False,
                server_default="agent",
            ),
        )
        op.create_index("ix_tasks_execution_mode", "tasks", ["execution_mode"])

    inspector = inspect(bind)
    if not inspector.has_table("work_plans"):
        op.create_table(
            "work_plans",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "task_id",
                sa.String(length=36),
                sa.ForeignKey("tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=1024), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("limitations_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ("task_id", "workspace_id", "status"):
            op.create_index(f"ix_work_plans_{column}", "work_plans", [column])

    inspector = inspect(bind)
    if not inspector.has_table("work_plan_steps"):
        op.create_table(
            "work_plan_steps",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "plan_id",
                sa.String(length=36),
                sa.ForeignKey("work_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=1024), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("tool_name", sa.String(length=255), nullable=False),
            sa.Column("arguments_json", sa.JSON(), nullable=False),
            sa.Column("dependencies_json", sa.JSON(), nullable=False),
            sa.Column("risk_level", sa.Integer(), nullable=False),
            sa.Column("execution_mode", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("tool_call_id", sa.String(length=36), nullable=True),
            sa.Column("external_entity_type", sa.String(length=64), nullable=True),
            sa.Column("external_entity_id", sa.String(length=36), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ("plan_id", "status", "tool_name"):
            op.create_index(f"ix_work_plan_steps_{column}", "work_plan_steps", [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("work_plan_steps"):
        for column in ("tool_name", "status", "plan_id"):
            op.drop_index(f"ix_work_plan_steps_{column}", table_name="work_plan_steps")
        op.drop_table("work_plan_steps")

    inspector = inspect(bind)
    if inspector.has_table("work_plans"):
        for column in ("status", "workspace_id", "task_id"):
            op.drop_index(f"ix_work_plans_{column}", table_name="work_plans")
        op.drop_table("work_plans")

    inspector = inspect(bind)
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "execution_mode" in task_columns:
        op.drop_index("ix_tasks_execution_mode", table_name="tasks")
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.drop_column("execution_mode")
