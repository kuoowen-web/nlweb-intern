"""CORE-1 回歸測試：results_cache 必須按 user 隔離（跨 user 私有文件洩漏防線）。

full-scan-2026-07 CORE-1（三席同抓 P1）：results_cache 全域 singleton 的
cache key 只用 conversation_id（空則 fallback query+site），完全不含 user_id
→ TTL 300s 內兩個不同 user 若撞相同 conversation_id（或都空 conversation_id
走 query+site fallback key）→ user B 的 generate/summarize 會 retrieve 到
user A 的排序結果，含 A 的 private 文件。

D-2026-07-20 規則 1 根解：
  - cache key 必須併入 trusted user_id（如 f"{user_id}:{conversation_id}"）。
  - 弱 fallback key（conversation_id 空）→ 直接不 cache（store/retrieve 都
    return，不用可碰撞 key）。

本測試對 ResultsCache 公開 API（store/retrieve）驗行為，不 mock 內部。
"""
import unittest

from core.results_cache import ResultsCache


class ResultsCacheUserIsolationTest(unittest.TestCase):
    def setUp(self):
        # 用獨立 instance（非全域 singleton），TTL 給足避免過期干擾
        self.cache = ResultsCache(ttl_seconds=300)

    def test_different_user_same_conversation_id_isolated(self):
        """兩個不同 user_id 撞相同 conversation_id → user B 撈不到 user A 的結果。"""
        conv = "shared-conversation-uuid-1"
        user_a = "user-a-uuid"
        user_b = "user-b-uuid"
        results_a = [{"url": "https://a.example/private", "name": "A private doc"}]

        # user A 存入
        self.cache.store(conv, results_a, "some query", user_id=user_a)

        # user A 自己撈得到
        self.assertEqual(
            self.cache.retrieve(conv, user_id=user_a),
            results_a,
            "user A 應撈到自己 cache 的結果",
        )

        # user B 用相同 conversation_id 撈 → 必須 miss（隔離）
        self.assertIsNone(
            self.cache.retrieve(conv, user_id=user_b),
            "user B 不得撈到 user A 的 cached 結果（跨 user 洩漏）",
        )

    def test_empty_conversation_id_not_cached(self):
        """conversation_id 為空（弱 fallback）→ 直接不 cache，retrieve 恆 miss。"""
        results = [{"url": "https://x.example/doc", "name": "doc"}]

        # conversation_id 空 → store 應直接不寫入
        self.cache.store("", results, "shared query", user_id="user-a-uuid")

        # 即使同一 user、同一空 key 也撈不到（因為根本沒 cache）
        self.assertIsNone(
            self.cache.retrieve("", user_id="user-a-uuid"),
            "空 conversation_id 屬弱 fallback key，必須不 cache",
        )

    def test_none_user_id_not_cached(self):
        """user_id 為 None（未認證身分）→ 不 cache（無法歸屬即不可共享）。"""
        results = [{"url": "https://x.example/doc", "name": "doc"}]
        conv = "conv-uuid-2"

        self.cache.store(conv, results, "query", user_id=None)
        self.assertIsNone(
            self.cache.retrieve(conv, user_id=None),
            "user_id None 無法安全歸屬，必須不 cache",
        )

    def test_same_user_same_conversation_hits(self):
        """同一 user + 同一非空 conversation_id → 正常命中（不破壞既有復用）。"""
        conv = "conv-uuid-3"
        user = "user-c-uuid"
        results = [{"url": "https://c.example/doc", "name": "doc c"}]

        self.cache.store(conv, results, "query", user_id=user)
        self.assertEqual(self.cache.retrieve(conv, user_id=user), results)


if __name__ == "__main__":
    unittest.main()
