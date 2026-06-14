"""add_infra_tables

Articles and chunks tables for PostgreSQL-based hybrid search.
Mirrors infra/init.sql schema. Extensions (pgvector, pg_bigm) are
assumed to be installed at the database level (Docker image).

Revision ID: b5e9d3f71a42
Revises: a3f8c2e51d07
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = 'b5e9d3f71a42'
down_revision: Union[str, None] = 'a3f8c2e51d07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Check if table already exists (idempotent migration)."""
    bind = op.get_bind()
    insp = inspect(bind)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists('articles'):
        op.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              BIGSERIAL PRIMARY KEY,
                url             TEXT NOT NULL UNIQUE,
                title           TEXT NOT NULL,
                author          TEXT,
                source          TEXT NOT NULL,
                date_published  TIMESTAMPTZ,
                content         TEXT,
                metadata        JSONB DEFAULT '{}',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

    if not _table_exists('chunks'):
        op.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id              BIGSERIAL PRIMARY KEY,
                article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                chunk_index     INTEGER NOT NULL,
                chunk_text      TEXT NOT NULL,
                embedding       vector(1024),
                tsv             TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (article_id, chunk_index)
            )
        """)

    # Indexes (CREATE INDEX IF NOT EXISTS for idempotency)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_article_id
            ON chunks (article_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_articles_source
            ON articles (source)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_articles_date_published
            ON articles (date_published DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_articles_author
            ON articles (author)
            WHERE author IS NOT NULL
    """)

    # Note: IVFFlat and pg_bigm GIN indexes are NOT created here.
    # IVFFlat requires data to exist first (empty table = bad clustering).
    # pg_bigm GIN requires the extension installed.
    # These are created by the data pipeline after import, or by init.sql.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks CASCADE")
    op.execute("DROP TABLE IF EXISTS articles CASCADE")
