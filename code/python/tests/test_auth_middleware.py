"""
Tests for webserver/middleware/auth.py — auth_middleware behavior.

Tests public vs protected endpoints, JWT validation, soft auth,
and missing JWT_SECRET handling.

Uses aiohttp.test_utils.TestClient + TestServer (no pytest-aiohttp dependency).

Note: NLWEB_DEV_AUTH_BYPASS removed 2026-05-19 (spec v0.大 §5.4); the per-test
monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', ...) calls are now defensive no-ops
(harmless; protect against stray env vars from operator shells).
"""

import os
import time
import json

import jwt
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from webserver.middleware.auth import auth_middleware, PUBLIC_ENDPOINTS

# Force SQLite mode: pop AFTER imports (load_dotenv in logger.py re-sets them)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)

JWT_SECRET = 'test-middleware-secret-9876'
JWT_ALGORITHM = 'HS256'


# ── Helpers ──────────────────────────────────────────────────────


def _make_jwt(claims: dict, secret: str = JWT_SECRET, expired: bool = False) -> str:
    """Create a JWT token for testing."""
    now = int(time.time())
    payload = {
        'user_id': claims.get('user_id', 'uid-123'),
        'email': claims.get('email', 'user@test.com'),
        'name': claims.get('name', 'Test User'),
        'org_id': claims.get('org_id', 'org-456'),
        'role': claims.get('role', 'member'),
        'iat': now,
        'exp': now - 3600 if expired else now + 3600,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def _build_app() -> web.Application:
    """Create a minimal aiohttp app with the auth middleware and test routes."""
    app = web.Application(middlewares=[auth_middleware])

    async def health(request):
        user = request.get('user')
        return web.json_response({'status': 'ok', 'user': user})

    async def ask(request):
        user = request.get('user')
        return web.json_response({'endpoint': 'ask', 'user': user})

    async def sites(request):
        return web.json_response({'sites': []})

    async def protected(request):
        user = request.get('user')
        return web.json_response({'endpoint': 'protected', 'user': user})

    async def api_sessions(request):
        user = request.get('user')
        return web.json_response({'endpoint': 'sessions', 'user': user})

    app.router.add_get('/health', health)
    app.router.add_get('/ask', ask)
    app.router.add_get('/sites', sites)
    app.router.add_get('/api/protected', protected)
    app.router.add_get('/api/sessions', api_sessions)

    return app


# ── Public Endpoint Tests ───────────────────────────────────────


class TestPublicEndpoints:

    @pytest.mark.asyncio
    async def test_health_no_auth(self, monkeypatch):
        """Public endpoint should pass through without auth."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/health')
            assert resp.status == 200
            data = await resp.json()
            assert data['status'] == 'ok'
            assert data['user'] is None

    @pytest.mark.asyncio
    async def test_sites_no_auth(self, monkeypatch):
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/sites')
            assert resp.status == 200


class TestSoftAuth:

    @pytest.mark.asyncio
    async def test_public_endpoint_with_valid_token(self, monkeypatch):
        """Public endpoint with valid Bearer token -> request['user'] is populated."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        token = _make_jwt({'user_id': 'u-soft', 'name': 'Soft User', 'org_id': 'o-1', 'role': 'admin'})
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/health', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 200
            data = await resp.json()
            assert data['user'] is not None
            assert data['user']['id'] == 'u-soft'
            assert data['user']['authenticated'] is True

    @pytest.mark.asyncio
    async def test_public_endpoint_with_invalid_token(self, monkeypatch):
        """Public endpoint with invalid token -> user is None (soft auth ignores errors)."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/health', headers={'Authorization': 'Bearer bad-token'})
            assert resp.status == 200
            data = await resp.json()
            assert data['user'] is None

    @pytest.mark.asyncio
    async def test_public_endpoint_with_expired_token(self, monkeypatch):
        """Public endpoint with expired token -> user is None (soft auth ignores)."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        token = _make_jwt({}, expired=True)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/health', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 200
            data = await resp.json()
            assert data['user'] is None


# ── Protected Endpoint Tests ─────────────────────────────────────


class TestProtectedEndpoints:

    @pytest.mark.asyncio
    async def test_ask_no_auth_returns_401(self, monkeypatch):
        """/ask is no longer public — unauthenticated requests must get 401."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/ask')
            assert resp.status == 401
            data = await resp.json()
            assert data['type'] == 'auth_required'

    @pytest.mark.asyncio
    async def test_ask_not_in_public_endpoints(self, monkeypatch):
        """/ask must not appear in PUBLIC_ENDPOINTS."""
        assert '/ask' not in PUBLIC_ENDPOINTS
        assert '/api/deep_research' not in PUBLIC_ENDPOINTS
        assert '/api/feedback' not in PUBLIC_ENDPOINTS

    @pytest.mark.asyncio
    async def test_no_token_returns_401(self, monkeypatch):
        """Protected endpoint without token -> 401."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected')
            assert resp.status == 401
            data = await resp.json()
            assert data['type'] == 'auth_required'

    @pytest.mark.asyncio
    async def test_valid_token_passes(self, monkeypatch):
        """Protected endpoint with valid JWT -> request['user'] has id, org_id, role."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        token = _make_jwt({
            'user_id': 'u-prot',
            'email': 'prot@test.com',
            'name': 'Protected User',
            'org_id': 'org-789',
            'role': 'admin',
        })
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 200
            data = await resp.json()
            user = data['user']
            assert user['id'] == 'u-prot'
            assert user['org_id'] == 'org-789'
            assert user['role'] == 'admin'
            assert user['authenticated'] is True

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self, monkeypatch):
        """Protected endpoint with expired JWT -> 401 with type=token_expired."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        token = _make_jwt({}, expired=True)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 401
            data = await resp.json()
            assert data['type'] == 'token_expired'

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, monkeypatch):
        """Protected endpoint with garbage token -> 401."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected', headers={'Authorization': 'Bearer not.a.jwt'})
            assert resp.status == 401
            data = await resp.json()
            assert data['type'] == 'invalid_token'

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_401(self, monkeypatch):
        """Token signed with wrong secret -> 401."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        token = _make_jwt({}, secret='wrong-secret')
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_token_missing_user_id_returns_401(self, monkeypatch):
        """JWT with no user_id claim -> 401 invalid_token."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        now = int(time.time())
        payload = {'email': 'x@y.com', 'iat': now, 'exp': now + 3600}  # no user_id
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 401
            data = await resp.json()
            assert data['type'] == 'invalid_token'


# ── Dev Auth Bypass: DELETED 2026-05-19 ──────────────────────────
# Removed `TestDevAuthBypass` class entirely (was 5 test methods).
# Rationale: NLWEB_DEV_AUTH_BYPASS deleted from middleware/auth.py per
# spec v0.大 §5.4 — E2E must use real admin login (admin@twdubao.com / test1234!).
# No bypass branch left in middleware to test.


# ── JWT_SECRET Not Set Tests ────────────────────────────────────


class TestJWTSecretNotSet:

    @pytest.mark.asyncio
    async def test_protected_returns_500(self, monkeypatch):
        """Protected endpoint without JWT_SECRET configured -> 500."""
        monkeypatch.delenv('JWT_SECRET', raising=False)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)
        token = _make_jwt({})  # signed with test secret, but server has no secret
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/api/protected', headers={'Authorization': f'Bearer {token}'})
            assert resp.status == 500
            data = await resp.json()
            assert data['type'] == 'auth_not_configured'

    @pytest.mark.asyncio
    async def test_public_still_works(self, monkeypatch):
        """Public endpoints should work even without JWT_SECRET."""
        monkeypatch.delenv('JWT_SECRET', raising=False)
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get('/health')
            assert resp.status == 200


# ── Auth Token from Cookie Tests ─────────────────────────────────


class TestCookieAuth:

    @pytest.mark.asyncio
    async def test_auth_from_cookie(self, monkeypatch):
        """Protected endpoint should accept access_token from cookie (BP-1: httpOnly cookie)."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)
        token = _make_jwt({'user_id': 'cookie-user'})
        async with TestClient(TestServer(_build_app())) as client:
            # Middleware reads 'access_token' cookie (BP-1: httpOnly cookie for web UI)
            client.session.cookie_jar.update_cookies({'access_token': token})
            resp = await client.get('/api/protected')
            assert resp.status == 200
            data = await resp.json()
            assert data['user']['id'] == 'cookie-user'


