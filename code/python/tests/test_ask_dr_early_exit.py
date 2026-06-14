"""#2 deploy-env-hardening: /ask 同步路徑遇 deep_research 必須早退 400,
不可同步跑完整 DR（會在 prod 被 Cloudflare 100s 砍 524 + 背景續燒 LLM）。
本地無 CF，無法驗 524 本身;此處驗「早退邏輯」— prod 驗收見 plan §部署/驗收。
"""
import pytest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from webserver.routes.api import handle_regular_ask


@pytest.mark.asyncio
async def test_regular_ask_deep_research_returns_400():
    """deep_research + 非 streaming → 400,且不得呼叫 runQuery（不燒 LLM）。"""
    req = make_mocked_request("GET", "/ask?streaming=false&generate_mode=deep_research")
    query_params = {"query": "測試", "generate_mode": "deep_research"}

    # 若早退正確,DeepResearchHandler 根本不該被建構/呼叫
    with patch("methods.deep_research.DeepResearchHandler") as MockDR:
        resp = await handle_regular_ask(req, query_params)

    assert isinstance(resp, web.Response)
    assert resp.status == 400
    body = resp.text
    assert "streaming" in body  # 訊息引導改用 streaming endpoint
    MockDR.assert_not_called()  # 關鍵:不可同步跑 DR


@pytest.mark.asyncio
async def test_regular_ask_generate_mode_none_not_blocked():
    """regression: generate_mode=none（一般查詢）不可被誤擋,仍走 NLWebHandler。"""
    req = make_mocked_request("GET", "/ask?streaming=false")
    query_params = {"query": "測試", "generate_mode": "none"}

    with patch("core.baseHandler.NLWebHandler") as MockHandler:
        instance = MockHandler.return_value
        instance.runQuery = AsyncMock(return_value={"message_type": "result", "items": []})
        resp = await handle_regular_ask(req, query_params)

    assert isinstance(resp, web.Response)
    assert resp.status == 200  # 未被早退
    instance.runQuery.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_interval_is_10s():
    """#1-C: keepalive 間隔必須是 10s（QUIC RTO 抵抗力）。patch sleep 攔截參數。"""
    from unittest.mock import AsyncMock, MagicMock
    from aiohttp.test_utils import make_mocked_request
    from webserver.aiohttp_streaming_wrapper import AioHttpStreamingWrapper

    req = make_mocked_request("GET", "/ask")
    resp = MagicMock()
    resp.write = AsyncMock()
    wrapper = AioHttpStreamingWrapper(req, resp, {})

    captured = []

    async def fake_sleep(secs):
        captured.append(secs)
        wrapper.connection_alive = False  # 跑一圈就停

    with patch("webserver.aiohttp_streaming_wrapper.asyncio.sleep", side_effect=fake_sleep):
        await wrapper.start_heartbeat()

    assert captured and captured[0] == 10


@pytest.mark.asyncio
async def test_pg_pool_conninfo_parses_and_has_app_timeouts(monkeypatch):
    """#5: app 的 PG pool conninfo 必須 (1) 被 libpq 合法 parse(防 Gemini #1 缺引號
    crash) (2) 帶 app-only statement_timeout=30s + idle_in_transaction=60s。
    **關鍵:用 psycopg 真 parse,不可只用字串 `in`** —— 字串 `in` 會放行
    `options=-c statement_timeout=30s`(無引號)這種會讓 libpq crash 的字串
    (外部 review Gemini #4:測試與錯誤共謀的迴音室)。
    """
    from unittest.mock import MagicMock, AsyncMock, patch
    from psycopg.conninfo import conninfo_to_dict
    from retrieval_providers.postgres_client import PgVectorClient

    client = PgVectorClient.__new__(PgVectorClient)  # 不跑 __init__
    client._pool = None
    import asyncio as _asyncio
    client._pool_init_lock = _asyncio.Lock()
    client.host = "h"; client.port = 5432; client.dbname = "nlweb"
    client.username = "nlweb"; client.password = "pw"

    captured = {}

    class FakePool:
        def __init__(self, conninfo=None, **kw):
            captured["conninfo"] = conninfo
        async def open(self): ...
        def connection(self):  # async context manager
            cm = MagicMock()
            conn = MagicMock()
            cur = MagicMock()
            cur.execute = AsyncMock(); cur.fetchone = AsyncMock(return_value=(1,))
            conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
            conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
            cm.__aenter__ = AsyncMock(return_value=conn)
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

    with patch("retrieval_providers.postgres_client.AsyncConnectionPool", FakePool), \
         patch("retrieval_providers.postgres_client.pgvector.psycopg.register_vector_async", AsyncMock()):
        await client._get_connection_pool()

    conninfo = captured["conninfo"]

    # (1) 必須能被 libpq 合法 parse —— 缺引號會在這裡 raise ProgrammingError
    parsed = conninfo_to_dict(conninfo)  # 若缺引號 → ProgrammingError,test 直接紅燈

    # (2) options 內含兩個 app-only timeout(連線層級,非 server 全域)
    opts = parsed.get("options", "")
    assert "statement_timeout=30s" in opts, f"缺 statement_timeout: {opts!r}"
    assert "idle_in_transaction_session_timeout=60s" in opts, f"缺 idle_in_transaction: {opts!r}"
