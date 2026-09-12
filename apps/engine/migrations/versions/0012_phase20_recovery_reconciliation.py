"""Phase 20 controlled recovery state reconciliation proposals.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("recovery_reconciliation_proposals"):
        op.create_table(
            "recovery_reconciliation_proposals",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "task_id",
                sa.String(length=36),
                sa.ForeignKey("tasks.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("transaction_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("snapshot_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("snapshot_state", sa.String(length=64), nullable=False),
            sa.Column("target_status", sa.String(length=32), nullable=False),
            sa.Column("historical_action", sa.String(length=32), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in (
            "task_id",
            "workspace_id",
            "entity_type",
            "transaction_id",
            "status",
        ):
            op.create_index(
                f"ix_recovery_reconciliation_proposals_{column}",
                "recovery_reconciliation_proposals",
                [column],
            )


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("recovery_reconciliation_proposals"):
        for column in (
            "status",
            "transaction_id",
            "entity_type",
            "workspace_id",
            "task_id",
        ):
            op.drop_index(
                f"ix_recovery_reconciliation_proposals_{column}",
                table_name="recovery_reconciliation_proposals",
            )
        op.drop_table("recovery_reconciliation_proposals")
