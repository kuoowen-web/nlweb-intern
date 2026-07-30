"""add_early_bird_signups

Landing page 早鳥表單（POST /api/early-bird）落地表。
設計沿 feedbacks 模式（7c2f4ae6b1d3）：
  - idempotent：CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
  - bind.dialect.name 分流 PG / SQLite
  - id 自增整數主鍵、created_at unix epoch float，同 feedbacks 風格
  - email 不設 UNIQUE：允許同 email 重複提交（rate limit 擋濫用即可）
注意：SQLite 測試 fixture（tests/test_early_bird.py）內含與本檔 SQLite 分支
字面一致的 DDL——改表結構時兩處同步（該檔 test_fixture_ddl_matches_migration
以欄位名集合比對防漂移，漏同步會紅）。

Revision ID: 9ebc45a96911
Revises: 283472e3fddd
Create Date: 2026-07-23 19:38:04.563965

"""
from typing import Sequence, Union

from alembic import op


revision: str = '9ebc45a96911'
down_revision: Union[str, Sequence[str], None] = '283472e3fddd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        op.execute("""
            CREATE TABLE IF NOT EXISTS early_bird_signups (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(255) NOT NULL,
                company VARCHAR(200),
                job_title VARCHAR(100),
                purpose TEXT,
                client_ip TEXT,
                created_at DOUBLE PRECISION NOT NULL
            )
        """)
    else:
        op.execute("""
            CREATE TABLE IF NOT EXISTS early_bird_signups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                company TEXT,
                job_title TEXT,
                purpose TEXT,
                client_ip TEXT,
                created_at REAL NOT NULL
            )
        """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_early_bird_created_at "
        "ON early_bird_signups (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_early_bird_created_at")
    op.execute("DROP TABLE IF EXISTS early_bird_signups")
