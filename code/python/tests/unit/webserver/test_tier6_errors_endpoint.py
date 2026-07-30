"""GET /api/analytics/tier6_errors 聚合 endpoint（票 2026-07-28 #9）。

daily-patrol 雲端巡檢（無 DB 憑證）用的輕量 read endpoint：
查 tier_6_enrichment 近 N 天 rows，Python 層聚合 metadata.error_type
（7a44c6a4 三分類：timeout / http_<status> / 無 error_type）。

Run:
    pytest tests/unit/webserver/test_tier6_errors_endpoint.py -v
"""
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def _row(source_type, metadata):
    """模擬 AnalyticsDB.fetchall 回傳 row（dict；metadata 是 TEXT JSON 字串或 None）。"""
    return {"source_type": source_type, "metadata": metadata}


def _make_handler(rows):
    """建 AnalyticsHandler，AnalyticsDB singleton 換成 mock（fetchall 回 rows）。"""
    from webserver import analytics_handler as ah_mod

    db = MagicMock()
    db.db_type = "sqlite"
    db.fetchall = AsyncMock(return_value=rows)
    with patch.object(ah_mod.AnalyticsDB, "get_instance", return_value=db):
        handler = ah_mod.AnalyticsHandler()
    return handler, db


async def _call(handler, path="/api/analytics/tier6_errors"):
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request("GET", path)
    response = await handler.get_tier6_errors(request)
    return response, json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_aggregates_by_source_429_timeout_and_normal():
    """規格範例形狀：google 8 calls（4 http_429 + 1 timeout + 3 正常）、wikipedia 4 正常
    → errors 聚合 error_type 次數、timeout 進 timeouts 欄不重複進 errors、has_errors=True。"""
    rows = (
        [_row("google_search", '{"error_type": "http_429"}')] * 4
        + [_row("google_search", '{"error_type": "timeout"}')]
        + [_row("google_search", '{"domains": ["a.com"]}')] * 2
        + [_row("google_search", None)]
        + [_row("wikipedia", "{}")] * 4
    )
    handler, db = _make_handler(rows)

    response, data = await _call(handler)

    assert response.status == 200
    assert data["days"] == 1  # 預設 1
    assert data["total_calls"] == 12
    assert data["by_source"]["google_search"] == {
        "calls": 8,
        "timeouts": 1,
        "errors": {"http_429": 4},
    }
    assert data["by_source"]["wikipedia"] == {"calls": 4, "timeouts": 0, "errors": {}}
    assert data["has_errors"] is True

    # 查詢走 AnalyticsDB async API，cutoff 為近 1 天
    args = db.fetchall.call_args.args
    cutoff = args[1][0]
    assert time.time() - 86400 - 60 < cutoff < time.time() - 86400 + 60


@pytest.mark.asyncio
async def test_metadata_parse_failure_and_timeout_only_not_errors():
    """metadata 爛 JSON / 非 dict → 容錯當無 error_type 不炸；
    只有 timeout 沒有其他 error_type → has_errors=False（timeout 不算 errors）。"""
    rows = [
        _row("google_search", "not-json{{{"),
        _row("google_search", '["list-not-dict"]'),
        _row("google_search", '{"error_type": "timeout"}'),
    ]
    handler, _ = _make_handler(rows)

    response, data = await _call(handler)

    assert response.status == 200
    assert data["total_calls"] == 3
    assert data["by_source"]["google_search"] == {
        "calls": 3,
        "timeouts": 1,
        "errors": {},
    }
    assert data["has_errors"] is False


@pytest.mark.asyncio
async def test_days_clamped_to_30_and_floor_1():
    """days 超過 30 → clamp 到 30 不報錯；days=0 → clamp 到 1。"""
    handler, db = _make_handler([])

    _, data = await _call(handler, "/api/analytics/tier6_errors?days=100")
    assert data["days"] == 30
    cutoff = db.fetchall.call_args.args[1][0]
    assert time.time() - 30 * 86400 - 60 < cutoff < time.time() - 30 * 86400 + 60

    _, data = await _call(handler, "/api/analytics/tier6_errors?days=0")
    assert data["days"] == 1


@pytest.mark.asyncio
async def test_route_registered_without_admin_gate():
    """路由掛在 /api/analytics/tier6_errors，且無 @admin_only（巡檢無憑證要能打）。"""
    from aiohttp import web
    from webserver import analytics_handler as ah_mod

    db = MagicMock()
    db.db_type = "sqlite"
    db.fetchall = AsyncMock(return_value=[])
    app = web.Application()
    with patch.object(ah_mod.AnalyticsDB, "get_instance", return_value=db):
        ah_mod.register_analytics_routes(app)

    handler_fn = next(
        (
            r.handler for r in app.router.routes()
            if r.method == "GET" and r.resource.canonical == "/api/analytics/tier6_errors"
        ),
        None,
    )
    assert handler_fn is not None, "route /api/analytics/tier6_errors 未註冊"

    # 無 auth 行為斷言：無 user 的 request 直打要 200（誤掛 @admin_only 會 401）
    from aiohttp.test_utils import make_mocked_request

    response = await handler_fn(make_mocked_request("GET", "/api/analytics/tier6_errors"))
    assert response.status == 200


def test_middleware_public_get_whitelist_contains_tier6_errors():
    """auth middleware 層也要放行（GET-only）：handler 無 @admin_only（上一條）擋不住
    middleware 的 401——2026-07-29 prod 實測漏此層整條巡檢被擋。只開 GET，
    不得進全方法白名單。"""
    from webserver.middleware.auth import PUBLIC_ENDPOINTS, PUBLIC_GET_ENDPOINTS

    assert "/api/analytics/tier6_errors" in PUBLIC_GET_ENDPOINTS
    assert "/api/analytics/tier6_errors" not in PUBLIC_ENDPOINTS
