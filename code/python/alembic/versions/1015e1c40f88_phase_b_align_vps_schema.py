"""phase_b_align_vps_schema

Phase 1.5（方案 B）catch-up migration：把 alembic head 的預期 schema
對齊到 VPS production PostgreSQL 的真實 schema。

背景：
  alembic 從未在 VPS 上跑過，VPS schema 完全靠 `auth_db.initialize()`
  啟動時的手動 DDL 維護。本次方案 B 要把 alembic 接成 source of truth。
  Phase 1（schema audit）已產出 `docs/scratch/alembic-vps-schema-audit.md`，
  列出 alembic head（e39a746fb916）vs VPS 真實 schema 的所有 drift。

本 migration 處理 4 群 drift（依 audit report 行序）：
  1. bootstrap_tokens 整張表 alembic 完全沒有（VPS-only）—— BLOCKER 收編
  2. organizations.plan：VPS 為 nullable + 無 default；alembic 預期 NOT NULL DEFAULT 'free'
  3. search_sessions 兩個 partial index 缺漏：idx_sessions_visibility / idx_sessions_deleted
  4. audit_logs：VPS 真實 type 已是 uuid + jsonb + gen_random_uuid()，
     原 a3f8c2e51d07 用 String(36) + Text；以方案 A（Q1 拍板）對齊到 VPS 真實 type

設計原則：
  - 完全 idempotent。VPS 已對齊欄位 = no-op；新環境從 0 跑也 work。
  - 不破壞 VPS 真實資料：所有 ALTER TYPE 用 information_schema 預檢 + USING cast。
  - 不修改任何既有 migration（absolute rule）。

Revision ID: 1015e1c40f88
Revises: e39a746fb916
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op


revision: str = '1015e1c40f88'
down_revision: Union[str, Sequence[str], None] = 'e39a746fb916'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    執行順序（按 audit report 草稿）：
      1. bootstrap_tokens 表 + index 收編
      2. organizations.plan 補 default + NOT NULL
      3. search_sessions 兩個 partial index 補建
      4. audit_logs columns ALTER TYPE 對齊到 uuid + jsonb（VPS 真實 type）

    本 migration 僅針對 PostgreSQL 設計（VPS production）。SQLite dev
    環境 audit_logs 的 type drift 不適用（SQLite 無 uuid/jsonb 概念），
    用 dialect 判斷 skip 對應段；其餘段（bootstrap_tokens / organizations /
    search_sessions partial index）對 SQLite 也無害，但 partial index 在
    SQLite 仍為合法語法，故統一執行。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # ─────────────────────────────────────────────────────────────
    # 1. bootstrap_tokens 表收編 [BLOCKER]
    # ─────────────────────────────────────────────────────────────
    # 對應 audit row：Table bootstrap_tokens（整張表 VPS-only）
    # 來源：auth_db.py `_get_postgres_schema()` 的 'bootstrap_tokens' entry
    # 注意：VPS 真實使用 TEXT（非 UUID），timestamps 為 DOUBLE PRECISION。
    #       這是 auth_db.initialize() 的 SQLite 風格延伸到 PG，雖非
    #       「正規 PG」但已是真實狀態，本次只做收編、不重塑 schema。
    if is_pg:
        op.execute("""
            CREATE TABLE IF NOT EXISTS bootstrap_tokens (
                id TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                org_name_hint TEXT DEFAULT '',
                created_at DOUBLE PRECISION NOT NULL,
                expires_at DOUBLE PRECISION NOT NULL,
                used_at DOUBLE PRECISION,
                used_by_email TEXT
            )
        """)
    else:
        # SQLite 對等定義（auth_db.py `_get_sqlite_schema()` 的 bootstrap_tokens）
        op.execute("""
            CREATE TABLE IF NOT EXISTS bootstrap_tokens (
                id TEXT PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                org_name_hint TEXT DEFAULT '',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL,
                used_by_email TEXT
            )
        """)

    # 補 idx_bootstrap_tokens_token（即使 token 已有 UNIQUE constraint，
    # auth_db.initialize() 仍另建此 index，為了與 VPS 真實一致也補上）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_bootstrap_tokens_token "
        "ON bootstrap_tokens (token)"
    )

    # ─────────────────────────────────────────────────────────────
    # 2. organizations.plan 對齊 [FIX × 2]
    # ─────────────────────────────────────────────────────────────
    # 對應 audit rows：
    #   - organizations.plan NOT NULL：VPS nullable / alembic 預期 NOT NULL
    #   - organizations.plan DEFAULT 'free'：VPS 無 default / alembic 預期有
    # 注意順序：先 UPDATE NULL → 'free' 才能安全 SET NOT NULL。
    #   SET DEFAULT / SET NOT NULL 重複跑不會錯，故不額外 IF guard。
    if is_pg:
        # 把可能存在的 NULL 值補成 'free'（防 SET NOT NULL 失敗）
        op.execute(
            "UPDATE organizations SET plan = 'free' WHERE plan IS NULL"
        )
        op.execute(
            "ALTER TABLE organizations ALTER COLUMN plan SET DEFAULT 'free'"
        )
        op.execute(
            "ALTER TABLE organizations ALTER COLUMN plan SET NOT NULL"
        )
    # SQLite：organizations baseline 已是 NOT NULL DEFAULT 'free'，無 drift，跳過

    # ─────────────────────────────────────────────────────────────
    # 3. search_sessions partial indexes 補建 [FIX × 2]
    # ─────────────────────────────────────────────────────────────
    # 對應 audit rows：
    #   - idx_sessions_visibility (partial)：VPS 不存在 / alembic c1c6deac2013 預期存在
    #   - idx_sessions_deleted (partial)：VPS 不存在 / alembic c1c6deac2013 預期存在
    # 名稱與條件完全沿用 c1c6deac2013 第 211-213 行 PG 分支寫法。
    # CREATE INDEX IF NOT EXISTS 已是 idempotent，無需額外 guard。
    if is_pg:
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_visibility "
            "ON search_sessions (org_id, visibility) "
            "WHERE visibility != 'private' AND deleted_at IS NULL"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_deleted "
            "ON search_sessions (deleted_at) "
            "WHERE deleted_at IS NOT NULL"
        )
    # SQLite 不支援 partial index 的某些 PG 特性，但語法相容；為保守起見只在 PG 跑

    # ─────────────────────────────────────────────────────────────
    # 4. audit_logs type 對齊到 VPS 真實 schema [FIX × 5]（方案 A，Q1 拍板）
    # ─────────────────────────────────────────────────────────────
    # 對應 audit rows：
    #   - audit_logs.id type uuid（VPS） vs String(36)（alembic a3f8c2e51d07）
    #   - audit_logs.id DEFAULT gen_random_uuid()（VPS） vs 無（alembic）
    #   - audit_logs.user_id type uuid vs String(36)
    #   - audit_logs.org_id type uuid vs String(36)
    #   - audit_logs.target_id type uuid vs String(36)
    #   - audit_logs.details type jsonb vs Text
    #
    # 設計：
    #   - 用 information_schema.columns 預檢，已是 uuid/jsonb 則 skip。
    #     此設計確保 VPS 跑 = no-op；新環境從 0 跑（先建 String(36)）會 ALTER 一次。
    #   - USING <col>::uuid / ::jsonb 處理舊 String/Text → 新 type 的 cast。
    #     若 VPS 已是目標 type，外層 IF 已 skip，不會走到 USING。
    #   - SQLite 沒有 uuid/jsonb 概念，整段 skip。
    if is_pg:
        # 4a. audit_logs.id → uuid + DEFAULT gen_random_uuid()
        # 包成單一 DO block：先 ALTER TYPE，再判斷 column_default 補 default
        op.execute("""
            DO $$
            BEGIN
                -- 若 id 還是非 uuid（例如 character varying），轉成 uuid
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'id'
                ) <> 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN id TYPE uuid USING id::uuid;
                END IF;

                -- 若 id 沒有 column_default，補上 gen_random_uuid()
                IF (
                    SELECT column_default FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'id'
                ) IS NULL THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN id SET DEFAULT gen_random_uuid();
                END IF;
            END$$;
        """)

        # 4b. audit_logs.user_id → uuid（nullable 維持，audit row 沒提到 nullability drift）
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'user_id'
                ) <> 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN user_id TYPE uuid USING user_id::uuid;
                END IF;
            END$$;
        """)

        # 4c. audit_logs.org_id → uuid
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'org_id'
                ) <> 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN org_id TYPE uuid USING org_id::uuid;
                END IF;
            END$$;
        """)

        # 4d. audit_logs.target_id → uuid
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'target_id'
                ) <> 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN target_id TYPE uuid USING target_id::uuid;
                END IF;
            END$$;
        """)

        # 4e. audit_logs.details → jsonb
        # 注意：text → jsonb 的 cast 若內容不是合法 JSON 會炸。
        # VPS 已是 jsonb（外層 IF skip），新環境表內無資料，cast 也安全。
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'details'
                ) <> 'jsonb' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN details TYPE jsonb USING details::jsonb;
                END IF;
            END$$;
        """)


def downgrade() -> None:
    """
    Best-effort downgrade，按相反順序還原：
      4. audit_logs：ALTER TYPE 回 VARCHAR(36) + Text + DROP DEFAULT
      3. search_sessions：DROP partial indexes
      2. organizations.plan：DROP NOT NULL + DROP DEFAULT
      1. bootstrap_tokens：DROP TABLE

    注意：
      - 不還原 organizations.plan 已被填成 'free' 的資料（NULL 無法還原）；
        只移除 NOT NULL / DEFAULT constraint。
      - audit_logs 的 uuid → VARCHAR(36) 用 USING cast；jsonb → text 同理。
      - 所有 step 用 IF EXISTS / information_schema 預檢，downgrade 重複跑不會錯。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    # ── 4. audit_logs 還原（PG only）──
    if is_pg:
        # 4e. details: jsonb → text
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'details'
                ) = 'jsonb' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN details TYPE text USING details::text;
                END IF;
            END$$;
        """)

        # 4d. target_id: uuid → varchar(36)
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'target_id'
                ) = 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN target_id TYPE varchar(36) USING target_id::text;
                END IF;
            END$$;
        """)

        # 4c. org_id: uuid → varchar(36)
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'org_id'
                ) = 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN org_id TYPE varchar(36) USING org_id::text;
                END IF;
            END$$;
        """)

        # 4b. user_id: uuid → varchar(36)
        op.execute("""
            DO $$
            BEGIN
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'user_id'
                ) = 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN user_id TYPE varchar(36) USING user_id::text;
                END IF;
            END$$;
        """)

        # 4a. id: 先 DROP DEFAULT 再 uuid → varchar(36)
        op.execute("""
            DO $$
            BEGIN
                -- 先 DROP DEFAULT（gen_random_uuid() 對 varchar 無效）
                IF (
                    SELECT column_default FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'id'
                ) IS NOT NULL THEN
                    ALTER TABLE audit_logs ALTER COLUMN id DROP DEFAULT;
                END IF;

                -- 再 cast type
                IF (
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = 'audit_logs' AND column_name = 'id'
                ) = 'uuid' THEN
                    ALTER TABLE audit_logs
                        ALTER COLUMN id TYPE varchar(36) USING id::text;
                END IF;
            END$$;
        """)

    # ── 3. search_sessions partial indexes 還原 ──
    if is_pg:
        op.execute("DROP INDEX IF EXISTS idx_sessions_deleted")
        op.execute("DROP INDEX IF EXISTS idx_sessions_visibility")

    # ── 2. organizations.plan 還原（移除 NOT NULL + DEFAULT；資料維持） ──
    if is_pg:
        op.execute(
            "ALTER TABLE organizations ALTER COLUMN plan DROP NOT NULL"
        )
        op.execute(
            "ALTER TABLE organizations ALTER COLUMN plan DROP DEFAULT"
        )

    # ── 1. bootstrap_tokens 還原 ──
    op.execute("DROP INDEX IF EXISTS idx_bootstrap_tokens_token")
    op.execute("DROP TABLE IF EXISTS bootstrap_tokens")
