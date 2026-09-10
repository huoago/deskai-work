"""Phase 6 persistent memory learning queue.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_learning_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_message_id",
            sa.String(length=36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_memory_learning_jobs_status",
        "memory_learning_jobs",
        ["status"],
    )
    op.create_index(
        "ix_memory_learning_jobs_workspace_id",
        "memory_learning_jobs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_memory_learning_jobs_conversation_id",
        "memory_learning_jobs",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_learning_jobs_conversation_id", table_name="memory_learning_jobs")
    op.drop_index("ix_memory_learning_jobs_workspace_id", table_name="memory_learning_jobs")
    op.drop_index("ix_memory_learning_jobs_status", table_name="memory_learning_jobs")
    op.drop_table("memory_learning_jobs")
