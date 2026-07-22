"""P0 red test: deep_research_handler 必須用 JWT 身分覆蓋 client 偽造的 user_id/org_id。

攻擊情境：已登入 user B 帶 body user_id=<user_A> + include_private_sources
打 /api/deep_research，斷言 handler 收到的 user_id 是 JWT 的 user-B，
不是偽造的 user-A（否則會撈到 A 的私有文件）。

不跑真實研究：monkeypatch DeepResearchHandler 攔截建構 query_params 後
立即丟出 sentinel，驗注入已在建構前發生。
"""
import os
import time
import unittest
from unittest.mock import patch

import jwt
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

os.environ.pop('DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)
_DR_JWT_SECRET = 'test-dr-isolation-secret'
os.environ['JWT_SECRET'] = _DR_JWT_SECRET

from webserver.middleware.auth import auth_middleware  # noqa: E402
from webserver.routes.api import (  # noqa: E402
    deep_research_handler,
    inject_auth_user_into_params,
)


def _jwt_for(user_id, org_id):
    now = int(time.time())
    return jwt.encode(
        {'user_id': user_id, 'email': 'b@test.com', 'name': 'B',
         'org_id': org_id, 'role': 'member', 'iat': now, 'exp': now + 3600},
        _DR_JWT_SECRET, algorithm='HS256',
    )


class _StopConstruction(Exception):
    """Sentinel — 攔到建構參數就停，不跑真實研究。"""
    def __init__(self, query_params):
        self.captured = dict(query_params)
        super().__init__("stopped for assertion")


class DrPrivateDocsIsolationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """ordering 免疫（full-scan-2026-07 收尾）：本測試簽的 JWT 用
        test-dr-isolation-secret，但 auth.py 每 call 現讀 os.environ['JWT_SECRET']。
        全套 collection 下 test_help_routes.py:11 於 import 時把 JWT_SECRET 覆蓋成
        自己的值 → 本測試 JWT 驗簽失敗 → user=None → captured user_id=None → 假紅
        （AssertionError None != 'user-B'）。受害方根解：每 test 執行時用 patch.dict
        重設 JWT_SECRET 為本測試的 secret（addCleanup 自動還原，對其他測試無副作用）。"""
        patcher = patch.dict(os.environ, {'JWT_SECRET': _DR_JWT_SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _make_client(self):
        app = web.Application(middlewares=[auth_middleware])
        app.router.add_post('/api/deep_research', deep_research_handler)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    async def test_jwt_overrides_spoofed_user_id(self):
        client = await self._make_client()
        try:
            captured = {}

            def _fake_ctor(query_params, http_handler):
                captured.update(query_params)
                raise _StopConstruction(query_params)

            token = _jwt_for(user_id='user-B', org_id='org-B')
            # patch 點：api.py 在 handler 函式內 `from methods.deep_research import DeepResearchHandler`
            with patch('methods.deep_research.DeepResearchHandler', side_effect=_fake_ctor):
                resp = await client.post(
                    '/api/deep_research',
                    json={'query': 'x', 'user_id': 'user-A-VICTIM',
                          'org_id': 'org-A-VICTIM', 'include_private_sources': 'true'},
                    headers={'Authorization': f'Bearer {token}'},
                )
                # handler 內部丟 _StopConstruction → 500，但我們只看 captured
                await resp.read()

            self.assertEqual(captured.get('user_id'), 'user-B',
                             "JWT user_id 必須覆蓋 client 偽造的 user-A-VICTIM")
            self.assertEqual(captured.get('org_id'), 'org-B',
                             "JWT org_id 必須覆蓋 client 偽造的 org-A-VICTIM")
        finally:
            await client.close()

    async def test_unauthenticated_request_rejected_401(self):
        client = await self._make_client()
        try:
            resp = await client.post(
                '/api/deep_research',
                json={'query': 'x', 'user_id': 'user-A-VICTIM',
                      'include_private_sources': 'true'},
            )
            self.assertEqual(resp.status, 401,
                             "無 JWT 直打 /api/deep_research 必須 401（endpoint 非 public）")
        finally:
            await client.close()

    async def test_legitimate_user_own_id_preserved(self):
        # 合法：user-B 帶自己的 user_id（或不帶）→ 注入後仍是 user-B
        client = await self._make_client()
        try:
            captured = {}

            def _fake_ctor(query_params, http_handler):
                captured.update(query_params)
                raise _StopConstruction(query_params)

            token = _jwt_for(user_id='user-B', org_id='org-B')
            with patch('methods.deep_research.DeepResearchHandler', side_effect=_fake_ctor):
                resp = await client.post(
                    '/api/deep_research',
                    json={'query': 'x', 'include_private_sources': 'true'},  # 不帶 user_id
                    headers={'Authorization': f'Bearer {token}'},
                )
                await resp.read()
            self.assertEqual(captured.get('user_id'), 'user-B')
            self.assertEqual(captured.get('org_id'), 'org-B')
        finally:
            await client.close()


class InjectAuthUserIntoParamsTest(unittest.TestCase):
    """驗抽出的共用 helper inject_auth_user_into_params（供 DR 持久化 rerun 複用）。

    語義須與 deep_research_handler 原手寫注入塊完全一致：
    authenticated → 無條件覆蓋 user_id/org_id（清偽造殘留）；否則不動 query_params。
    """

    def test_authenticated_user_overrides_params(self):
        # 帶偽造 user_id/org_id 的 query_params，authenticated user 覆蓋成 JWT 值
        qp = {"user_id": "attacker-target", "org_id": "attacker-org"}
        inject_auth_user_into_params(
            qp, {"id": "real-user", "org_id": "real-org", "authenticated": True}
        )
        self.assertEqual(qp["user_id"], "real-user")
        self.assertEqual(qp["org_id"], "real-org")

    def test_authenticated_user_org_none_clears_spoofed_org(self):
        # 無條件覆蓋核心：JWT 無 org（合法無 org user）→ 清掉偽造 org_id 成 None
        qp = {"user_id": "attacker-target", "org_id": "attacker-org"}
        inject_auth_user_into_params(
            qp, {"id": "real-user", "org_id": None, "authenticated": True}
        )
        self.assertEqual(qp["user_id"], "real-user")
        self.assertIsNone(qp["org_id"])

    def test_unauthenticated_user_leaves_params_untouched(self):
        # authenticated=False → fallback 語義，不動 query_params
        qp = {"user_id": "u-1", "org_id": "o-1"}
        inject_auth_user_into_params(qp, {"id": "x", "authenticated": False})
        self.assertEqual(qp, {"user_id": "u-1", "org_id": "o-1"})

    def test_none_user_leaves_params_untouched(self):
        # user=None（未認證）→ 不動 query_params
        qp = {"user_id": "u-2", "org_id": "o-2"}
        inject_auth_user_into_params(qp, None)
        self.assertEqual(qp, {"user_id": "u-2", "org_id": "o-2"})


if __name__ == "__main__":
    unittest.main()
