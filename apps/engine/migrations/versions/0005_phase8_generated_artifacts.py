"""Phase 8 generated artifact registry.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("generated_artifacts"):
        return
    op.create_table(
        "generated_artifacts",
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
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=1024), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_generated_artifacts_task_id", "generated_artifacts", ["task_id"])
    op.create_index("ix_generated_artifacts_workspace_id", "generated_artifacts", ["workspace_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("generated_artifacts"):
        return
    op.drop_index("ix_generated_artifacts_workspace_id", table_name="generated_artifacts")
    op.drop_index("ix_generated_artifacts_task_id", table_name="generated_artifacts")
    op.drop_table("generated_artifacts")
