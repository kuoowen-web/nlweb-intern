"""add lr_dialog_snapshot column to search_sessions (was out-of-band manual script)

把 `lr_dialog_snapshot` 欄位正式納入 alembic 鏈。

背景：
  此欄位原本只由旁路手動腳本 `tools/migrate_lr_dialog_snapshot.py` 跑 raw
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 建立，從未進 alembic head。
  後果：deploy 時 `alembic upgrade head` 不會帶上它，prod 永遠缺這欄位，
  導致 LR session 的 dialog snapshot 無法落地。本 migration 收編該欄位，
  讓它隨 deploy 自動上 prod。

欄位語意（沿用手動腳本註解）：
  `lr_dialog_snapshot` 儲存 #lrChat 對話的前端 DOM snapshot（JSON array），
  與 `live_research_state` 隔離 —— 後端 `_save_state` 只寫 live_research_state，
  不會覆蓋此欄位。

設計原則（沿用 7c2f4ae6b1d3 / 1015e1c40f88 catch-up 模式）：
  - 完全 idempotent：對已手動跑過腳本的環境 = no-op，不會炸。
  - 用 `bind.dialect.name == 'postgresql'` 分流 PG / SQLite，與手動腳本一致。
    * PG：`ADD COLUMN IF NOT EXISTS ... JSONB DEFAULT '[]'`（PG 原生支援
      column-level IF NOT EXISTS + JSONB）。
    * SQLite：`ADD COLUMN ... TEXT DEFAULT '[]'`（SQLite 沒有 column-level
      IF NOT EXISTS，改用 PRAGMA 預檢欄位是否存在達成 idempotent）。
  - 純 raw SQL via `op.execute(...)`，不重塑 schema、不改既有欄位。

Revision ID: 9863ee09ce82
Revises: 7c2f4ae6b1d3
Create Date: 2026-06-19 20:15:28.252487

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9863ee09ce82'
down_revision: Union[str, Sequence[str], None] = '7c2f4ae6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """Dialect-agnostic 欄位存在檢查（給 SQLite 用，因為 SQLite 沒有
    column-level ADD COLUMN IF NOT EXISTS）。"""
    insp = sa.inspect(bind)
    cols = {c['name'] for c in insp.get_columns(table)}
    return column in cols


def upgrade() -> None:
    """新增 search_sessions.lr_dialog_snapshot 欄位（idempotent）。

    與 tools/migrate_lr_dialog_snapshot.py 完全等價：
      - PG：JSONB（native JSON），用 ADD COLUMN IF NOT EXISTS（PG 原生 idempotent）。
      - SQLite：TEXT（serialized JSON string），手動 PRAGMA 預檢達成 idempotent。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        # PostgreSQL：JSONB 原生型別，ADD COLUMN IF NOT EXISTS 為 PG 原生 idempotent。
        op.execute(
            "ALTER TABLE search_sessions "
            "ADD COLUMN IF NOT EXISTS lr_dialog_snapshot JSONB DEFAULT '[]'"
        )
    else:
        # SQLite：無 column-level IF NOT EXISTS，先 PRAGMA 預檢避免 duplicate column 錯誤。
        if not _column_exists(bind, 'search_sessions', 'lr_dialog_snapshot'):
            op.execute(
                "ALTER TABLE search_sessions "
                "ADD COLUMN lr_dialog_snapshot TEXT DEFAULT '[]'"
            )


def downgrade() -> None:
    """移除 search_sessions.lr_dialog_snapshot 欄位（idempotent）。

    注意：downgrade 會真的 DROP 此欄位（含其中資料）。

      - PG：DROP COLUMN IF EXISTS（PG 原生 idempotent）。
      - SQLite：3.35.0+ 才支援 DROP COLUMN，且無 column-level IF EXISTS，
        故先 PRAGMA 預檢。若該欄位不存在則 no-op。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        op.execute(
            "ALTER TABLE search_sessions DROP COLUMN IF EXISTS lr_dialog_snapshot"
        )
    else:
        if _column_exists(bind, 'search_sessions', 'lr_dialog_snapshot'):
            op.execute(
                "ALTER TABLE search_sessions DROP COLUMN lr_dialog_snapshot"
            )
