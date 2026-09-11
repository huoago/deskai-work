"""Phase 14 transactional file-organization batches.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("file_organization_batches"):
        op.create_table(
            "file_organization_batches",
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
            sa.Column("operation_count", sa.Integer(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_file_organization_batches_task_id",
            "file_organization_batches",
            ["task_id"],
        )
        op.create_index(
            "ix_file_organization_batches_workspace_id",
            "file_organization_batches",
            ["workspace_id"],
        )
        op.create_index(
            "ix_file_organization_batches_status",
            "file_organization_batches",
            ["status"],
        )

    proposal_columns = {
        column["name"]
        for column in inspect(bind).get_columns("file_organization_proposals")
    }
    if "batch_id" not in proposal_columns:
        op.add_column(
            "file_organization_proposals",
            sa.Column("batch_id", sa.String(length=36), nullable=True),
        )
        op.create_index(
            "ix_file_organization_proposals_batch_id",
            "file_organization_proposals",
            ["batch_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    proposal_columns = {
        column["name"]
        for column in inspector.get_columns("file_organization_proposals")
    }
    if "batch_id" in proposal_columns:
        op.drop_index(
            "ix_file_organization_proposals_batch_id",
            table_name="file_organization_proposals",
        )
        op.drop_column("file_organization_proposals", "batch_id")

    if inspect(bind).has_table("file_organization_batches"):
        op.drop_index(
            "ix_file_organization_batches_status",
            table_name="file_organization_batches",
        )
        op.drop_index(
            "ix_file_organization_batches_workspace_id",
            table_name="file_organization_batches",
        )
        op.drop_index(
            "ix_file_organization_batches_task_id",
            table_name="file_organization_batches",
        )
        op.drop_table("file_organization_batches")
