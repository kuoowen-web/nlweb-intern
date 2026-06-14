"""
Tests for Task 6 Backend Variant: register / activate handlers auto-issue
access_token + refresh_token cookies (mirroring login_handler pattern).

Why: previously these handlers returned only `{success, user}` and the inline
JS had to display a "前往登入" link, then the user had to log in manually. That
created the bootstrap onboarding leak window (UI shows stale admin state until
the user navigates away). With cookies auto-issued, the inline JS can redirect
straight to "/" and the existing init-sync flow takes over.

We mock the AuthService at the route module level so we test only what the
handler does with the service result (cookie shape, body shape, status).
"""

import os
import unittest
from unittest.mock import patch, AsyncMock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

os.environ.setdefault('JWT_SECRET', 'test-secret-for-onboarding-cookie-tests')


def _make_app():
    """Build an aiohttp app with the auth routes wired up."""
    from webserver.routes.auth import setup_auth_routes
    app = web.Application()
    setup_auth_routes(app)
    return app


# ── Helpers ──────────────────────────────────────────────────────


def _assert_token_cookie(testcase, set_cookie_headers, name, *,
                         expected_path, expected_max_age):
    """Find Set-Cookie header for `name`, assert flags match login_handler pattern.

    aiohttp returns multiple Set-Cookie headers as a list when accessed via
    `resp.headers.getall('Set-Cookie')`.
    """
    matching = [h for h in set_cookie_headers if h.split('=', 1)[0] == name]
    testcase.assertEqual(
        len(matching), 1,
        f"Expected exactly one Set-Cookie for {name}, got {matching}"
    )
    header = matching[0]
    # Required security flags (mirror login_handler line 110-125)
    testcase.assertIn('HttpOnly', header, f"{name} missing HttpOnly: {header}")
    testcase.assertIn('Secure', header, f"{name} missing Secure: {header}")
    testcase.assertIn('SameSite=Lax', header, f"{name} missing SameSite=Lax: {header}")
    testcase.assertIn(f'Path={expected_path}', header,
                      f"{name} wrong Path: {header}")
    testcase.assertIn(f'Max-Age={expected_max_age}', header,
                      f"{name} wrong Max-Age: {header}")


# ── Register handler ─────────────────────────────────────────────


class RegisterIssuesCookiesTest(AioHTTPTestCase):
    async def get_application(self):
        return _make_app()

    async def test_register_success_sets_both_token_cookies(self):
        fake_user = {
            'id': 'user-uuid-admin-1',
            'email': 'admin@example.com',
            'name': 'Admin',
            'email_verified': True,
            'access_token': 'fake.access.jwt',
            'refresh_token': 'fake-refresh-opaque',
        }
        with patch('webserver.routes.auth._get_service') as svc_factory:
            svc = svc_factory.return_value
            svc.register_user = AsyncMock(return_value=fake_user)

            resp = await self.client.post('/api/auth/register', json={
                'email': 'admin@example.com',
                'password': 'Passw0rd!',
                'name': 'Admin',
                'org_name': 'Acme',
                'bootstrap_token': 'bt-1',
            })

            self.assertEqual(resp.status, 201)
            body = await resp.json()
            self.assertTrue(body['success'])
            self.assertEqual(body['user']['email'], 'admin@example.com')
            # Tokens must NOT leak in response body (httpOnly cookies are SSO truth)
            self.assertNotIn('access_token', body)
            self.assertNotIn('refresh_token', body)
            self.assertNotIn('access_token', body.get('user', {}))
            self.assertNotIn('refresh_token', body.get('user', {}))

            cookies = resp.headers.getall('Set-Cookie', [])
            _assert_token_cookie(self, cookies, 'access_token',
                                 expected_path='/', expected_max_age=15 * 60)
            _assert_token_cookie(self, cookies, 'refresh_token',
                                 expected_path='/api/auth',
                                 expected_max_age=7 * 24 * 3600)

    async def test_register_failure_does_not_set_cookies(self):
        with patch('webserver.routes.auth._get_service') as svc_factory:
            svc = svc_factory.return_value
            svc.register_user = AsyncMock(side_effect=ValueError('Email already registered'))

            resp = await self.client.post('/api/auth/register', json={
                'email': 'dup@example.com',
                'password': 'Passw0rd!',
                'name': 'Admin',
                'org_name': 'Acme',
                'bootstrap_token': 'bt-1',
            })

            self.assertEqual(resp.status, 400)
            cookies = resp.headers.getall('Set-Cookie', [])
            self.assertEqual(
                [c for c in cookies if c.startswith('access_token=')
                 or c.startswith('refresh_token=')],
                [],
                "Failure path must not set auth cookies"
            )

    async def test_register_missing_required_fields_no_cookies(self):
        # Validation runs before service call: still no cookies expected.
        with patch('webserver.routes.auth._get_service') as svc_factory:
            svc = svc_factory.return_value
            svc.register_user = AsyncMock()

            resp = await self.client.post('/api/auth/register', json={
                'email': 'a@b.com',
                # missing password / name
            })

            self.assertEqual(resp.status, 400)
            svc.register_user.assert_not_called()
            cookies = resp.headers.getall('Set-Cookie', [])
            self.assertEqual(
                [c for c in cookies if c.startswith('access_token=')
                 or c.startswith('refresh_token=')],
                [],
            )


