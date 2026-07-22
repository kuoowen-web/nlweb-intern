"""W-1 回歸測試：analytics / ranking 儀表板讀取端點必須加 admin gate。

full-scan-2026-07 W-1（Codex + in-house 同抓 P1）：analytics 的
stats/queries/top_clicks/export_training_data 與 ranking 的 config/pipeline
六個 GET 端點不在 middleware PUBLIC_ENDPOINTS（要求登入），但 handler 內零
role 檢查 → 任一登入 member（非 admin）可讀/CSV 匯出全平台跨 org 資料。

D-2026-07-20 規則 2 根解：這些端點加 role==admin gate（堵「任一登入者可匯出
全平台資料」）。org_id SQL 邊界現階段不做（無多租戶可隔離），列 TODO。

判定與 routes/admin.py:41-44 對齊：
  - 未登入（request['user'] 無/未 authenticated）→ 401
  - 登入但 role != 'admin' → 403
  - admin → 放行

用與 test_admin_session_count.py 相同的 fake_auth middleware 注入 request['user']。
"""
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase


def _build_app(role: str = 'admin', authenticated: bool = True,
               inject_user: bool = True):
    """Build app with fake auth middleware setting request['user'] role."""
    from webserver.analytics_handler import register_analytics_routes
    from webserver.ranking_analytics_handler import register_ranking_analytics_routes
    app = web.Application()

    @web.middleware
    async def fake_auth(request, handler):
        if inject_user:
            request['user'] = {
                'authenticated': authenticated,
                'id': f'{role}-uuid-1',
                'org_id': 'org-uuid-1',
                'role': role,
                'email': f'{role}@example.com',
            }
        return await handler(request)

    app.middlewares.append(fake_auth)
    register_analytics_routes(app)
    register_ranking_analytics_routes(app)
    return app


class AnalyticsMemberForbiddenTest(AioHTTPTestCase):
    async def get_application(self):
        return _build_app(role='member')

    async def test_member_cannot_export_training_data(self):
        resp = await self.client.get('/api/analytics/export_training_data')
        self.assertEqual(resp.status, 403,
                         "非 admin 登入者匯出 training data 必須被拒（403）")

    async def test_member_cannot_get_stats(self):
        resp = await self.client.get('/api/analytics/stats')
        self.assertEqual(resp.status, 403)

    async def test_member_cannot_get_queries(self):
        resp = await self.client.get('/api/analytics/queries')
        self.assertEqual(resp.status, 403)

    async def test_member_cannot_get_top_clicks(self):
        resp = await self.client.get('/api/analytics/top_clicks')
        self.assertEqual(resp.status, 403)

    async def test_member_cannot_get_ranking_config(self):
        resp = await self.client.get('/api/ranking/config')
        self.assertEqual(resp.status, 403)

    async def test_member_cannot_get_pipeline_details(self):
        resp = await self.client.get('/api/ranking/pipeline/some-query-id')
        self.assertEqual(resp.status, 403)


class AnalyticsUnauthenticatedTest(AioHTTPTestCase):
    async def get_application(self):
        # 不注入 request['user'] → 未登入
        return _build_app(inject_user=False)

    async def test_unauthenticated_export_401(self):
        resp = await self.client.get('/api/analytics/export_training_data')
        self.assertEqual(resp.status, 401,
                         "未登入者必須被拒（401）")

    async def test_unauthenticated_ranking_config_401(self):
        resp = await self.client.get('/api/ranking/config')
        self.assertEqual(resp.status, 401)


class AnalyticsAdminAllowedTest(AioHTTPTestCase):
    async def get_application(self):
        return _build_app(role='admin')

    async def test_admin_passes_gate_for_stats(self):
        # admin 通過 gate；DB 層 mock 掉，只驗「不是被 401/403 擋」。
        with patch('core.analytics_db.AnalyticsDB.fetchone',
                   new=AsyncMock(return_value={'c': 0})), \
             patch('core.analytics_db.AnalyticsDB.fetchall',
                   new=AsyncMock(return_value=[])):
            resp = await self.client.get('/api/analytics/stats')
        self.assertNotIn(resp.status, (401, 403),
                         "admin 不應被 auth gate 擋；實際狀態=%s" % resp.status)


if __name__ == '__main__':
    unittest.main()
