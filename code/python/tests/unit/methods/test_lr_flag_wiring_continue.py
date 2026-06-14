"""LR 前端接線 unit 測試：驗 handler 在 continue / initial request 雙路皆正確抽取兩個 flag。

注意：本測試含兩層 —
1. handler-parsing regression-lock（4 個）：驗 Python handler 端抽取行為（已有實作）。
2. 真 route-level body regression（1 個）：呼叫 continue route handler 本體，
   驗 api.py 確實把 JSON body merge 進 query_params 後建 handler。
   防兩層 regression：(1) 前端 body 漏 flag (2) route 層停止 merge body。

前端 JS 的 body 結構變更屬 integration 層，真機驗收在 Task 6 真 BAB / prod manual gate。
"""
import os
import sys

from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


@pytest.fixture
def http_handler():
    h = MagicMock()
    h.write_stream = AsyncMock()
    return h


def test_handler_enable_gap_enrichment_true_on_initial(http_handler):
    """Initial request body 帶 enable_gap_enrichment=True → handler.enable_gap_enrichment is True。"""
    from methods.live_research import LiveResearchHandler
    qp = {'query': 'x', 'dry_run': 'true', 'enable_gap_enrichment': True, 'enable_web_search': True}
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_gap_enrichment is True


def test_handler_enable_gap_enrichment_true_on_continue(http_handler):
    """Continue request body 帶 enable_gap_enrichment=True → handler.enable_gap_enrichment is True。

    Continue request 由 routes/api.py 建立新 handler，傳 body 作 query_params。
    此測試驗此路徑正確抽取（實作已存在，測試是 spec 文件化）。
    """
    from methods.live_research import LiveResearchHandler
    # Simulate continue request body：兩個 flag 皆帶
    qp = {
        'query': '',  # continue 可能無 query
        'dry_run': 'true',
        'enable_gap_enrichment': True,
        'enable_web_search': True,
    }
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_gap_enrichment is True
    assert h.enable_web_search is True


def test_handler_enable_gap_enrichment_default_false(http_handler):
    """Backward-compat: 不帶 enable_gap_enrichment → False (safe default)。"""
    from methods.live_research import LiveResearchHandler
    qp = {'query': 'x', 'dry_run': 'true'}
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_gap_enrichment is False


def test_handler_enable_web_search_true_on_continue_body(http_handler):
    """Continue request body 帶 enable_web_search=True → handler.enable_web_search is True。

    修前：continueLiveResearch body 不帶此 flag → handler.enable_web_search = False。
    修後：body 帶 enable_web_search: true → handler.enable_web_search = True。
    """
    from methods.live_research import LiveResearchHandler
    qp = {
        'query': '',
        'dry_run': 'true',
        'enable_web_search': True,
        'enable_gap_enrichment': True,
    }
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_web_search is True


@pytest.mark.asyncio
async def test_continue_route_body_flags_reach_handler(monkeypatch):
    """真 route-level regression：呼叫 continue route handler 本體，
    驗 api.py 確實把 JSON body merge 進 query_params 後建 handler。
    防兩層 regression：(1) 前端 body 漏 flag (2) route 層停止 merge body。

    注意：continue route 以 `from methods.live_research import LiveResearchHandler`
    lazy import（api.py:1451），故 patch target 為 methods.live_research.LiveResearchHandler，
    非 api 模組屬性。GUARDRAIL_DR_ENABLED=false 跳過 concurrency limiter；
    feature flag live_research 須為 True 否則 route 提前回 503。
    """
    import methods.live_research as lr_mod
    import webserver.routes.api as api_mod  # noqa: F401  (確保 route 已 import)
    from core.config import CONFIG
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv('GUARDRAIL_DR_ENABLED', 'false')
    features = CONFIG.reasoning_params.setdefault('features', {})
    monkeypatch.setitem(features, 'live_research', True)

    captured = {}

    class _CaptureHandler:
        connection_alive_event = MagicMock()
        _lr_research_task = None

        def __init__(self, query_params, http_handler):
            captured.update(query_params or {})

        async def continueResearch(self, **kwargs):
            return None

    monkeypatch.setattr(lr_mod, 'LiveResearchHandler', _CaptureHandler)

    body = {
        'session_id': 'frontend-sid',
        'lr_session_id': 'uuid-x',
        'user_message': '',
        'auto_continue': False,
        'enable_web_search': True,
        'enable_gap_enrichment': True,
    }
    request = make_mocked_request('POST', '/api/live_research/continue')
    monkeypatch.setattr(request, 'json', AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    assert captured.get('enable_web_search') is True
    assert captured.get('enable_gap_enrichment') is True
