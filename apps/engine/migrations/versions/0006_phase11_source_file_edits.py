"""Phase 11 confirmed source-file edit proposals.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("source_file_edits"):
        return
    op.create_table(
        "source_file_edits",
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
        sa.Column(
            "file_id",
            sa.String(length=36),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("diff_preview", sa.Text(), nullable=False),
        sa.Column("change_spec_json", sa.JSON(), nullable=False),
        sa.Column("original_sha256", sa.String(length=64), nullable=False),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=False),
        sa.Column("applied_sha256", sa.String(length=64), nullable=True),
        sa.Column("candidate_path", sa.Text(), nullable=False),
        sa.Column("backup_path", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_source_file_edits_task_id", "source_file_edits", ["task_id"])
    op.create_index("ix_source_file_edits_workspace_id", "source_file_edits", ["workspace_id"])
    op.create_index("ix_source_file_edits_file_id", "source_file_edits", ["file_id"])
    op.create_index("ix_source_file_edits_status", "source_file_edits", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("source_file_edits"):
        return
    op.drop_index("ix_source_file_edits_status", table_name="source_file_edits")
    op.drop_index("ix_source_file_edits_file_id", table_name="source_file_edits")
    op.drop_index("ix_source_file_edits_workspace_id", table_name="source_file_edits")
    op.drop_index("ix_source_file_edits_task_id", table_name="source_file_edits")
    op.drop_table("source_file_edits")
