"""Unit tests for baseHandler L2 trusted-identity resolution.

驗證拍板 1（L2 single source）：
- wrapper.request['user'] authenticated → 用 server 值覆蓋 client 偽造值
- wrapper=None / 無 request / 未認證 → fallback query_params（不破壞既有路徑）
"""
import unittest
from types import SimpleNamespace

from core.baseHandler import _resolve_trusted_identity


class _FakeRequest(dict):
    """dict 子類：request['user'] 用 dict access，request.get('user') 也可用。"""


def _wrapper_with_user(user):
    req = _FakeRequest()
    if user is not None:
        req["user"] = user
    return SimpleNamespace(request=req)


class ResolveTrustedIdentityTest(unittest.TestCase):
    def test_server_identity_overrides_client_spoof(self):
        # client 偽造 user_id=attacker-target，但 JWT 是 real-user
        qp = {"user_id": "attacker-target", "org_id": "attacker-org"}
        wrapper = _wrapper_with_user({
            "id": "real-user", "org_id": "real-org", "authenticated": True,
        })
        user_id, org_id = _resolve_trusted_identity(qp, wrapper)
        self.assertEqual(user_id, "real-user")
        self.assertEqual(org_id, "real-org")

    def test_server_org_none_overrides_client_org(self):
        # JWT 無 org（合法無 org user）→ org_id 應為 None，不採 client 的 org
        qp = {"user_id": "attacker-target", "org_id": "attacker-org"}
        wrapper = _wrapper_with_user({
            "id": "real-user", "org_id": None, "authenticated": True,
        })
        user_id, org_id = _resolve_trusted_identity(qp, wrapper)
        self.assertEqual(user_id, "real-user")
        self.assertIsNone(org_id)

    def test_fallback_to_query_params_when_no_wrapper(self):
        # http_handler=None（非 streaming / from_message 路徑）→ fallback
        qp = {"user_id": "u-1", "org_id": "o-1"}
        user_id, org_id = _resolve_trusted_identity(qp, None)
        self.assertEqual(user_id, "u-1")
        self.assertEqual(org_id, "o-1")

    def test_fallback_when_wrapper_has_no_request(self):
        wrapper = SimpleNamespace()  # 無 .request 屬性
        qp = {"user_id": "u-2", "org_id": "o-2"}
        user_id, org_id = _resolve_trusted_identity(qp, wrapper)
        self.assertEqual(user_id, "u-2")
        self.assertEqual(org_id, "o-2")

    def test_fallback_when_user_not_authenticated(self):
        # request 有 user 但 authenticated=False（soft-auth 失敗）→ 不採信 → fallback
        qp = {"user_id": "u-3", "org_id": "o-3"}
        wrapper = _wrapper_with_user({"id": "x", "authenticated": False})
        user_id, org_id = _resolve_trusted_identity(qp, wrapper)
        self.assertEqual(user_id, "u-3")
        self.assertEqual(org_id, "o-3")

    def test_no_identity_anywhere_returns_none(self):
        user_id, org_id = _resolve_trusted_identity({}, None)
        self.assertIsNone(user_id)
        self.assertIsNone(org_id)


if __name__ == "__main__":
    unittest.main()
