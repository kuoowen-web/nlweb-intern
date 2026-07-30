"""
Tests for landing 路由切換：/ → landing、/app → 產品頁、兩者皆 public。
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

os.environ['JWT_SECRET'] = 'test-secret-for-landing-routes'

STATIC_DIR = Path(__file__).resolve().parents[3] / 'static'


@pytest_asyncio.fixture
async def client(aiohttp_client):
    from webserver.routes.static import setup_static_routes
    from webserver.middleware.auth import auth_middleware
    app = web.Application(middlewares=[auth_middleware])
    app['config'] = {'static_directory': str(STATIC_DIR)}
    setup_static_routes(app)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_root_serves_landing(client):
    resp = await client.get('/')
    assert resp.status == 200
    text = await resp.text()
    assert '把 AI 給不了的信任' in text          # landing H1
    assert 'news-search' not in text.split('<title>')[1][:60]


@pytest.mark.asyncio
async def test_app_serves_product(client):
    resp = await client.get('/app')
    assert resp.status == 200
    text = await resp.text()
    assert 'news-search-prototype' in text or '新聞搜尋' in text


@pytest.mark.asyncio
async def test_app_is_public_no_auth_required(client):
    """未帶任何 auth header/cookie 打 /app 必須 200（登入牆是前端 modal）。"""
    resp = await client.get('/app', headers={})
    assert resp.status == 200


@pytest.mark.asyncio
async def test_landing_missing_falls_back_to_app(client, monkeypatch, tmp_path):
    """landing/index.html 不存在時 / fallback 到產品頁（防 prod 開天窗）。"""
    from webserver.routes import static as static_mod
    fake_static = tmp_path / 'static'
    (fake_static / 'landing').mkdir(parents=True)
    # 只放產品頁、不放 landing
    (fake_static / 'news-search-prototype.html').write_text('<html>app</html>', encoding='utf-8')
    app = web.Application()
    app['static_path'] = fake_static
    from aiohttp.test_utils import make_mocked_request
    req = make_mocked_request('GET', '/', app=app)
    resp = await static_mod.landing_handler(req)
    assert resp.status == 200
