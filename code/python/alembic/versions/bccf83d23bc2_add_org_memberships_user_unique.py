"""add UNIQUE constraint on org_memberships.user_id (一 email 一公司)

full-scan-2026-07 W-3+W-4（D-2026-07-20 規則 3）：CEO 產品意圖＝同一 email
只註冊於一間公司。但 org_memberships.user_id 只有普通 index 無 UNIQUE，且
三入口（accept_invitation / admin_create_user / create_organization）皆可為
同一 user 建多筆 membership → delete_user / set_user_active 跨組織 blast
radius（單一 org admin 可全域刪除/停用跨 org 使用者）。

根解＝把「一人一公司」規則寫進 DB：org_memberships.user_id 加 UNIQUE。
constraint 存在後多組織使用者不可能存在，delete_user / set_user_active 的
跨組織問題自動消失（該 code 維持現狀 + 已在其上方加註解說明此不變式）。

⚠ 上線前防呆（D-2026-07-20 明列）：upgrade() **先查現有重複 user_id**，若存在
→ raise 明確錯誤（列出 offending user_id + 各自筆數），不讓它撞 raw DB
integrity error。歷史遺留多重 membership 必須先由人工清理再重跑。

Dialect 分流（比照鏈上既有 migration `bind.dialect.name` 慣例）：
  - PostgreSQL：ALTER TABLE ... ADD CONSTRAINT ... UNIQUE (user_id)。
  - SQLite：CREATE UNIQUE INDEX（SQLite ALTER TABLE 不支援 ADD CONSTRAINT）。
兩者皆 idempotent-friendly：已存在則跳過。

Revision ID: bccf83d23bc2
Revises: 9863ee09ce82
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bccf83d23bc2'
down_revision: Union[str, Sequence[str], None] = '9863ee09ce82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# UNIQUE 約束 / index 名稱（PG constraint 與 SQLite index 共用此名）。
_UNIQUE_NAME = 'uq_org_memberships_user'
# 舊的非唯一 index（baseline :209 建），加 UNIQUE 後保留無妨（UNIQUE 本身即可
# 供查詢用），但為避免重複索引，SQLite 路徑會在建 UNIQUE 後 drop 它。
_OLD_INDEX_NAME = 'idx_org_memberships_user'


class DuplicateMembershipError(RuntimeError):
    """org_memberships 存在同一 user_id 多筆 membership，無法加 UNIQUE。"""


def _raise_if_duplicate_user_ids(bind) -> None:
    """查 org_memberships 是否有重複 user_id，有則 raise 明確錯誤。

    `bind` 可以是 SQLAlchemy Connection 或 sqlite3.Connection（測試直接傳
    sqlite3 連線）——兩者都支援 .execute()。回傳的 row 以位置索引取值，
    避免依賴 dict/tuple 差異。

    重複判定：GROUP BY user_id HAVING COUNT(*) > 1。
    """
    sql = (
        "SELECT user_id, COUNT(*) AS cnt FROM org_memberships "
        "GROUP BY user_id HAVING COUNT(*) > 1 ORDER BY cnt DESC"
    )
    result = bind.execute(sa.text(sql)) if _is_sqlalchemy_bind(bind) else bind.execute(sql)
    dups = [(row[0], row[1]) for row in result.fetchall()]
    if dups:
        detail = ", ".join(f"{uid} (x{cnt})" for uid, cnt in dups)
        raise DuplicateMembershipError(
            "無法對 org_memberships.user_id 加 UNIQUE：偵測到重複 user_id "
            f"（同一 user 多筆 membership）。offending user_id: {detail}。"
            "請先人工清理（合併/移除多餘 membership）後再重跑此 migration。"
        )


def _is_sqlalchemy_bind(bind) -> bool:
    """判斷 bind 是否為 SQLAlchemy connection（需用 sa.text 包 SQL）。"""
    return hasattr(bind, 'dialect')


def upgrade() -> None:
    bind = op.get_bind()

    # 上線前防呆：先查重複，有則 raise 明確錯誤（不撞 raw integrity error）。
    _raise_if_duplicate_user_ids(bind)

    is_pg = bind.dialect.name == 'postgresql'
    if is_pg:
        # PG：加具名 UNIQUE constraint。IF NOT EXISTS 不適用於 ADD CONSTRAINT，
        # 用 DO 區塊查 pg_constraint 達成 idempotent。
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = '{_UNIQUE_NAME}'
                ) THEN
                    ALTER TABLE org_memberships
                        ADD CONSTRAINT {_UNIQUE_NAME} UNIQUE (user_id);
                END IF;
            END $$;
            """
        )
        # 舊的非唯一 index 與 UNIQUE 重複，移除以免雙索引。
        op.execute(f"DROP INDEX IF EXISTS {_OLD_INDEX_NAME}")
    else:
        # SQLite：CREATE UNIQUE INDEX（ALTER TABLE 不支援 ADD CONSTRAINT）。
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE_NAME} "
            "ON org_memberships(user_id)"
        )
        # 移除舊的非唯一 index（UNIQUE index 已可供查詢）。
        op.execute(f"DROP INDEX IF EXISTS {_OLD_INDEX_NAME}")


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'
    if is_pg:
        op.execute(f"ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS {_UNIQUE_NAME}")
        # 還原舊的非唯一 index。
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_OLD_INDEX_NAME} "
            "ON org_memberships(user_id)"
        )
    else:
        op.execute(f"DROP INDEX IF EXISTS {_UNIQUE_NAME}")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_OLD_INDEX_NAME} "
            "ON org_memberships(user_id)"
        )
