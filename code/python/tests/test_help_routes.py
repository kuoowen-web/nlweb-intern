"""
Tests for webserver/routes/help.py — feedback routes only.
Uses real SQLite (no mocks for DB). Fresh DB per test via tmp_path.
"""
import os
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web

os.environ['JWT_SECRET'] = 'test-secret-for-help-routes'

from auth.auth_db import AuthDB

# Force SQLite mode: pop AFTER imports (load_dotenv in logger.py re-sets them)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Fresh SQLite DB for every test."""
    AuthDB._instance = None
    db = AuthDB(db_path=str(tmp_path / "test.db"))
    AuthDB._instance = db
    db._init_database_sync()
    db._initialized = True
    yield
    AuthDB._instance = None


@pytest_asyncio.fixture
async def client(aiohttp_client):
    from webserver.routes.help import setup_help_routes
    from webserver.middleware.auth import auth_middleware
    app = web.Application(middlewares=[auth_middleware])
    setup_help_routes(app)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_post_feedback_success(client):
    payload = {
        'category': 'bug',
        'rating': 4,
        'content': '這是一個測試意見，長度超過十個字元。',
        'email': 'test@example.com',
    }
    resp = await client.post('/api/help/feedback', json=payload)
    assert resp.status == 201
    data = await resp.json()
    assert data['success'] is True
    assert 'id' in data


@pytest.mark.asyncio
async def test_post_feedback_missing_required(client):
    # Missing content
    resp = await client.post('/api/help/feedback', json={'category': 'bug', 'rating': 3})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_post_feedback_invalid_rating(client):
    payload = {'category': 'bug', 'rating': 6, 'content': '測試內容長度足夠十個字元以上'}
    resp = await client.post('/api/help/feedback', json=payload)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_post_feedback_content_too_short(client):
    payload = {'category': 'bug', 'rating': 3, 'content': '太短'}
    resp = await client.post('/api/help/feedback', json=payload)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_post_feedback_content_too_long(client):
    """POST /api/feedback with content over 500 chars should return 400."""
    long_content = 'A' * 501
    payload = {'category': 'bug', 'rating': 3, 'content': long_content}
    resp = await client.post('/api/help/feedback', json=payload)
    assert resp.status == 400
