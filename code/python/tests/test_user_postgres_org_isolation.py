"""Unit tests for user_postgres_provider org isolation WHERE-clause builder.

驗證拍板 2：org filter 從條件式改強制。
- 有 org_id → org_id = %s
- 無 org_id → org_id IS NULL（不是跳過過濾）
不連 DB，只測 clause/param 組裝的純邏輯。
"""
import unittest

from retrieval_providers.user_postgres_provider import _build_user_docs_where


class BuildWhereOrgIsolationTest(unittest.TestCase):
    def test_with_org_id_adds_equality_clause(self):
        where_sql, params = _build_user_docs_where(
            user_id="user-A", org_id="org-1", source_ids=None
        )
        self.assertIn("user_id = %s", where_sql)
        self.assertIn("org_id = %s", where_sql)
        self.assertNotIn("org_id IS NULL", where_sql)
        self.assertEqual(params, ["user-A", "org-1"])

    def test_without_org_id_uses_is_null_not_skip(self):
        # 拍板 2 核心：無 org 不再「跳過過濾」，改成隔離到 org_id IS NULL
        where_sql, params = _build_user_docs_where(
            user_id="user-A", org_id=None, source_ids=None
        )
        self.assertIn("user_id = %s", where_sql)
        self.assertIn("org_id IS NULL", where_sql)
        self.assertNotIn("org_id = %s", where_sql)
        # org_id IS NULL 是字面 SQL，不進 params
        self.assertEqual(params, ["user-A"])

    def test_empty_string_org_id_treated_as_no_org(self):
        # org_id 空字串（client 傳空）視同無 org → IS NULL
        where_sql, params = _build_user_docs_where(
            user_id="user-A", org_id="", source_ids=None
        )
        self.assertIn("org_id IS NULL", where_sql)
        self.assertEqual(params, ["user-A"])

    def test_source_ids_still_appended(self):
        where_sql, params = _build_user_docs_where(
            user_id="user-A", org_id="org-1", source_ids=["s1", "s2"]
        )
        self.assertIn("org_id = %s", where_sql)
        self.assertIn("source_id IN (%s, %s)", where_sql)
        self.assertEqual(params, ["user-A", "org-1", "s1", "s2"])


if __name__ == "__main__":
    unittest.main()
