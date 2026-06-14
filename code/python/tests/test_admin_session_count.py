"""Tests for GET /api/admin/session-count?user_id=<uuid> admin-only endpoint.

Used by E2E spawn-detection tests for authoritative PG row count (replaces
UI-count substitute that lacked precision).
"""
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase


def _build_app(role: str = 'admin', authenticated: bool = True):
    """Build app with fake auth middleware setting request['user'] role."""
    from webserver.routes.admin import setup_admin_routes
    app = web.Application()

    @web.middleware
    async def fake_auth(request, handler):
        if authenticated:
            request['user'] = {
                'authenticated': True,
                'id': f'{role}-uuid-1',
                'org_id': 'org-uuid-1',
                'role': role,
                'email': f'{role}@example.com',
            }
        return await handler(request)

    app.middlewares.append(fake_auth)
    setup_admin_routes(app)
    return app


class AdminSessionCountAdminTest(AioHTTPTestCase):
    async def get_application(self):
        return _build_app(role='admin')

    async def test_admin_can_query_count(self):
        with patch('webserver.routes.admin._count_sessions_for_user',
                   new=AsyncMock(return_value=7)):
            resp = await self.client.get(
                '/api/admin/session-count?user_id=target-uuid-1'
            )
            self.assertEqual(resp.status, 200)
            body = await resp.json()
            self.assertIn('count', body)
            self.assertEqual(body['count'], 7)
            self.assertEqual(body['user_id'], 'target-uuid-1')

    async def test_missing_user_id_returns_400(self):
        resp = await self.client.get('/api/admin/session-count')
        self.assertEqual(resp.status, 400)


class AdminSessionCountMemberTest(AioHTTPTestCase):
    async def get_application(self):
        return _build_app(role='member')

    async def test_member_forbidden(self):
        resp = await self.client.get(
            '/api/admin/session-count?user_id=target-uuid-1'
        )
        self.assertEqual(resp.status, 403)


class AdminSessionCountUnauthenticatedTest(AioHTTPTestCase):
    async def get_application(self):
        # No auth middleware - request['user'] not set
        from webserver.routes.admin import setup_admin_routes
        app = web.Application()
        setup_admin_routes(app)
        return app

    async def test_no_cookie_returns_401(self):
        resp = await self.client.get(
            '/api/admin/session-count?user_id=target-uuid-1'
        )
        self.assertEqual(resp.status, 401)


if __name__ == '__main__':
    unittest.main()
