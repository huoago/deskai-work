"""Phase 7 task result fields.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("tasks")}
    if "result_text" not in columns:
        op.add_column("tasks", sa.Column("result_text", sa.Text(), nullable=True))
    if "error_message" not in columns:
        op.add_column("tasks", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("tasks")}
    with op.batch_alter_table("tasks") as batch:
        if "error_message" in columns:
            batch.drop_column("error_message")
        if "result_text" in columns:
            batch.drop_column("result_text")