# ── Activate handler ─────────────────────────────────────────────


class ActivateIssuesCookiesTest(AioHTTPTestCase):
    async def get_application(self):
        return _make_app()

    async def test_activate_success_sets_both_token_cookies(self):
        fake_result = {
            'id': 'user-uuid-member-1',
            'email': 'employee@example.com',
            'name': 'Emp',
            'activated': True,
            'access_token': 'fake.access.jwt',
            'refresh_token': 'fake-refresh-opaque',
        }
        with patch('webserver.routes.auth._get_service') as svc_factory:
            svc = svc_factory.return_value
            svc.activate_account = AsyncMock(return_value=fake_result)

            resp = await self.client.post('/api/auth/activate', json={
                'token': 'activation-token-xyz',
                'password': 'Passw0rd!',
            })

            self.assertEqual(resp.status, 200)
            body = await resp.json()
            self.assertTrue(body['success'])
            self.assertEqual(body['user']['email'], 'employee@example.com')
            # Tokens must not leak in body
            self.assertNotIn('access_token', body)
            self.assertNotIn('refresh_token', body)
            self.assertNotIn('access_token', body.get('user', {}))
            self.assertNotIn('refresh_token', body.get('user', {}))

            cookies = resp.headers.getall('Set-Cookie', [])
            _assert_token_cookie(self, cookies, 'access_token',
                                 expected_path='/', expected_max_age=15 * 60)
            _assert_token_cookie(self, cookies, 'refresh_token',
                                 expected_path='/api/auth',
                                 expected_max_age=7 * 24 * 3600)

    async def test_activate_failure_does_not_set_cookies(self):
        with patch('webserver.routes.auth._get_service') as svc_factory:
            svc = svc_factory.return_value
            svc.activate_account = AsyncMock(side_effect=ValueError('Invalid activation token'))

            resp = await self.client.post('/api/auth/activate', json={
                'token': 'bogus',
                'password': 'Passw0rd!',
            })

            self.assertEqual(resp.status, 400)
            cookies = resp.headers.getall('Set-Cookie', [])
            self.assertEqual(
                [c for c in cookies if c.startswith('access_token=')
                 or c.startswith('refresh_token=')],
                [],
            )


# ── Service-layer contract: service must produce both tokens ──────


