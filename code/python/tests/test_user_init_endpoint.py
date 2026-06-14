"""Tests for GET /api/user/init composite endpoint."""
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase


def _make_app_with_auth(authenticated: bool = True, org_id: str = 'org-uuid-1',
                        role: str = 'member', user_id: str = 'user-uuid-1'):
    """Build an aiohttp app with a fake auth middleware injecting request['user']."""
    from webserver.routes.user_init import setup_user_init_routes
    app = web.Application()

    @web.middleware
    async def fake_auth(request, handler):
        if authenticated:
            request['user'] = {
                'authenticated': True,
                'id': user_id,
                'org_id': org_id,
                'role': role,
                'email': 't@example.com',
            }
        # else: no request['user'] set — endpoint should reject
        return await handler(request)

    app.middlewares.append(fake_auth)
    setup_user_init_routes(app)
    return app


class UserInitReturnsShapeTest(AioHTTPTestCase):
    async def get_application(self):
        return _make_app_with_auth()

    async def test_returns_shape(self):
        with patch('webserver.routes.user_init._get_session_service') as svc_factory, \
             patch('webserver.routes.user_init._get_auth_service') as auth_factory:
            svc = svc_factory.return_value
            svc.list_sessions = AsyncMock(return_value=[{'id': 's1'}])
            svc.get_shared_sessions = AsyncMock(return_value=[{'id': 'sh1'}])
            svc.get_preferences = AsyncMock(return_value={'theme': 'dark'})

            auth_svc = auth_factory.return_value
            auth_svc.get_user_by_id = AsyncMock(return_value={
                'id': 'user-uuid-1', 'email': 't@example.com', 'name': 'Test',
            })
            auth_svc.get_org_by_id = AsyncMock(return_value={
                'id': 'org-uuid-1', 'name': 'Acme',
            })

            resp = await self.client.get('/api/user/init')
            self.assertEqual(resp.status, 200)
            body = await resp.json()
            self.assertTrue(body['success'])
            self.assertIn('user', body)
            self.assertIn('org', body)
            self.assertIn('role', body)
            self.assertIn('sessions', body)
            self.assertIn('shared_sessions', body)
            self.assertIn('preferences', body)
            self.assertEqual(body['user']['id'], 'user-uuid-1')
            self.assertEqual(body['role'], 'member')
            self.assertEqual(len(body['sessions']), 1)
            self.assertEqual(len(body['shared_sessions']), 1)
            self.assertEqual(body['preferences'], {'theme': 'dark'})


class UserInitUserPayloadContractTest(AioHTTPTestCase):
    """Contract: /api/user/init user payload must include org_id + role,
    matching the shape returned by POST /api/auth/login and GET /api/auth/me.

    Background: frontend init-sync overwrites AuthManager._user with the
    init response's user payload. If org_id is missing, isOnline() guards
    flip to false and the "share to org" button + org admin sections
    disappear on page reload. (Hotfix 2026-05-20.)
    """

    async def get_application(self):
        return _make_app_with_auth(org_id='a145df99-c8fa-414b-8396-77232a75c991',
                                   role='admin')

    async def test_user_payload_has_org_id_and_role(self):
        with patch('webserver.routes.user_init._get_session_service') as svc_factory, \
             patch('webserver.routes.user_init._get_auth_service') as auth_factory:
            svc = svc_factory.return_value
            svc.list_sessions = AsyncMock(return_value=[])
            svc.get_shared_sessions = AsyncMock(return_value=[])
            svc.get_preferences = AsyncMock(return_value={})

            auth_svc = auth_factory.return_value
            # Note: get_user_by_id does NOT return org_id / role (intentional
            # at the DB layer — org membership is a separate table). The
            # handler must inject them post-fetch.
            auth_svc.get_user_by_id = AsyncMock(return_value={
                'id': 'user-uuid-1', 'email': 'admin@example.com', 'name': 'Admin',
            })
            auth_svc.get_org_by_id = AsyncMock(return_value={
                'id': 'a145df99-c8fa-414b-8396-77232a75c991', 'name': 'Acme',
            })

            resp = await self.client.get('/api/user/init')
            self.assertEqual(resp.status, 200)
            body = await resp.json()

            # Contract assertions: user payload mirrors login response shape
            self.assertIn('org_id', body['user'],
                          "user payload missing org_id — frontend isOnline() will break")
            self.assertEqual(body['user']['org_id'],
                             'a145df99-c8fa-414b-8396-77232a75c991')
            self.assertIn('role', body['user'],
                          "user payload missing role — admin UI guards will break")
            self.assertEqual(body['user']['role'], 'admin')

            # And user.org_id must equal top-level org.id (single source of truth)
            self.assertEqual(body['user']['org_id'], body['org']['id'])


class UserInitUnauthenticatedTest(AioHTTPTestCase):
    async def get_application(self):
        # No fake_auth middleware — endpoint gets no request['user']
        from webserver.routes.user_init import setup_user_init_routes
        app = web.Application()
        setup_user_init_routes(app)
        return app

    async def test_unauthenticated_returns_401(self):
        resp = await self.client.get('/api/user/init')
        self.assertEqual(resp.status, 401)


class UserInitMissingOrgTest(AioHTTPTestCase):
    async def get_application(self):
        return _make_app_with_auth(org_id=None)

    async def test_missing_org_returns_400(self):
        resp = await self.client.get('/api/user/init')
        self.assertEqual(resp.status, 400)


if __name__ == '__main__':
    unittest.main()
