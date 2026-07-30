"""
Tests for webserver/routes/early_bird.py — landing 早鳥表單。
Real SQLite per test（模式同 test_help_routes.py）。
early_bird_signups 不在 auth_db legacy schema dict（DO NOT add 政策），
fixture 自建表——DDL 與 alembic migration SQLite 分支字面一致。
"""
import os
import re
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import web

os.environ['JWT_SECRET'] = 'test-secret-for-early-bird'

from auth.auth_db import AuthDB

os.environ.pop('POSTGRES_CONNECTION_STRING', None)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)

EARLY_BIRD_DDL = """
    CREATE TABLE IF NOT EXISTS early_bird_signups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        company TEXT,
        job_title TEXT,
        purpose TEXT,
        client_ip TEXT,
        created_at REAL NOT NULL
    )
"""


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    AuthDB._instance = None
    db = AuthDB(db_path=str(tmp_path / "test.db"))
    AuthDB._instance = db
    db._init_database_sync()
    conn = db._sqlite_connect()
    conn.cursor().execute(EARLY_BIRD_DDL)
    conn.commit()
    db._initialized = True
    yield
    AuthDB._instance = None


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """rate limit 計數是 module-level dict，逐測試清空。"""
    from webserver.middleware.rate_limit import _windows
    _windows.clear()
    yield
    _windows.clear()


@pytest_asyncio.fixture
async def client(aiohttp_client):
    from webserver.routes.early_bird import setup_early_bird_routes
    from webserver.middleware.auth import auth_middleware
    from webserver.middleware.rate_limit import rate_limit_middleware
    app = web.Application(middlewares=[rate_limit_middleware, auth_middleware])
    setup_early_bird_routes(app)
    return await aiohttp_client(app)


VALID = {
    'name': '李測試',
    'email': 'lead@example.com',
    'company': '測試股份有限公司',
    'job_title': '經理',
    'purpose': '研究報告資料統整用。',
}


async def _count_rows():
    db = AuthDB.get_instance()
    row = await db.fetchone("SELECT COUNT(*) AS c FROM early_bird_signups", ())
    return row['c']


@pytest.mark.asyncio
async def test_signup_success(client):
    resp = await client.post('/api/early-bird', json=VALID)
    assert resp.status == 201
    data = await resp.json()
    assert data['success'] is True
    assert 'id' in data
    assert await _count_rows() == 1


@pytest.mark.asyncio
async def test_signup_optional_fields_empty(client):
    resp = await client.post('/api/early-bird', json={'name': '李測試', 'email': 'a@b.tw'})
    assert resp.status == 201


@pytest.mark.asyncio
async def test_signup_missing_name(client):
    payload = dict(VALID); payload['name'] = ''
    resp = await client.post('/api/early-bird', json=payload)
    assert resp.status == 400
    assert 'error' in await resp.json()


@pytest.mark.asyncio
async def test_signup_bad_email(client):
    for bad in ['not-an-email', 'a@b', 'a b@c.tw', '']:
        payload = dict(VALID); payload['email'] = bad
        resp = await client.post('/api/early-bird', json=payload)
        assert resp.status == 400, f"email={bad!r} 應 400"


@pytest.mark.asyncio
async def test_signup_field_too_long(client):
    payload = dict(VALID); payload['purpose'] = 'x' * 501
    resp = await client.post('/api/early-bird', json=payload)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_signup_invalid_json(client):
    resp = await client.post('/api/early-bird', data='not json',
                             headers={'Content-Type': 'application/json'})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_signup_non_dict_json(client):
    """JSON 合法但非 object（array/str/number/null）應回 400 而非 500——
    body.get() 會對非 dict AttributeError；public endpoint 不可 5xx。"""
    for raw in ['[]', '"x"', '123', 'null']:
        resp = await client.post('/api/early-bird', data=raw,
                                 headers={'Content-Type': 'application/json'})
        assert resp.status == 400, f"payload={raw!r} 應 400，實得 {resp.status}"
        assert 'error' in await resp.json(), f"payload={raw!r} body 應含 error"


@pytest.mark.asyncio
async def test_honeypot_silently_dropped(client):
    payload = dict(VALID); payload['website'] = 'http://spam.example'
    resp = await client.post('/api/early-bird', json=payload)
    assert resp.status == 201          # 對 bot 裝成功
    assert await _count_rows() == 0    # 但不落庫


@pytest.mark.asyncio
async def test_rate_limit_5_per_hour(client):
    for i in range(5):
        resp = await client.post('/api/early-bird', json=VALID)
        assert resp.status == 201, f"第 {i+1} 次應成功"
    resp = await client.post('/api/early-bird', json=VALID)
    assert resp.status == 429
    data = await resp.json()
    assert data['type'] == 'rate_limit_exceeded'


