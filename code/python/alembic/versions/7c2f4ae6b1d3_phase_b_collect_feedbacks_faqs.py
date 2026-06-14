"""phase_b_collect_feedbacks_faqs

Phase 2.5 catch-up migration：把 `feedbacks` 與 `faqs` 兩張表納入 alembic head。

背景：
  Phase 1 audit（docs/scratch/alembic-vps-schema-audit.md）的 scope 限定
  auth / session / audit / org / bootstrap_tokens，並未涵蓋 feedbacks / faqs。
  這兩張表原本完全由 `auth_db.initialize()` 啟動時的手動 DDL 維護，
  alembic 從未收編。

  Phase 2 plan（docs/in progress/plans/alembic-phase-2-remove-initialize-ddl-plan.md）
  的 Phase 2.0 cross-check 將此認定為 BLOCKER：
    - 差異 #3：feedbacks 表 alembic 完全沒有
    - 差異 #4：faqs 表 alembic 完全沒有
    - 差異 #5：idx_feedbacks_created_at 缺漏（隨 #3 補）
    - 差異 #6：idx_faqs_sort_order 缺漏（隨 #4 補）

  必須先把這兩張表收編到 alembic head，才能在 Phase 2 Main
  安全移除 `auth_db.initialize()` 的 DDL 邏輯，讓 alembic 成為唯一
  schema source of truth。

設計原則（沿用 Phase 1.5 catch-up `1015e1c40f88` 的模式）：
  - 完全 idempotent：`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`
    對 VPS 已存在的表 = no-op，對乾淨環境會建表。
  - 用 `bind.dialect.name == 'postgresql'` 分流 PG / SQLite。
  - 純 raw SQL via `op.execute(...)`，不重塑 schema、不正規化、不改 type。
  - column 定義完全沿用 `auth_db.py` 既有 schema dict 的字面定義
    （authoritative source），不破壞 VPS 真實資料。

附錄風險（Phase 3 SSH dry run 才驗證，不在本 migration 處理）：
  若 VPS feedbacks / faqs 真實 schema 已與 auth_db.py 定義 drift
  （例如 column type 不一致，類似 audit_logs uuid drift 的情況），
  本 migration 因為使用 `CREATE TABLE IF NOT EXISTS` 對該表 = no-op，
  不會嘗試對齊。若 Phase 3 dry run 揭露 drift，由後續 ALTER migration
  追加處理（同 1015e1c40f88 模式）。

Revision ID: 7c2f4ae6b1d3
Revises: 1015e1c40f88
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op


revision: str = '7c2f4ae6b1d3'
down_revision: Union[str, Sequence[str], None] = '1015e1c40f88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    執行順序：
      1. feedbacks 表 + idx_feedbacks_created_at 收編
      2. faqs 表 + idx_faqs_sort_order 收編

    兩段都用 dialect 分流：PG 走 SERIAL / VARCHAR / BOOLEAN / DOUBLE PRECISION，
    SQLite 走 INTEGER PRIMARY KEY AUTOINCREMENT / TEXT / INTEGER / REAL。
    column 定義完全照 auth_db.py 既有 schema dict，不做任何重塑。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # ─────────────────────────────────────────────────────────────
    # 1. feedbacks 表收編（差異 #3）
    # ─────────────────────────────────────────────────────────────
    # 來源（authoritative）：
    #   - PG：auth_db.py line 672-683 `_get_postgres_schema()` 的 'feedbacks' entry
    #   - SQLite：auth_db.py line 487-498 `_get_sqlite_schema()` 的 'feedbacks' entry
    #
    # 特別說明 column type 選擇：
    #   - `user_id TEXT`（非 UUID）：刻意設計。feedbacks.user_id 可為 NULL（匿名
    #     回饋）或外部識別字串，不適合用 UUID 約束。沿用 auth_db.py 字面定義。
    #   - `id SERIAL`（PG）/ `INTEGER PRIMARY KEY AUTOINCREMENT`（SQLite）：
    #     auth_db.py 使用 auto-increment 整數主鍵（非 UUID），沿用不重塑。
    #   - `created_at DOUBLE PRECISION`（PG）/ `REAL`（SQLite）：unix epoch float
    #     timestamp，與 bootstrap_tokens 同風格，沿用 auth_db.py 既有設計。
    if is_pg:
        op.execute("""
            CREATE TABLE IF NOT EXISTS feedbacks (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                email VARCHAR(255),
                category VARCHAR(50) NOT NULL,
                rating INTEGER NOT NULL,
                content TEXT NOT NULL,
                screenshot_path TEXT,
                session_id TEXT,
                created_at DOUBLE PRECISION NOT NULL
            )
        """)
    else:
        # SQLite 對等定義（auth_db.py line 487-498）
        op.execute("""
            CREATE TABLE IF NOT EXISTS feedbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                email TEXT,
                category TEXT NOT NULL,
                rating INTEGER NOT NULL,
                content TEXT NOT NULL,
                screenshot_path TEXT,
                session_id TEXT,
                created_at REAL NOT NULL
            )
        """)

    # idx_feedbacks_created_at（差異 #5）—— 對應 auth_db.py line 721
    # CREATE INDEX IF NOT EXISTS 對 PG / SQLite 都是合法且 idempotent 語法。
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedbacks_created_at "
        "ON feedbacks (created_at)"
    )

    # ─────────────────────────────────────────────────────────────
    # 2. faqs 表收編（差異 #4）
    # ─────────────────────────────────────────────────────────────
    # 來源（authoritative）：
    #   - PG：auth_db.py line 685-696 `_get_postgres_schema()` 的 'faqs' entry
    #   - SQLite：auth_db.py line 499-510 `_get_sqlite_schema()` 的 'faqs' entry
    #
    # 特別說明 column type 選擇：
    #   - `is_published BOOLEAN ... DEFAULT TRUE`（PG）vs
    #     `is_published INTEGER ... DEFAULT 1`（SQLite）：SQLite 沒有原生 BOOLEAN
    #     type，沿用 auth_db.py 既有 INTEGER 0/1 表示法，不正規化。
    #   - `created_at` / `updated_at`：與 feedbacks 同風格，DOUBLE PRECISION（PG）
    #     / REAL（SQLite）unix epoch float。
    if is_pg:
        op.execute("""
            CREATE TABLE IF NOT EXISTS faqs (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                category VARCHAR(50) NOT NULL DEFAULT 'general',
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_published BOOLEAN NOT NULL DEFAULT TRUE,
                created_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL
            )
        """)
    else:
        # SQLite 對等定義（auth_db.py line 499-510）
        op.execute("""
            CREATE TABLE IF NOT EXISTS faqs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_published INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

    # idx_faqs_sort_order（差異 #6）—— 對應 auth_db.py line 722
    # 複合 index (sort_order, id)：FAQ 列表常用 ORDER BY sort_order, id。
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_faqs_sort_order "
        "ON faqs (sort_order, id)"
    )


def downgrade() -> None:
    """
    Best-effort downgrade，按相反順序還原：
      2. faqs：DROP INDEX → DROP TABLE
      1. feedbacks：DROP INDEX → DROP TABLE

    注意：
      - 所有 step 用 IF EXISTS guard，downgrade 重複跑不會錯。
      - 真實資料若已存在會被 DROP TABLE 移除 —— 與 1015e1c40f88 downgrade
        bootstrap_tokens 同模式。VPS 上若 manually run downgrade 必須先備份
        feedbacks / faqs 真實資料。
    """
    # ── 2. faqs 還原 ──
    op.execute("DROP INDEX IF EXISTS idx_faqs_sort_order")
    op.execute("DROP TABLE IF EXISTS faqs")

    # ── 1. feedbacks 還原 ──
    op.execute("DROP INDEX IF EXISTS idx_feedbacks_created_at")
    op.execute("DROP TABLE IF EXISTS feedbacks")
