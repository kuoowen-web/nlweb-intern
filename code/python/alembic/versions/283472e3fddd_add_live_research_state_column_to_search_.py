"""add live_research_state column to search_sessions (was out-of-band manual script)

把 `live_research_state` 欄位正式納入 alembic 鏈。

背景（full-scan-2026-07 AS-2 / IT-2，三席同抓皆判 P1）：
  此欄位原本只由旁路手動腳本 `tools/migrate_live_research.py` 跑 raw
  `ALTER TABLE ... ADD COLUMN`（PG 帶 IF NOT EXISTS）建立，從未進 alembic
  head。其姊妹欄位 `lr_dialog_snapshot` 已被 `9863ee09ce82` 專門收編，唯獨
  這個漏了（只在 9863ee docstring 被順帶提及、無任何 DDL）。
  後果：fresh alembic-only PG 部署（Phase 2 移除 auth_db.initialize() DDL 後）
  的 search_sessions 缺此欄位 → `core/session_service._save_state` 的
  `UPDATE search_sessions SET live_research_state=?` 炸 no-such-column →
  整個 Live Research 跨 request 持久化壞。這正是 9863ee 修姊妹欄位時親自描述
  的失敗模式，只修一個漏一個。

  現況無害（VPS / dev 靠 auth_db.initialize() 的 CREATE TABLE 已含此欄位
  [SQLite auth_db:532 / PG auth_db:743] + 歷史手動跑過腳本），latent。本
  migration 收編該欄位，讓它隨 deploy 自動上 prod，並與 legacy DDL 等價
  （Phase 2 regression test `test_alembic_schema_equivalence` 即驗此等價）。

欄位語意（沿用手動腳本註解）：
  `live_research_state` 儲存 `LiveResearchStageState` 的 JSON（keyed by
  session_id），後端 `_save_state` 只寫此欄位。與 `lr_dialog_snapshot`
  （前端 #lrChat DOM snapshot array）隔離，兩者互不覆蓋。

設計原則（沿用 9863ee09ce82 catch-up 模式）：
  - 完全 idempotent：對已手動跑過腳本 / 走 legacy DDL 的環境 = no-op，不炸。
  - 用 `bind.dialect.name == 'postgresql'` 分流 PG / SQLite，與手動腳本一致。
    * PG：`ADD COLUMN IF NOT EXISTS ... JSONB DEFAULT '{}'`（PG 原生支援
      column-level IF NOT EXISTS + JSONB）。
    * SQLite：`ADD COLUMN ... TEXT DEFAULT '{}'`（SQLite 沒有 column-level
      IF NOT EXISTS，改用 PRAGMA 預檢欄位是否存在達成 idempotent）。
  - default 為 `'{}'`（object / dict，因存的是 LiveResearchStageState dict），
    這是與姊妹欄位 lr_dialog_snapshot（default `'[]'` array）唯一的差別，
    對齊 `tools/migrate_live_research.py` 與 auth_db legacy DDL。
  - 純 raw SQL via `op.execute(...)`，不重塑 schema、不改既有欄位。

Revision ID: 283472e3fddd
Revises: bccf83d23bc2
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '283472e3fddd'
down_revision: Union[str, Sequence[str], None] = 'bccf83d23bc2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """Dialect-agnostic 欄位存在檢查（給 SQLite 用，因為 SQLite 沒有
    column-level ADD COLUMN IF NOT EXISTS）。"""
    insp = sa.inspect(bind)
    cols = {c['name'] for c in insp.get_columns(table)}
    return column in cols


def upgrade() -> None:
    """新增 search_sessions.live_research_state 欄位（idempotent）。

    與 tools/migrate_live_research.py 完全等價：
      - PG：JSONB（native JSON），用 ADD COLUMN IF NOT EXISTS（PG 原生 idempotent）。
      - SQLite：TEXT（serialized JSON string），手動 PRAGMA 預檢達成 idempotent。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        # PostgreSQL：JSONB 原生型別，ADD COLUMN IF NOT EXISTS 為 PG 原生 idempotent。
        op.execute(
            "ALTER TABLE search_sessions "
            "ADD COLUMN IF NOT EXISTS live_research_state JSONB DEFAULT '{}'"
        )
    else:
        # SQLite：無 column-level IF NOT EXISTS，先 PRAGMA 預檢避免 duplicate column 錯誤。
        if not _column_exists(bind, 'search_sessions', 'live_research_state'):
            op.execute(
                "ALTER TABLE search_sessions "
                "ADD COLUMN live_research_state TEXT DEFAULT '{}'"
            )


def downgrade() -> None:
    """移除 search_sessions.live_research_state 欄位（idempotent）。

    注意：downgrade 會真的 DROP 此欄位（含其中 LR 持久化資料）。

      - PG：DROP COLUMN IF EXISTS（PG 原生 idempotent）。
      - SQLite：3.35.0+ 才支援 DROP COLUMN，且無 column-level IF EXISTS，
        故先 PRAGMA 預檢。若該欄位不存在則 no-op。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        op.execute(
            "ALTER TABLE search_sessions DROP COLUMN IF EXISTS live_research_state"
        )
    else:
        if _column_exists(bind, 'search_sessions', 'live_research_state'):
            op.execute(
                "ALTER TABLE search_sessions DROP COLUMN live_research_state"
            )
