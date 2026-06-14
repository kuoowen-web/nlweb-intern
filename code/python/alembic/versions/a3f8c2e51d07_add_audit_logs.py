"""add_audit_logs

Audit log table for tracking user actions (Phase 3B).

Revision ID: a3f8c2e51d07
Revises: c1c6deac2013
Create Date: 2026-02-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = 'a3f8c2e51d07'
down_revision: Union[str, Sequence[str], None] = 'c1c6deac2013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    try:
        conn = op.get_bind()
        return inspect(conn).has_table(table_name)
    except Exception:
        return False


def upgrade() -> None:
    # 改用 raw SQL with CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
    # 達成 DDL 層級 idempotent guard（雙層：外層 _table_exists + 內層 IF NOT EXISTS）
    # column type 沿用原 String(36) / Text 字面定義 — VPS 真實 type（uuid / jsonb）
    # 對齊由後續 1015e1c40f88 處理，本檔不動。
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if not _table_exists('audit_logs'):
        if is_pg:
            op.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36),
                    org_id VARCHAR(36),
                    action VARCHAR(100) NOT NULL,
                    target_type VARCHAR(50),
                    target_id VARCHAR(36),
                    details TEXT,
                    ip_address VARCHAR(64),
                    created_at DOUBLE PRECISION NOT NULL
                )
            """)
        else:
            op.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    org_id TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    details TEXT,
                    ip_address TEXT,
                    created_at REAL NOT NULL
                )
            """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_org_id ON audit_logs (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs (action)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs (created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_created_at")
    op.execute("DROP INDEX IF EXISTS idx_audit_action")
    op.execute("DROP INDEX IF EXISTS idx_audit_org_id")
    op.execute("DROP INDEX IF EXISTS idx_audit_user_id")
    op.execute("DROP TABLE IF EXISTS audit_logs")