@pytest.mark.asyncio
async def test_notification_called_and_failure_tolerated(client, monkeypatch):
    """通知走 awaited + timeout 語義（Task 7.3）：handler await 到通知完成/失敗才回應，
    monkeypatch 的呼叫紀錄與例外都直接測得到，無需背景 task drain。"""
    calls = []
    import webserver.routes.early_bird as eb
    monkeypatch.setattr(eb, 'send_early_bird_notification',
                        lambda *a, **kw: calls.append(a))
    resp = await client.post('/api/early-bird', json=VALID)
    assert resp.status == 201
    assert len(calls) == 1

    def boom(*a, **kw):
        raise RuntimeError('resend down')
    monkeypatch.setattr(eb, 'send_early_bird_notification', boom)
    resp = await client.post('/api/early-bird', json=VALID)
    assert resp.status == 201          # 通知失敗不影響落庫與回應
    assert await _count_rows() == 2


@pytest.mark.asyncio
async def test_notification_timeout_tolerated(client, monkeypatch):
    """S1 timeout 分支專測（R2-S2）：通知函數卡超過 NOTIFY_TIMEOUT_SECONDS 時，
    handler 應在逾時後照常回 201 且落庫成立——核心防護「宣稱有」必須被釘住。
    做法：timeout 常數降到 0.1s + 通知換成 sleep(0.3) 的同步函數（跑在 executor
    thread，wait_for 逾時取消 future、thread 自行跑完收尾，回應照常返回）。
    反偽關鍵：夾出 POST 的 elapsed 並斷言 < 0.3s——證明 handler 沒等通知 sleep(0.3)
    跑完就返回。少了這條 elapsed 斷言，即使把 asyncio.wait_for 整個移除、handler
    等滿 0.3s，本測試仍會綠（假綠縫）。"""
    import time as time_mod
    import webserver.routes.early_bird as eb
    monkeypatch.setattr(eb, 'NOTIFY_TIMEOUT_SECONDS', 0.1)
    monkeypatch.setattr(eb, 'send_early_bird_notification',
                        lambda *a, **kw: time_mod.sleep(0.3))
    start = time_mod.monotonic()
    resp = await client.post('/api/early-bird', json=VALID)
    elapsed = time_mod.monotonic() - start
    assert resp.status == 201          # 通知逾時不影響落庫與回應
    assert await _count_rows() == 1
    # timeout 0.1s + 充分餘裕，仍嚴格小於通知 sleep 0.3s——handler 未等通知跑完就返回
    assert elapsed < 0.3, f"handler 等了 {elapsed:.3f}s，未在 timeout 生效後返回"


def test_fixture_ddl_matches_migration():
    """S5 防漂移：本檔 EARLY_BIRD_DDL 與 alembic migration SQLite 分支的
    欄位名集合比對（normalized）——改表結構漏同步任一處即紅，不靠人記。"""
    versions_dir = Path(__file__).resolve().parents[1] / 'alembic' / 'versions'
    hits = list(versions_dir.glob('*_add_early_bird_signups.py'))
    assert len(hits) == 1, f"應恰有一個 early_bird migration，找到：{[h.name for h in hits]}"
    text = hits[0].read_text(encoding='utf-8')
    # 收尾錨點=「換行+縮排+單獨 `)`」（R2-S3）：不依賴 `"""` 收尾，未來欄位
    # 型別含 `)` 變體（如 NUMERIC(10)）也不會斷——中間行都以逗號/型別結尾，
    # 首個「行首只有 `)`」必為 CREATE TABLE 閉括號。已對兩分支 DDL round-trip 實測。
    bodies = re.findall(
        r'CREATE TABLE IF NOT EXISTS early_bird_signups\s*\((.*?)\n\s*\)',
        text, re.DOTALL)
    assert len(bodies) == 2, "migration 應含 PG + SQLite 兩個 CREATE TABLE 分支"
    sqlite_bodies = [b for b in bodies if 'AUTOINCREMENT' in b]
    assert len(sqlite_bodies) == 1, "SQLite 分支（含 AUTOINCREMENT）應恰一個"

    def col_names(body: str) -> set:
        return {ln.strip().split()[0].lower()
                for ln in body.strip().splitlines() if ln.strip()}

    fixture_body = EARLY_BIRD_DDL.split('(', 1)[1].rsplit(')', 1)[0]
    assert col_names(fixture_body) == col_names(sqlite_bodies[0]), (
        "fixture EARLY_BIRD_DDL 與 migration SQLite 分支欄位名集合漂移——兩處需同步")
