"""LR analytics route 打點（票 2026-07-28-k）：兩條 LR route 預生成 query_id +
同步 log_query_start(mode='live_research')，對稱 DR pattern（api.py:840-857）。"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


class _FakeWrapper:
    def __init__(self, *a, **k):
        self.connection_alive = True

    async def prepare_response(self):
        pass

    async def write_stream(self, *a, **k):
        pass

    async def finish_response(self):
        pass

    def set_on_disconnect(self, cb):
        pass


def _fake_handler_cls(created: dict, method_name: str):
    """建 FakeHandler class：記錄 instance 供斷言；runQuery/continueResearch 立即回 checkpoint。"""

    class _FakeHandler:
        connection_alive_event = MagicMock()
        _lr_research_task = None
        user_id = "user-uuid-1"
        org_id = "org-uuid-1"
        session_id = "sess_frontend"
        site = "all"

        def __init__(self, *a, **k):
            created["handler"] = self

    async def _done(self, **kwargs):
        return {"status": "checkpoint"}

    setattr(_FakeHandler, method_name, _done)
    return _FakeHandler


def _patch_route_env(monkeypatch, created, method_name):
    import methods.live_research as lr_mod
    import webserver.routes.api as api_mod
    from core.config import CONFIG

    monkeypatch.setenv("GUARDRAIL_DR_ENABLED", "false")
    features = CONFIG.reasoning_params.setdefault("features", {})
    monkeypatch.setitem(features, "live_research", True)

    ql = MagicMock()
    monkeypatch.setattr(api_mod, "AioHttpStreamingWrapper", _FakeWrapper)
    monkeypatch.setattr(lr_mod, "LiveResearchHandler", _fake_handler_cls(created, method_name))
    monkeypatch.setattr("core.query_logger.get_query_logger", lambda: ql)
    return api_mod, ql


@pytest.mark.asyncio
async def test_start_route_pre_registers_query_id_and_logs_start(monkeypatch):
    """start route：handler.query_id 預生成（query_ 前綴）+ log_query_start 同步打點
    mode='live_research'、user/org/session 從 handler 取、start_time 記在 handler。"""
    from aiohttp.test_utils import make_mocked_request

    created = {}
    api_mod, ql = _patch_route_env(monkeypatch, created, "runQuery")

    body = {"query": "台灣綠能發展衝突", "session_id": "sess_frontend"}
    request = make_mocked_request("POST", "/api/live_research")
    monkeypatch.setattr(request, "json", AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_start_handler(request)

    h = created["handler"]
    assert getattr(h, "query_id", "").startswith("query_")
    assert hasattr(h, "_lr_analytics_query_start_time")

    ql.log_query_start.assert_called_once()
    kwargs = ql.log_query_start.call_args.kwargs
    assert kwargs["query_id"] == h.query_id
    assert kwargs["mode"] == "live_research"
    assert kwargs["query_text"] == "台灣綠能發展衝突"
    assert kwargs["user_id"] == "user-uuid-1"
    assert kwargs["org_id"] == "org-uuid-1"
    assert kwargs["session_id"] == "sess_frontend"
    # initial 打點時 lr_session_id 尚未生成 → conversation_id 不帶（Task 3 回填）
    assert "conversation_id" not in kwargs or kwargs["conversation_id"] == ""


@pytest.mark.asyncio
async def test_start_route_query_logger_failure_is_non_fatal(monkeypatch):
    """log_query_start 拋例外 → route 不炸（non-fatal warning），runQuery 照跑。"""
    from aiohttp.test_utils import make_mocked_request

    created = {}
    api_mod, ql = _patch_route_env(monkeypatch, created, "runQuery")
    ql.log_query_start.side_effect = RuntimeError("db down")

    body = {"query": "Q", "session_id": "s"}
    request = make_mocked_request("POST", "/api/live_research")
    monkeypatch.setattr(request, "json", AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_start_handler(request)  # 不應 raise
    assert "handler" in created  # handler 有建、流程有走到
    # AR R1：start 失敗不殘留 query_id——下游 hasattr gate 全關、不產生 FK 孤兒噪音
    assert not hasattr(created["handler"], "query_id")


@pytest.mark.asyncio
async def test_continue_route_logs_start_with_lr_session_as_conversation_id(monkeypatch):
    """continue route：每次 invocation 各生成一個 query_id；conversation_id = body 的
    lr_session_id（後端權威 UUID，雙 PG row 陷阱紅線）；query_text = user_message。"""
    from aiohttp.test_utils import make_mocked_request

    created = {}
    api_mod, ql = _patch_route_env(monkeypatch, created, "continueResearch")

    body = {
        "session_id": "sess_frontend",
        "lr_session_id": "11111111-2222-3333-4444-555555555555",
        "user_message": "第二章多補國外案例",
        "auto_continue": False,
    }
    request = make_mocked_request("POST", "/api/live_research/continue")
    monkeypatch.setattr(request, "json", AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    h = created["handler"]
    assert getattr(h, "query_id", "").startswith("query_")
    kwargs = ql.log_query_start.call_args.kwargs
    assert kwargs["mode"] == "live_research"
    assert kwargs["conversation_id"] == "11111111-2222-3333-4444-555555555555"
    assert kwargs["query_text"] == "第二章多補國外案例"
    assert kwargs["session_id"] == "sess_frontend"


@pytest.mark.asyncio
async def test_continue_route_truncates_long_user_message(monkeypatch):
    """user_message 長文（含使用者粘貼素材）→ query_text 截 500 字（CEO 拍板 OQ4）。"""
    from aiohttp.test_utils import make_mocked_request

    created = {}
    api_mod, ql = _patch_route_env(monkeypatch, created, "continueResearch")

    body = {
        "session_id": "s",
        "lr_session_id": "uuid-x",
        "user_message": "長" * 800,
        "auto_continue": False,
    }
    request = make_mocked_request("POST", "/api/live_research/continue")
    monkeypatch.setattr(request, "json", AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    kwargs = ql.log_query_start.call_args.kwargs
    assert len(kwargs["query_text"]) == 500


@pytest.mark.asyncio
async def test_continue_route_empty_message_uses_placeholder_query_text(monkeypatch):
    """auto_continue 空訊息 → query_text 用 '[LR continue] auto' placeholder（NOT NULL 欄位 + 可讀性）。"""
    from aiohttp.test_utils import make_mocked_request

    created = {}
    api_mod, ql = _patch_route_env(monkeypatch, created, "continueResearch")

    body = {
        "session_id": "s",
        "lr_session_id": "uuid-x",
        "user_message": "",
        "auto_continue": True,
    }
    request = make_mocked_request("POST", "/api/live_research/continue")
    monkeypatch.setattr(request, "json", AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    kwargs = ql.log_query_start.call_args.kwargs
    assert kwargs["query_text"] == "[LR continue] auto"
