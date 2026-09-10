"""Phase 4 retrieval FTS table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10
"""
from __future__ import annotations

from sqlalchemy.exc import OperationalError

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    try:
        bind.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_v2 USING fts5(
                chunk_id UNINDEXED,
                workspace_id UNINDEXED,
                filename,
                section,
                content,
                tokenize='trigram'
            )
            """
        )
    except OperationalError:
        # Modern SQLite provides trigram FTS5, which works well for CJK and
        # engineering codes. Keep a safe unicode61 fallback for older runtimes.
        bind.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_v2 USING fts5(
                chunk_id UNINDEXED,
                workspace_id UNINDEXED,
                filename,
                section,
                content,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks_fts_v2")
