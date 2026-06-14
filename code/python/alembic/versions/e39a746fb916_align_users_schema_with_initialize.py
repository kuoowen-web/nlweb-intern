"""align_users_schema_with_initialize

Fix drift between alembic baseline (9df501ad9a13) and auth_db.initialize() PG schema:
  1. users.email_verification_expires column was missing from baseline
  2. users.password_hash was NOT NULL in baseline but nullable in auth_db.initialize()
     (admin_create_user creates users without a password)

VPS (already auto-created via auth_db.initialize()) runs this as a no-op thanks to
inspector pre-checks and IF NOT EXISTS / IF EXISTS guards.

Revision ID: e39a746fb916
Revises: d4a7e1b83c59
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'e39a746fb916'
down_revision: Union[str, Sequence[str], None] = 'd4a7e1b83c59'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """Check if a column exists in the given table."""
    inspector = inspect(bind)
    columns = {c['name'] for c in inspector.get_columns(table)}
    return column in columns


def _column_is_nullable(bind, table: str, column: str) -> bool:
    """Return True if the column is nullable (not NOT NULL)."""
    inspector = inspect(bind)
    for c in inspector.get_columns(table):
        if c['name'] == column:
            return c.get('nullable', True)
    return True


def upgrade() -> None:
    """
    1. Add email_verification_expires if missing.
    2. Make password_hash nullable if it is currently NOT NULL.
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # ── Fix 1: Add email_verification_expires ──────────────────────────────
    if not _column_exists(bind, 'users', 'email_verification_expires'):
        if is_pg:
            # PG: safe raw DDL (VPS already has this via auth_db.initialize)
            op.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "email_verification_expires DOUBLE PRECISION"
            )
        else:
            # SQLite: raw ADD COLUMN is fine here since batch_alter not needed for just adding
            op.execute(
                "ALTER TABLE users ADD COLUMN email_verification_expires REAL"
            )

    # ── Fix 2: Make password_hash nullable ────────────────────────────────
    if not _column_is_nullable(bind, 'users', 'password_hash'):
        if is_pg:
            # PG: single ALTER COLUMN, no data loss
            op.execute("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL")
        else:
            # SQLite: batch_alter recreates the table with nullable password_hash
            with op.batch_alter_table('users') as batch_op:
                batch_op.alter_column(
                    'password_hash',
                    existing_type=sa.String(255),
                    nullable=True,
                )


def downgrade() -> None:
    """
    Best-effort downgrade.
    - Drop email_verification_expires.
    - Attempt to re-add NOT NULL to password_hash (may fail if NULLs exist).
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # Drop email_verification_expires
    if _column_exists(bind, 'users', 'email_verification_expires'):
        if is_pg:
            op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verification_expires")
        else:
            with op.batch_alter_table('users') as batch_op:
                batch_op.drop_column('email_verification_expires')

    # Attempt to restore NOT NULL on password_hash (may fail if NULLs exist)
    try:
        if is_pg:
            # Fill NULLs first to avoid constraint violation
            op.execute(
                "UPDATE users SET password_hash = '' WHERE password_hash IS NULL"
            )
            op.execute("ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL")
        else:
            with op.batch_alter_table('users') as batch_op:
                batch_op.alter_column(
                    'password_hash',
                    existing_type=sa.String(255),
                    nullable=False,
                )
    except Exception:
        # Downgrade is best-effort; NOT NULL cannot always be restored
        pass
