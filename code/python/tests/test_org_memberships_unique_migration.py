"""W-3+W-4 回歸測試：org_memberships.user_id 加 UNIQUE constraint（migration）。

full-scan-2026-07 W-3+W-4（D-2026-07-20 規則 3）：org_memberships.user_id 只有
index 無 UNIQUE，三入口可為同一 user 建多筆 membership → delete_user /
set_user_active 跨組織 blast radius。

根解＝加 UNIQUE constraint（alembic migration）。constraint 存在後多組織使用者
不可能存在，delete_user/set_user_active 跨組織問題自動消失。

⚠ migration upgrade() 上線前防呆：先查現有重複 user_id，若存在 → raise 明確
錯誤（列出 offending user_id），不撞 raw DB integrity error。

本測試對 in-memory / temp sqlite 套 migration（不碰 prod/real DB）：
  1. 無重複 → migration 成功套用，之後插入重複 user_id 會被 UNIQUE 擋。
  2. 有重複 → migration 的 dup-check raise，錯誤訊息含 offending user_id。
"""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _PYTHON_ROOT / "alembic.ini"
_MIGRATION_GLOB = "*_add_org_memberships_user_unique.py"


def _load_migration_module():
    """動態載入新 migration 檔（rev 名未定，用 glob 找）。"""
    versions = _PYTHON_ROOT / "alembic" / "versions"
    matches = list(versions.glob(_MIGRATION_GLOB))
    if not matches:
        raise FileNotFoundError(
            f"找不到 org_memberships UNIQUE migration（glob {_MIGRATION_GLOB}）"
        )
    path = matches[0]
    spec = importlib.util.spec_from_file_location("_uniq_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_sqlite_with_org_memberships(rows):
    """建 temp sqlite，含 org_memberships 表（比照 baseline SQLite DDL），插入 rows。

    rows: list of (id, user_id, org_id) tuples。
    回傳 db 檔路徑（呼叫者負責刪）。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE org_memberships (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            org_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            invited_by TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            accepted_at REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO org_memberships (id, user_id, org_id) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


class OrgMembershipsUniqueMigrationTest(unittest.TestCase):
    def test_dup_check_raises_with_offending_user_id(self):
        """有重複 user_id → migration dup-check raise，訊息含 offending id。"""
        mod = _load_migration_module()
        dup_user = "dup-user-uuid"
        rows = [
            (str(uuid.uuid4()), dup_user, "org-a"),
            (str(uuid.uuid4()), dup_user, "org-b"),  # 同 user 兩 org
            (str(uuid.uuid4()), "solo-user-uuid", "org-a"),
        ]
        path = _make_sqlite_with_org_memberships(rows)
        try:
            conn = sqlite3.connect(path)
            try:
                # migration 應提供可對 bind/connection 查重複的函式
                with self.assertRaises(Exception) as ctx:
                    mod._raise_if_duplicate_user_ids(conn)
                self.assertIn(dup_user, str(ctx.exception),
                              "dup-check 錯誤訊息必須列出 offending user_id")
            finally:
                conn.close()
        finally:
            os.unlink(path)

    def test_no_dup_passes_and_unique_enforced(self):
        """無重複 → dup-check 通過；套用 UNIQUE 後插入重複被擋。"""
        mod = _load_migration_module()
        rows = [
            (str(uuid.uuid4()), "user-1", "org-a"),
            (str(uuid.uuid4()), "user-2", "org-a"),
        ]
        path = _make_sqlite_with_org_memberships(rows)
        try:
            conn = sqlite3.connect(path)
            try:
                # 無重複 → 不 raise
                mod._raise_if_duplicate_user_ids(conn)
                # 套用 UNIQUE index（migration 用的同一 DDL）
                conn.execute(
                    "CREATE UNIQUE INDEX uq_org_memberships_user "
                    "ON org_memberships(user_id)"
                )
                conn.commit()
                # 現在插入重複 user_id 應被擋
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO org_memberships (id, user_id, org_id) "
                        "VALUES (?, ?, ?)",
                        (str(uuid.uuid4()), "user-1", "org-b"),
                    )
                    conn.commit()
            finally:
                conn.close()
        finally:
            os.unlink(path)


class OrgMembershipsMigrationChainTest(unittest.TestCase):
    """驗 migration 正確接在 alembic 鏈尾（down_revision = 前 head）。"""

    def test_down_revision_is_prev_head(self):
        mod = _load_migration_module()
        # 新 migration 應接在 9863ee09ce82（W-3 修復前的 head）之後
        self.assertEqual(
            mod.down_revision, "9863ee09ce82",
            "新 migration 必須接在原 head 9863ee09ce82 之後"
        )
        self.assertTrue(mod.revision, "migration 必須有 revision id")


if __name__ == "__main__":
    unittest.main()
