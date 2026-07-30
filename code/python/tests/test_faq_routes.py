"""
Tests for webserver/routes/help.py — GET /api/faq（公開 FAQ 讀取）。
Real SQLite per test（模式同 test_help_routes.py）。faqs 表在 _init_database_sync
建為空表；每測試自行 seed 列（單元測試不跑 alembic seed migration）。
"""
import os
import time

import pytest
import pytest_asyncio
from aiohttp import web

os.environ['JWT_SECRET'] = 'test-secret-for-faq-routes'

from auth.auth_db import AuthDB

os.environ.pop('POSTGRES_CONNECTION_STRING', None)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)


def _seed_faq(db, question, answer, category, sort_order, is_published):
    conn = db._sqlite_connect()
    now = time.time()
    conn.cursor().execute(
        "INSERT INTO faqs (question, answer, category, sort_order, is_published, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (question, answer, category, sort_order, is_published, now, now),
    )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
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
async def test_get_faq_empty(client):
    """空表：200 + faqs 為空陣列。"""
    resp = await client.get('/api/faq')
    assert resp.status == 200
    data = await resp.json()
    assert data['faqs'] == []


@pytest.mark.asyncio
async def test_get_faq_returns_published_sorted(client):
    """只回 published、按 sort_order 升序，欄位符合契約。"""
    db = AuthDB.get_instance()
    _seed_faq(db, 'Q2', 'A2', 'search', 1, 1)
    _seed_faq(db, 'Q0', 'A0', 'general', 0, 1)
    _seed_faq(db, 'Qhidden', 'Ahidden', 'other', 2, 0)  # 未上架
    resp = await client.get('/api/faq')
    assert resp.status == 200
    data = await resp.json()
    faqs = data['faqs']
    # 只回 2 個 published，按 sort_order 排：Q0(0) 在前、Q2(1) 在後
    assert [f['question'] for f in faqs] == ['Q0', 'Q2']
    # 欄位契約：只含 id/question/answer/category/sort_order，不含 is_published
    assert set(faqs[0].keys()) == {'id', 'question', 'answer', 'category', 'sort_order'}
    assert faqs[0]['category'] == 'general'


@pytest.mark.asyncio
async def test_get_faq_public_no_auth(client):
    """未帶 JWT 也 200（公開 GET）。"""
    db = AuthDB.get_instance()
    _seed_faq(db, 'Q', 'A', 'general', 0, 1)
    resp = await client.get('/api/faq')  # 無 Authorization header
    assert resp.status == 200
