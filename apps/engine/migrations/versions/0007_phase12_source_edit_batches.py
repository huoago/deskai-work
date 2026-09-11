"""Phase 12 transactional source-edit batches.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("source_edit_batches"):
        op.create_table(
            "source_edit_batches",
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
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("edit_count", sa.Integer(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_source_edit_batches_task_id", "source_edit_batches", ["task_id"])
        op.create_index(
            "ix_source_edit_batches_workspace_id",
            "source_edit_batches",
            ["workspace_id"],
        )
        op.create_index("ix_source_edit_batches_status", "source_edit_batches", ["status"])

    source_columns = {column["name"] for column in inspect(bind).get_columns("source_file_edits")}
    if "batch_id" not in source_columns:
        op.add_column(
            "source_file_edits",
            sa.Column("batch_id", sa.String(length=36), nullable=True),
        )
        op.create_index("ix_source_file_edits_batch_id", "source_file_edits", ["batch_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    source_columns = {column["name"] for column in inspector.get_columns("source_file_edits")}
    if "batch_id" in source_columns:
        op.drop_index("ix_source_file_edits_batch_id", table_name="source_file_edits")
        op.drop_column("source_file_edits", "batch_id")

    if inspect(bind).has_table("source_edit_batches"):
        op.drop_index("ix_source_edit_batches_status", table_name="source_edit_batches")
        op.drop_index("ix_source_edit_batches_workspace_id", table_name="source_edit_batches")
        op.drop_index("ix_source_edit_batches_task_id", table_name="source_edit_batches")
        op.drop_table("source_edit_batches")
