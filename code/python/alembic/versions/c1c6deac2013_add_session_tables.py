"""add_session_tables

Session management tables: search_sessions, org_folders, org_folder_sessions,
session_shares, user_preferences. Also adds storage_quota_gb and
monthly_search_limit to organizations.

Revision ID: c1c6deac2013
Revises: 9df501ad9a13
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'c1c6deac2013'
down_revision: Union[str, Sequence[str], None] = '9df501ad9a13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # ── Add missing columns to organizations ──
    # PG 9.6+ 支援 ADD COLUMN IF NOT EXISTS（雙層 idempotent guard：inspector pre-check + DDL guard）
    # SQLite 不支援 ADD COLUMN IF NOT EXISTS，靠 _column_exists inspector pre-check 達到 idempotent
    if _table_exists('organizations'):
        if not _column_exists('organizations', 'storage_quota_gb'):
            if is_pg:
                op.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS storage_quota_gb INTEGER DEFAULT 5")
            else:
                op.execute("ALTER TABLE organizations ADD COLUMN storage_quota_gb INTEGER DEFAULT 5")
        if not _column_exists('organizations', 'monthly_search_limit'):
            if is_pg:
                op.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS monthly_search_limit INTEGER DEFAULT 1000")
            else:
                op.execute("ALTER TABLE organizations ADD COLUMN monthly_search_limit INTEGER DEFAULT 1000")

    # ── search_sessions ──
    if not _table_exists('search_sessions'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    org_id UUID NOT NULL REFERENCES organizations(id),
                    title VARCHAR(500),
                    conversation_history JSONB DEFAULT '[]',
                    session_history JSONB DEFAULT '[]',
                    chat_history JSONB DEFAULT '[]',
                    accumulated_articles JSONB DEFAULT '[]',
                    pinned_messages JSONB DEFAULT '[]',
                    pinned_news_cards JSONB DEFAULT '[]',
                    research_report JSONB DEFAULT '{}',
                    user_feedback VARCHAR(20),
                    admin_note TEXT,
                    visibility VARCHAR(20) DEFAULT 'private',
                    team_comments JSONB DEFAULT '[]',
                    is_archived BOOLEAN DEFAULT FALSE,
                    deleted_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    title TEXT,
                    conversation_history TEXT DEFAULT '[]',
                    session_history TEXT DEFAULT '[]',
                    chat_history TEXT DEFAULT '[]',
                    accumulated_articles TEXT DEFAULT '[]',
                    pinned_messages TEXT DEFAULT '[]',
                    pinned_news_cards TEXT DEFAULT '[]',
                    research_report TEXT DEFAULT '{}',
                    user_feedback TEXT,
                    admin_note TEXT,
                    visibility TEXT DEFAULT 'private',
                    team_comments TEXT DEFAULT '[]',
                    is_archived INTEGER DEFAULT 0,
                    deleted_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (org_id) REFERENCES organizations(id)
                )
            """)

    # ── org_folders ──
    if not _table_exists('org_folders'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS org_folders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    created_by UUID NOT NULL REFERENCES users(id),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS org_folders (
                    id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id)
                )
            """)

    # ── org_folder_sessions (junction table) ──
    if not _table_exists('org_folder_sessions'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS org_folder_sessions (
                    folder_id UUID NOT NULL REFERENCES org_folders(id) ON DELETE CASCADE,
                    session_id UUID NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    added_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (folder_id, session_id)
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS org_folder_sessions (
                    folder_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    PRIMARY KEY (folder_id, session_id),
                    FOREIGN KEY (folder_id) REFERENCES org_folders(id) ON DELETE CASCADE,
                    FOREIGN KEY (session_id) REFERENCES search_sessions(id) ON DELETE CASCADE
                )
            """)

    # ── session_shares (junction table) ──
    if not _table_exists('session_shares'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS session_shares (
                    session_id UUID NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    shared_with_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    shared_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (session_id, shared_with_user_id)
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS session_shares (
                    session_id TEXT NOT NULL,
                    shared_with_user_id TEXT NOT NULL,
                    shared_at REAL NOT NULL,
                    PRIMARY KEY (session_id, shared_with_user_id),
                    FOREIGN KEY (session_id) REFERENCES search_sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY (shared_with_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

    # ── user_preferences ──
    if not _table_exists('user_preferences'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    org_id UUID NOT NULL REFERENCES organizations(id),
                    preference_key VARCHAR(100) NOT NULL,
                    preference_value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, org_id, preference_key)
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    preference_value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id, org_id, preference_key),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (org_id) REFERENCES organizations(id)
                )
            """)

    # ── Indexes ──
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_org ON search_sessions(user_id, org_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON search_sessions(updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_org_folders ON org_folders(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_prefs_user_org ON user_preferences(user_id, org_id)",
    ]
    if is_pg:
        indexes.extend([
            "CREATE INDEX IF NOT EXISTS idx_sessions_visibility ON search_sessions(org_id, visibility) WHERE visibility != 'private' AND deleted_at IS NULL",
            "CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON search_sessions(deleted_at) WHERE deleted_at IS NOT NULL",
        ])

    for idx_sql in indexes:
        try:
            op.execute(idx_sql)
        except Exception:
            pass


def downgrade() -> None:
    for table in ['user_preferences', 'session_shares', 'org_folder_sessions',
                  'org_folders', 'search_sessions']:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS storage_quota_gb")
        op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS monthly_search_limit")
