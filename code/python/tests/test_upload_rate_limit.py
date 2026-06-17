"""
Tests for webserver/middleware/upload_rate_limit.py — upload_rate_limit_middleware.

Enforces config/user_data.yaml `security.max_uploads_per_hour` (default 20) as a
per-USER sliding window on POST /api/user/upload. Runs AFTER auth_middleware, so
request['user'] is already populated (decision log docs/decisions.md:175-178:
authenticated users keyed by user_id, not IP).

Behaviors covered:
- Same user: uploads up to the limit pass; the (limit+1)th in-window -> 429.
- Different users are counted independently.
- Non-upload paths are never rate-limited.
- After the window expires, the user can upload again.
- 429 payload/headers match the existing rate_limit.py contract
  (type=rate_limit_exceeded + Retry-After header).

Uses aiohttp.test_utils.TestClient + TestServer (matches test_auth_middleware.py).
The middleware's window store and config reader are reset/overridden per test so
tests are deterministic and don't depend on real config-file values or wall-clock.
"""

import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import webserver.middleware.upload_rate_limit as url_mod
from webserver.middleware.upload_rate_limit import upload_rate_limit_middleware

UPLOAD_PATH = '/api/user/upload'


# ── Helpers ──────────────────────────────────────────────────────


def _set_user(user_id):
    """Build a middleware that injects a fake authenticated user, then runs the
    upload rate limiter — simulating auth_middleware having run first."""

    @web.middleware
    async def fake_auth(request, handler):
        if user_id is not None:
            request['user'] = {'id': user_id, 'authenticated': True}
        return await handler(request)

    return fake_auth


def _build_app(user_id='user-A'):
    """App with fake-auth (sets request['user']) THEN the upload rate limiter."""
    app = web.Application(middlewares=[_set_user(user_id), upload_rate_limit_middleware])

    async def upload(request):
        return web.json_response({'ok': True})

    async def other(request):
        return web.json_response({'ok': True, 'path': 'other'})

    app.router.add_post(UPLOAD_PATH, upload)
    app.router.add_post('/api/user/sources', other)
    return app


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset the sliding-window store and pin the limit to 20 / 3600s so tests
    don't depend on the on-disk config value."""
    url_mod._windows.clear()
    monkeypatch.setattr(url_mod, '_get_upload_limit', lambda: (20, 3600))
    yield
    url_mod._windows.clear()


# ── Tests ────────────────────────────────────────────────────────


class TestPerUserLimit:

    @pytest.mark.asyncio
    async def test_uploads_up_to_limit_pass(self):
        """First 20 uploads from the same user all succeed."""
        async with TestClient(TestServer(_build_app('user-A'))) as client:
            for i in range(20):
                resp = await client.post(UPLOAD_PATH)
                assert resp.status == 200, f"upload #{i + 1} should pass"

    @pytest.mark.asyncio
    async def test_21st_upload_returns_429(self):
        """The 21st upload in the window is blocked with 429."""
        async with TestClient(TestServer(_build_app('user-A'))) as client:
            for _ in range(20):
                resp = await client.post(UPLOAD_PATH)
                assert resp.status == 200
            resp = await client.post(UPLOAD_PATH)
            assert resp.status == 429
            data = await resp.json()
            assert data['type'] == 'rate_limit_exceeded'
            assert 'Limit: 20 per hour' in data['error']
            assert 'Retry-After' in resp.headers


class TestPerUserIsolation:

    @pytest.mark.asyncio
    async def test_different_users_counted_independently(self):
        """user-A maxing out does not affect user-B."""
        # user-A maxes out
        async with TestClient(TestServer(_build_app('user-A'))) as client_a:
            for _ in range(20):
                assert (await client_a.post(UPLOAD_PATH)).status == 200
            assert (await client_a.post(UPLOAD_PATH)).status == 429

        # user-B still has a full quota
        async with TestClient(TestServer(_build_app('user-B'))) as client_b:
            resp = await client_b.post(UPLOAD_PATH)
            assert resp.status == 200


class TestNonUploadPaths:

    @pytest.mark.asyncio
    async def test_other_path_not_rate_limited(self):
        """A non-upload path is never throttled, even past the upload limit."""
        async with TestClient(TestServer(_build_app('user-A'))) as client:
            for _ in range(25):
                resp = await client.post('/api/user/sources')
                assert resp.status == 200


class TestWindowExpiry:

    @pytest.mark.asyncio
    async def test_recovers_after_window_expires(self, monkeypatch):
        """Once the sliding window elapses, the user can upload again."""
        clock = {'t': 1000.0}
        monkeypatch.setattr(url_mod.time, 'monotonic', lambda: clock['t'])

        async with TestClient(TestServer(_build_app('user-A'))) as client:
            for _ in range(20):
                assert (await client.post(UPLOAD_PATH)).status == 200
            # blocked at the limit
            assert (await client.post(UPLOAD_PATH)).status == 429

            # advance past the 3600s window -> old timestamps evicted
            clock['t'] += 3601
            assert (await client.post(UPLOAD_PATH)).status == 200