class ServiceProducesTokensTest(unittest.IsolatedAsyncioTestCase):
    """End-to-end contract on AuthService: register_user / activate_account return
    `access_token` + `refresh_token` strings that decode with JWT_SECRET."""

    async def asyncSetUp(self):
        # Force sqlite mode
        for k in ('DATABASE_URL', 'ANALYTICS_DATABASE_URL', 'POSTGRES_CONNECTION_STRING'):
            os.environ.pop(k, None)

        from auth.auth_db import AuthDB
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "auth_onboarding.db")
        AuthDB._instance = None
        db = AuthDB(db_path=db_path)
        AuthDB._instance = db
        db._init_database_sync()
        db._initialized = True
        self._db = db

        from auth.auth_service import AuthService
        self._service = AuthService()

        # Stub emails so we don't try to send
        import auth.email_service as es
        self._orig_send_activation = es.send_activation_email
        self._orig_send_verification = es.send_verification_email
        es.send_activation_email = lambda *a, **kw: None
        es.send_verification_email = lambda *a, **kw: None

    async def asyncTearDown(self):
        from auth.auth_db import AuthDB
        import auth.email_service as es
        es.send_activation_email = self._orig_send_activation
        es.send_verification_email = self._orig_send_verification
        AuthDB._instance = None
        self._tmpdir.cleanup()

    async def test_register_user_returns_tokens(self):
        bt_row = await self._service.create_bootstrap_token(org_name_hint='Acme',
                                                            expires_hours=1)
        result = await self._service.register_user(
            'admin@example.com', 'Passw0rd!', 'Admin',
            org_name='Acme', bootstrap_token=bt_row['token']
        )
        self.assertIn('access_token', result)
        self.assertIn('refresh_token', result)
        self.assertTrue(result['access_token'])
        self.assertTrue(result['refresh_token'])
        # Existing fields preserved
        self.assertEqual(result['email'], 'admin@example.com')
        self.assertTrue(result['email_verified'])

        # JWT shape: decodes with current secret and includes user_id + org_id
        import jwt as _jwt
        from auth.auth_service import JWT_ALGORITHM
        payload = _jwt.decode(result['access_token'],
                              os.environ['JWT_SECRET'],
                              algorithms=[JWT_ALGORITHM])
        self.assertEqual(payload['user_id'], result['id'])
        self.assertEqual(payload['email'], 'admin@example.com')
        self.assertIsNotNone(payload.get('org_id'))
        self.assertEqual(payload.get('role'), 'admin')

    async def test_activate_account_returns_tokens(self):
        # Set up: admin creates a user, then we activate.
        bt_row = await self._service.create_bootstrap_token(org_name_hint='Acme',
                                                            expires_hours=1)
        admin = await self._service.register_user(
            'admin@example.com', 'Passw0rd!', 'Admin',
            org_name='Acme', bootstrap_token=bt_row['token']
        )
        admin_membership = await self._db.fetchone(
            "SELECT org_id FROM org_memberships WHERE user_id = ?", (admin['id'],)
        )
        org_id = admin_membership['org_id']

        # admin_create_user uses send_activation_email which we stubbed above
        created = await self._service.admin_create_user(
            'employee@example.com', 'Emp', 'member', org_id, admin['id']
        )
        # Fetch activation token from DB
        row = await self._db.fetchone(
            "SELECT email_verification_token FROM users WHERE id = ?",
            (created['id'],)
        )
        activation_token = row['email_verification_token']

        result = await self._service.activate_account(activation_token, 'Passw0rd!')
        self.assertIn('access_token', result)
        self.assertIn('refresh_token', result)
        self.assertTrue(result['access_token'])
        self.assertTrue(result['refresh_token'])
        # Existing fields preserved
        self.assertEqual(result['email'], 'employee@example.com')
        self.assertTrue(result['activated'])

        import jwt as _jwt
        from auth.auth_service import JWT_ALGORITHM
        payload = _jwt.decode(result['access_token'],
                              os.environ['JWT_SECRET'],
                              algorithms=[JWT_ALGORITHM])
        self.assertEqual(payload['user_id'], result['id'])
        self.assertEqual(payload['email'], 'employee@example.com')
        self.assertEqual(payload.get('org_id'), str(org_id))
        self.assertEqual(payload.get('role'), 'member')


if __name__ == '__main__':
    unittest.main()