# ── Query Param Auth Tests ──────────────────────────────────────


class TestQueryParamAuth:

    @pytest.mark.asyncio
    async def test_auth_from_query_param(self, monkeypatch):
        """Protected GET endpoint should accept auth_token from query param."""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)
        token = _make_jwt({'user_id': 'query-user'})
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get(f'/api/protected?auth_token={token}')
            assert resp.status == 200
            data = await resp.json()
            assert data['user']['id'] == 'query-user'


# ── Admin Resend Activation Handler Tests ───────────────────────


class TestAdminResendActivationHandler:
    """Unit tests for POST /api/admin/resend-activation handler.

    Uses aiohttp TestClient with a mock service to verify handler HTTP status
    responses for different exception types.
    """

    def _build_app_with_handler(self, mock_service) -> web.Application:
        """Build minimal app with the resend activation handler and mock service."""
        from webserver.routes.auth import admin_resend_activation_handler
        import webserver.routes.auth as auth_routes

        app = web.Application(middlewares=[auth_middleware])

        # Patch the _get_service function to return our mock
        auth_routes._get_service._instance = mock_service

        app.router.add_post('/api/admin/resend-activation', admin_resend_activation_handler)
        return app

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, monkeypatch):
        """Unauthenticated request → 401"""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)

        class MockService:
            async def admin_resend_activation(self, *a, **kw):
                return {'success': True, 'email': 'x@x.com'}

        app = self._build_app_with_handler(MockService())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                '/api/admin/resend-activation',
                json={'user_id': 'uid-123'},
            )
            assert resp.status == 401

    @pytest.mark.asyncio
    async def test_missing_user_id_returns_400(self, monkeypatch):
        """Missing user_id body → 400"""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)

        class MockService:
            async def admin_resend_activation(self, *a, **kw):
                return {'success': True, 'email': 'x@x.com'}

        app = self._build_app_with_handler(MockService())
        token = _make_jwt({'user_id': 'admin-id', 'org_id': 'org-456', 'role': 'admin'})
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'access_token': token})
            resp = await client.post(
                '/api/admin/resend-activation',
                json={},  # no user_id
            )
            assert resp.status == 400
            data = await resp.json()
            assert 'user_id' in data['error']

    @pytest.mark.asyncio
    async def test_permission_error_returns_403(self, monkeypatch):
        """PermissionError from service → 403"""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)

        class MockService:
            async def admin_resend_activation(self, *a, **kw):
                raise PermissionError("Only admins can resend activation")

        app = self._build_app_with_handler(MockService())
        token = _make_jwt({'user_id': 'admin-id', 'org_id': 'org-456', 'role': 'admin'})
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'access_token': token})
            resp = await client.post(
                '/api/admin/resend-activation',
                json={'user_id': 'target-uid'},
            )
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_lookup_error_returns_404(self, monkeypatch):
        """LookupError from service (user not found) → 404"""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)

        class MockService:
            async def admin_resend_activation(self, *a, **kw):
                raise LookupError("User not found in organization")

        app = self._build_app_with_handler(MockService())
        token = _make_jwt({'user_id': 'admin-id', 'org_id': 'org-456', 'role': 'admin'})
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'access_token': token})
            resp = await client.post(
                '/api/admin/resend-activation',
                json={'user_id': 'unknown-uid'},
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_value_error_returns_400(self, monkeypatch):
        """ValueError from service (already activated/deactivated) → 400"""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)

        class MockService:
            async def admin_resend_activation(self, *a, **kw):
                raise ValueError("User account is already activated")

        app = self._build_app_with_handler(MockService())
        token = _make_jwt({'user_id': 'admin-id', 'org_id': 'org-456', 'role': 'admin'})
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'access_token': token})
            resp = await client.post(
                '/api/admin/resend-activation',
                json={'user_id': 'target-uid'},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_success_returns_200(self, monkeypatch):
        """Success → 200 with success=True"""
        monkeypatch.setenv('JWT_SECRET', JWT_SECRET)
        monkeypatch.delenv('NLWEB_DEV_AUTH_BYPASS', raising=False)

        class MockService:
            async def admin_resend_activation(self, *a, **kw):
                return {'success': True, 'email': 'member@e.com'}

        app = self._build_app_with_handler(MockService())
        token = _make_jwt({'user_id': 'admin-id', 'org_id': 'org-456', 'role': 'admin'})
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({'access_token': token})
            resp = await client.post(
                '/api/admin/resend-activation',
                json={'user_id': 'target-uid'},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data['success'] is True
