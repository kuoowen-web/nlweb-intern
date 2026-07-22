"""W-2 回歸測試：session/auth 序列化出口必須剝除 null byte（U+0000）。

full-scan-2026-07 W-2（三席同抓 P1）：使用者字串含 null byte（U+0000）時
json.dumps 序列化為跳脫 \\u0000，PostgreSQL JSONB/TEXT 明確拒絕 →
InvalidTextRepresentation → route 回 500（非 fail-closed）。漏洞散佈
session_service 所有 JSONB/TEXT 寫入點 + auth org/user name 入庫點。

D-2026-07-20 規則 4 根解：寫入前統一 sanitize helper（遞迴剝除字串 null
byte），掛 session_service 所有序列化出口 + auth org/user name 入庫點。單點
helper 覆蓋全族，非逐 call site 補丁。

本測試針對單點 helper `_strip_null_bytes` 驗遞迴剝除行為（str/dict/list/
nested），並驗 session_service._dumps_safe 出口不含 \\x00。
"""
import json
import unittest


class StripNullBytesHelperTest(unittest.TestCase):
    def test_strips_from_plain_string(self):
        from core.sanitize import strip_null_bytes
        self.assertEqual(strip_null_bytes("a\x00b"), "ab")

    def test_strips_from_nested_dict_and_list(self):
        from core.sanitize import strip_null_bytes
        payload = {
            "title": "hel\x00lo",
            "items": ["x\x00", {"k": "v\x00v"}],
            "num": 42,
            "none": None,
        }
        cleaned = strip_null_bytes(payload)
        self.assertEqual(cleaned["title"], "hello")
        self.assertEqual(cleaned["items"][0], "x")
        self.assertEqual(cleaned["items"][1]["k"], "vv")
        self.assertEqual(cleaned["num"], 42)
        self.assertIsNone(cleaned["none"])
        # 序列化後不得含跳脫 null byte
        self.assertNotIn("\\u0000", json.dumps(cleaned))

    def test_strips_dict_keys_too(self):
        from core.sanitize import strip_null_bytes
        cleaned = strip_null_bytes({"k\x00ey": "v"})
        self.assertIn("key", cleaned)
        self.assertNotIn("k\x00ey", cleaned)

    def test_preserves_non_string_scalars(self):
        from core.sanitize import strip_null_bytes
        self.assertEqual(strip_null_bytes(42), 42)
        self.assertEqual(strip_null_bytes(True), True)
        self.assertIsNone(strip_null_bytes(None))


class SessionServiceDumpsSafeTest(unittest.TestCase):
    """驗 session_service 的統一序列化出口剝 null byte（不炸、內容乾淨）。"""

    def test_dumps_safe_strips_null_bytes(self):
        from core.session_service import SessionService
        # _dumps_safe 是 staticmethod / classmethod，不需 DB 連線
        out = SessionService._dumps_safe({"msg": "he\x00llo", "list": ["a\x00"]})
        self.assertIsInstance(out, str)
        self.assertNotIn("\\u0000", out)
        loaded = json.loads(out)
        self.assertEqual(loaded["msg"], "hello")
        self.assertEqual(loaded["list"][0], "a")


class SetPreferenceKeySanitizeTest(unittest.TestCase):
    """review 補修：set_preference 的 preference_key（client 可控，PUT
    /api/preferences/{key} path 直入）也必須剝 null byte——不只 value。"""

    def test_preference_key_null_byte_stripped(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from core.session_service import SessionService

        with patch('auth.auth_db.AuthDB.get_instance') as gi:
            db = MagicMock()
            db.db_type = 'sqlite'
            db.execute = AsyncMock()
            gi.return_value = db
            svc = SessionService()
            asyncio.run(svc.set_preference('u1', 'o1', 'k\x00ey', {'v': 1}))

            self.assertTrue(db.execute.await_count >= 1)
            params = db.execute.await_args.args[1]
            joined = ''.join(str(p) for p in params)
            self.assertNotIn('\x00', joined,
                             "preference_key 的 null byte 未被剝除")
            self.assertIn('key', params, "剝除後的 key 應仍傳入 DB")


class AuthSanitizeNameTest(unittest.TestCase):
    """驗 auth_service 有可用的 name sanitize 出口（org/user name 入庫前剝 null）。"""

    def test_auth_service_sanitize_name_strips_null(self):
        from auth.auth_service import AuthService
        cleaned = AuthService._sanitize_db_text("Ac\x00me")
        self.assertEqual(cleaned, "Acme")

    def test_auth_service_sanitize_name_handles_none(self):
        from auth.auth_service import AuthService
        self.assertIsNone(AuthService._sanitize_db_text(None))


if __name__ == "__main__":
    unittest.main()
