"""baseline_auth_tables

Baseline migration: existing auth tables created by auth_db.py auto-create.
This migration is a "stamp" — tables already exist in DB, we just record the schema.

Revision ID: 9df501ad9a13
Revises:
Create Date: 2026-02-14 14:52:46.455076
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = '9df501ad9a13'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Check if table already exists (for idempotent baseline)."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create auth tables if they don't already exist (baseline)."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if not _table_exists('organizations'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS organizations (
                    id UUID PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    slug VARCHAR(255) NOT NULL UNIQUE,
                    plan VARCHAR(50) NOT NULL DEFAULT 'free',
                    max_members INTEGER NOT NULL DEFAULT 5,
                    settings TEXT DEFAULT '{}',
                    created_at DOUBLE PRECISION NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL DEFAULT 'free',
                    max_members INTEGER NOT NULL DEFAULT 5,
                    settings TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
            """)

    if not _table_exists('users'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    email_verification_token VARCHAR(255),
                    password_reset_token VARCHAR(255),
                    password_reset_expires DOUBLE PRECISION,
                    last_login DOUBLE PRECISION,
                    created_at DOUBLE PRECISION NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    email_verification_token TEXT,
                    password_reset_token TEXT,
                    password_reset_expires REAL,
                    last_login REAL,
                    created_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
            """)

    if not _table_exists('org_memberships'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS org_memberships (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    role VARCHAR(50) NOT NULL DEFAULT 'member',
                    invited_by UUID,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    accepted_at DOUBLE PRECISION
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS org_memberships (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    invited_by TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    accepted_at REAL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
                )
            """)

    if not _table_exists('invitations'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS invitations (
                    id UUID PRIMARY KEY,
                    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    email VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'member',
                    invited_by UUID NOT NULL REFERENCES users(id),
                    token VARCHAR(255) NOT NULL UNIQUE,
                    expires_at DOUBLE PRECISION NOT NULL,
                    accepted_at DOUBLE PRECISION,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    invited_by TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    expires_at REAL NOT NULL,
                    accepted_at REAL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
                    FOREIGN KEY (invited_by) REFERENCES users(id)
                )
            """)

    if not _table_exists('refresh_tokens'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash VARCHAR(255) NOT NULL UNIQUE,
                    expires_at DOUBLE PRECISION NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    revoked_at DOUBLE PRECISION
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    revoked_at REAL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

    if not _table_exists('login_attempts'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id UUID PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    ip_address INET,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    attempted_at DOUBLE PRECISION NOT NULL
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    ip_address TEXT,
                    success INTEGER NOT NULL DEFAULT 0,
                    attempted_at REAL NOT NULL
                )
            """)

    # Indexes (idempotent with IF NOT EXISTS)
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_users_verification_token ON users(email_verification_token)",
        "CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(password_reset_token)",
        "CREATE INDEX IF NOT EXISTS idx_org_memberships_user ON org_memberships(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_org_memberships_org ON org_memberships(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token)",
        "CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email)",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_time ON login_attempts(attempted_at)",
    ]
    for idx_sql in indexes:
        try:
            op.execute(idx_sql)
        except Exception:
            pass  # Index may already exist


def downgrade() -> None:
    """Drop all auth tables."""
    for table in ['login_attempts', 'refresh_tokens', 'invitations',
                  'org_memberships', 'users', 'organizations']:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
