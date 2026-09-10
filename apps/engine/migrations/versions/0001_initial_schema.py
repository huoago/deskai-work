"""Initial DeskAI V1 schema and FTS5 foundation.

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""
from __future__ import annotations

from alembic import op

from app.database.base import Base
from app.database import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            workspace_id UNINDEXED,
            filename,
            section,
            content,
            tokenize='unicode61'
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TABLE IF EXISTS chunks_fts")
    Base.metadata.drop_all(bind=bind)
