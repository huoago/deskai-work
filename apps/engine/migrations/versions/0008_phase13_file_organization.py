"""Phase 13 confirmed file organization proposals.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("file_organization_proposals"):
        return
    op.create_table(
        "file_organization_proposals",
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
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("original_sha256", sa.String(length=64), nullable=False),
        sa.Column("applied_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_file_organization_proposals_task_id",
        "file_organization_proposals",
        ["task_id"],
    )
    op.create_index(
        "ix_file_organization_proposals_workspace_id",
        "file_organization_proposals",
        ["workspace_id"],
    )
    op.create_index(
        "ix_file_organization_proposals_file_id",
        "file_organization_proposals",
        ["file_id"],
    )
    op.create_index(
        "ix_file_organization_proposals_status",
        "file_organization_proposals",
        ["status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("file_organization_proposals"):
        return
    op.drop_index(
        "ix_file_organization_proposals_status",
        table_name="file_organization_proposals",
    )
    op.drop_index(
        "ix_file_organization_proposals_file_id",
        table_name="file_organization_proposals",
    )
    op.drop_index(
        "ix_file_organization_proposals_workspace_id",
        table_name="file_organization_proposals",
    )
    op.drop_index(
        "ix_file_organization_proposals_task_id",
        table_name="file_organization_proposals",
    )
    op.drop_table("file_organization_proposals")
