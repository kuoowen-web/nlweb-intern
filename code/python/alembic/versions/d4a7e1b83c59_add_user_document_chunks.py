"""add_user_document_chunks

User document chunks table for private knowledge base.
Mirrors infra/init.sql schema for user_document_chunks.
Requires pgvector extension (assumed installed at database level).

Revision ID: d4a7e1b83c59
Revises: b5e9d3f71a42
Create Date: 2026-04-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = 'd4a7e1b83c59'
down_revision: Union[str, None] = 'b5e9d3f71a42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Check if table already exists (idempotent migration)."""
    bind = op.get_bind()
    insp = inspect(bind)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists('user_document_chunks'):
        op.execute("""
            CREATE TABLE IF NOT EXISTS user_document_chunks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL,
                org_id TEXT,
                source_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                total_chunks INTEGER NOT NULL,
                content TEXT NOT NULL,
                metadata JSONB DEFAULT '{}',
                embedding vector(1024) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    # Indexes (CREATE INDEX IF NOT EXISTS for idempotency)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_udc_user_id
            ON user_document_chunks(user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_udc_source_id
            ON user_document_chunks(source_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_udc_user_org
            ON user_document_chunks(user_id, org_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_document_chunks CASCADE")
