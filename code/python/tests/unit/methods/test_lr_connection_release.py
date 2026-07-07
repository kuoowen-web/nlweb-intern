"""LR 連線釋放治本（plan: lr-sse-connection-release-fix, 2026-06-22）。

驗證：
- client 斷線（connection_alive_event clear / _lr_detach_event set）時，
  runQuery / continueResearch **提早 return**（不卡在 await task 到底）。
- 提早 return 時**不** cancel 背景 task（disconnect-no-cancel 保留）。
- 背景 task 的 done-callback (_on_lr_research_complete) 仍存活、仍負責 exception retrieval。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from methods.live_research import LiveResearchHandler  # noqa: E402


def _make_handler():
    qp = {"query": "Q", "dry_run": "true", "session_id": "sess-rel"}
    http_handler = MagicMock()
    h = LiveResearchHandler(qp, http_handler)
    h.final_retrieved_items = []
    h.lr_session_id = "sess-rel"
    h._save_state = AsyncMock()
    return h


@pytest.mark.asyncio
async def test_runquery_detaches_when_client_offline(monkeypatch):
    """斷線：runQuery 提早 return，背景 task 不被 cancel、繼續存在。"""
    h = _make_handler()

    # 用一個慢 orchestrator task 模擬「研究還沒跑完」
    slow_done = asyncio.Event()

    async def _slow_start(**kwargs):
        await slow_done.wait()  # 不會在斷線前完成
        return MagicMock(current_stage=1, checkpoint_prompt="cp")

    fake_orch = MagicMock()
    fake_orch.start = _slow_start
    monkeypatch.setattr(
        "methods.live_research.LiveResearchOrchestrator",
        lambda **kw: fake_orch,
    )
    # skip prepare()/session creation 的真實路徑（dry_run）
    monkeypatch.setattr(h, "prepare", AsyncMock())
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="sess-rel"))
    h.query_done = False

    run_task = asyncio.create_task(h.runQuery())
    await asyncio.sleep(0.02)  # 讓 runQuery 建好 _lr_research_task 並進入 await

    assert h._lr_research_task is not None
    bg_task = h._lr_research_task

    # 模擬斷線：clear alive + set detach event
    h.connection_alive_event.clear()
    h._lr_detach_event.set()

    # runQuery 應提早 return（不會卡在 slow_start）
    await asyncio.wait_for(run_task, timeout=1.0)

    # 背景 task 未被 cancel、仍在跑（pending）
    assert bg_task.cancelled() is False
    assert bg_task.done() is False

    # 收尾：放行背景 task 避免 warning
    slow_done.set()
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_continue_detaches_when_client_offline(monkeypatch):
    """斷線：continueResearch 提早 return，背景 task 不被 cancel。"""
    h = _make_handler()
    slow_done = asyncio.Event()

    async def _slow_continue(**kwargs):
        await slow_done.wait()
        return MagicMock(current_stage=5, checkpoint_prompt="cp", stage_status="checkpoint")

    fake_orch = MagicMock()
    fake_orch.continue_from_checkpoint = _slow_continue
    monkeypatch.setattr(
        "methods.live_research.LiveResearchOrchestrator",
        lambda **kw: fake_orch,
    )
    # _load_state 回非 None state，跳過早退分支；schema_version>=2 跳過 legacy gate
    fake_state = MagicMock()
    fake_state.schema_version = 2
    monkeypatch.setattr(h, "_load_state", AsyncMock(return_value=fake_state))
    # 跳過 legacy gate 等分支：直接讓 continueResearch 走到 task 建立
    h.query_params = {"lr_session_id": "sess-rel"}

    run_task = asyncio.create_task(
        h.continueResearch(user_message="go", auto_continue=False)
    )
    await asyncio.sleep(0.02)
    assert h._lr_research_task is not None
    bg_task = h._lr_research_task

    h.connection_alive_event.clear()
    h._lr_detach_event.set()

    await asyncio.wait_for(run_task, timeout=1.0)
    assert bg_task.cancelled() is False
    assert bg_task.done() is False

    slow_done.set()
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_runquery_detach_keeps_task_ref_non_none(monkeypatch):
    """detach 契約：runQuery 提早 return 後，handler._lr_research_task **保留**（非 None 且 pending）。
    這是 route 能掛 slot-release done-callback 的唯一前提（直接鎖契約，與 route slot 行為解耦）。"""
    h = _make_handler()
    slow_done = asyncio.Event()

    async def _slow_start(**kwargs):
        await slow_done.wait()
        return MagicMock(current_stage=1, checkpoint_prompt="cp")

    fake_orch = MagicMock()
    fake_orch.start = _slow_start
    monkeypatch.setattr(
        "methods.live_research.LiveResearchOrchestrator",
        lambda **kw: fake_orch,
    )
    monkeypatch.setattr(h, "prepare", AsyncMock())
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="sess-rel"))
    h.query_done = False

    run_task = asyncio.create_task(h.runQuery())
    await asyncio.sleep(0.02)
    assert h._lr_research_task is not None

    h.connection_alive_event.clear()
    h._lr_detach_event.set()
    await asyncio.wait_for(run_task, timeout=1.0)

    # 核心斷言：detach return 後 ref 仍非 None 且仍 pending（route 靠此掛 done-callback）
    assert h._lr_research_task is not None and not h._lr_research_task.done()

    slow_done.set()
    await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# Task 2: Route 層成功路徑補 finish_response()（fd 釋放）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_route_calls_finish_response_on_success(monkeypatch):
    """route start handler 成功路徑 return 前呼叫 wrapper.finish_response() → fd 釋放。

    連線釋放治本（plan: lr-sse-connection-release-fix, 2026-06-22）：成功 / detach
    return 後 route 須主動收尾 SSE response（write_eof + transport teardown），否則
    HTTP 連線 fd 不釋放，殭屍長連線累積 → CF edge 522。

    route test pattern 參 test_lr_flag_wiring_continue.py 的 make_mocked_request +
    GUARDRAIL_DR_ENABLED=false + feature flag gate。
    """
    import methods.live_research as lr_mod
    import webserver.routes.api as api_mod
    from core.config import CONFIG
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv('GUARDRAIL_DR_ENABLED', 'false')
    features = CONFIG.reasoning_params.setdefault('features', {})
    monkeypatch.setitem(features, 'live_research', True)

    finish_called = {"n": 0}

    class _FakeWrapper:
        def __init__(self, *a, **k):
            self.connection_alive = True

        async def prepare_response(self):
            pass

        async def write_stream(self, *a, **k):
            pass

        async def finish_response(self):
            finish_called["n"] += 1

        def set_on_disconnect(self, cb):
            pass

    class _FakeHandler:
        connection_alive_event = MagicMock()
        _lr_research_task = None

        def __init__(self, *a, **k):
            pass

        async def runQuery(self):
            return {"status": "checkpoint"}

    monkeypatch.setattr(api_mod, "AioHttpStreamingWrapper", _FakeWrapper)
    monkeypatch.setattr(lr_mod, "LiveResearchHandler", _FakeHandler)

    body = {"query": "台灣綠能發展衝突", "session_id": "start-sid"}
    request = make_mocked_request('POST', '/api/live_research')
    monkeypatch.setattr(request, 'json', AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_start_handler(request)

    assert finish_called["n"] == 1


@pytest.mark.asyncio
async def test_continue_route_calls_finish_response_on_success(monkeypatch):
    """route continue handler 成功路徑 return 前呼叫 wrapper.finish_response() → fd 釋放。

    鏡像 start handler；防 regression：continue 路徑漏收尾 SSE 連線。
    """
    import methods.live_research as lr_mod
    import webserver.routes.api as api_mod
    from core.config import CONFIG
    from aiohttp.test_utils import make_mocked_request

    monkeypatch.setenv('GUARDRAIL_DR_ENABLED', 'false')
    features = CONFIG.reasoning_params.setdefault('features', {})
    monkeypatch.setitem(features, 'live_research', True)

    finish_called = {"n": 0}

    class _FakeWrapper:
        def __init__(self, *a, **k):
            self.connection_alive = True

        async def prepare_response(self):
            pass

        async def write_stream(self, *a, **k):
            pass

        async def finish_response(self):
            finish_called["n"] += 1

        def set_on_disconnect(self, cb):
            pass

    class _FakeHandler:
        connection_alive_event = MagicMock()
        _lr_research_task = None

        def __init__(self, *a, **k):
            pass

        async def continueResearch(self, **kwargs):
            return {"status": "checkpoint"}

    monkeypatch.setattr(api_mod, "AioHttpStreamingWrapper", _FakeWrapper)
    monkeypatch.setattr(lr_mod, "LiveResearchHandler", _FakeHandler)

    body = {
        'session_id': 'frontend-sid',
        'lr_session_id': 'uuid-x',
        'user_message': '',
        'auto_continue': False,
    }
    request = make_mocked_request('POST', '/api/live_research/continue')
    monkeypatch.setattr(request, 'json', AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    assert finish_called["n"] == 1


# ---------------------------------------------------------------------------
# Task 2.5: 路 A — slot release 綁 task 終態（修 Gemini C1）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continue_route_defers_slot_release_until_task_done_on_detach(monkeypatch):
    """路 A C1 核心：detach（task pending）→ route return 時 **兩個** slot（search + LR conc）
    都未 release（仍佔住）；背景 task 終態才由 closure done-callback release（綁 task）。

    這直接鎖死「同 user 第二請求被擋（429）」不變量——若 slot 提早 release，第二請求
    會拿到 slot 啟動並行 task → 同 session 競寫 PG row（Gemini C1）。

    S（Codex Suggestion）：同時驗 search slot + LR conc slot 兩個（只 assert 單一會漏
    另一個 slot 的 regression）。
    """
    import methods.live_research as lr_mod
    import webserver.routes.api as api_mod
    from webserver.middleware.concurrency_limiter import ConcurrencyLimiter
    from aiohttp.test_utils import make_mocked_request
    from core.config import CONFIG

    monkeypatch.setenv('GUARDRAIL_DR_ENABLED', 'true')
    features = CONFIG.reasoning_params.setdefault('features', {})
    monkeypatch.setitem(features, 'live_research', True)

    # 用獨立 limiter 實例（避免 singleton 跨 test 污染），route 經 get_instance 取得
    limiter = ConcurrencyLimiter()
    monkeypatch.setattr(ConcurrencyLimiter, "get_instance", classmethod(lambda cls: limiter))

    # 未認證 mocked request（peername=None → client_ip=0.0.0.0）→ route 內部組的 key 為：
    #   search_ip:0.0.0.0（search slot）+ lr_ip:0.0.0.0（LR conc slot）
    search_key = "search_ip:0.0.0.0"
    conc_key = "lr_ip:0.0.0.0"

    bg_done = asyncio.Event()

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

    class _FakeHandler:
        connection_alive_event = MagicMock()

        def __init__(self, *a, **k):
            # 模擬 detach：handler 提早 return 且保留 _lr_research_task（pending）
            async def _bg():
                await bg_done.wait()
            self._lr_research_task = asyncio.ensure_future(_bg())

        async def continueResearch(self, **kwargs):
            # detach return：status=detached，_lr_research_task 保留非 None 且 pending
            return {"status": "detached"}

    monkeypatch.setattr(api_mod, "AioHttpStreamingWrapper", _FakeWrapper)
    monkeypatch.setattr(lr_mod, "LiveResearchHandler", _FakeHandler)

    body = {
        'session_id': 'frontend-sid',
        'lr_session_id': 'uuid-x',
        'user_message': '',
        'auto_continue': False,
    }
    request = make_mocked_request('POST', '/api/live_research/continue')
    monkeypatch.setattr(request, 'json', AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    # 斷言 1（S, Codex）：route return 後 task 仍 pending → **兩個** slot 都仍被佔（未 release）。
    assert limiter.active_count(search_key) == 1, "detach 時 search slot 不應被 route finally release"
    assert limiter.active_count(conc_key) == 1, "detach 時 LR conc slot 不應被 route finally release"

    # 斷言 2：放行背景 task → done-callback 觸發 → **兩個** slot 都釋放（由 closure 放）。
    bg_done.set()
    await asyncio.sleep(0)  # 讓 done-callback 排程執行
    await asyncio.sleep(0)
    assert limiter.active_count(search_key) == 0, "task 終態後 search slot 應由 done-callback release"
    assert limiter.active_count(conc_key) == 0, "task 終態後 LR conc slot 應由 done-callback release"


def test_concurrency_limiter_release_is_idempotent():
    """release() 對同 key/request_id 呼叫多次 / 不存在的 slot 不 raise（路 A 雙釋放安全網）。"""
    from webserver.middleware.concurrency_limiter import ConcurrencyLimiter
    lim = ConcurrencyLimiter()  # 獨立實例（非 singleton）避免污染
    key, rid = "lr_user:idem-test", "req-idem"
    assert lim.try_acquire(key, rid, limit=1) is True
    lim.release(key, rid)
    # 第二次 release（模擬 route finally + done-callback 都跑）→ 無害、不 raise
    lim.release(key, rid)
    # release 從未 acquire 的 slot → 無害
    lim.release("never:acquired", "ghost")
    assert lim.active_count(key) == 0


@pytest.mark.asyncio
async def test_continue_route_releases_immediately_when_detach_but_task_done(monkeypatch):
    """detach race（in-house AR2 I-A1）：handler 保留 ref 但 task 已 done → route 走 else
    （不 defer）、finally 當場 release 兩 slot（task 已終態，release 時機正確）。

    與 defers_slot_release（task pending → defer、slot 仍佔）構成對照實驗：同樣 detach
    return（status=detached、ref 非 None），唯一差別是 task.done() 狀態，鎖死 discriminator
    `not done()` 的兩條分支各自正確。對照 Risk #8 race 分析。
    """
    import methods.live_research as lr_mod
    import webserver.routes.api as api_mod
    from webserver.middleware.concurrency_limiter import ConcurrencyLimiter
    from aiohttp.test_utils import make_mocked_request
    from core.config import CONFIG

    monkeypatch.setenv('GUARDRAIL_DR_ENABLED', 'true')
    features = CONFIG.reasoning_params.setdefault('features', {})
    monkeypatch.setitem(features, 'live_research', True)

    limiter = ConcurrencyLimiter()
    monkeypatch.setattr(ConcurrencyLimiter, "get_instance", classmethod(lambda cls: limiter))

    search_key = "search_ip:0.0.0.0"
    conc_key = "lr_ip:0.0.0.0"

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

    class _FakeHandler:
        connection_alive_event = MagicMock()

        def __init__(self, *a, **k):
            # 模擬「detach 但 task 已 done」：保留 ref，但下面會等它跑完
            async def _already():
                return None
            self._lr_research_task = asyncio.ensure_future(_already())

        async def continueResearch(self, **kwargs):
            # 等內部 task 真正完成（done()==True）才 return → route 檢查時 not done()==False
            if self._lr_research_task is not None:
                await self._lr_research_task
            return {"status": "detached"}

    monkeypatch.setattr(api_mod, "AioHttpStreamingWrapper", _FakeWrapper)
    monkeypatch.setattr(lr_mod, "LiveResearchHandler", _FakeHandler)

    body = {
        'session_id': 'frontend-sid',
        'lr_session_id': 'uuid-x',
        'user_message': '',
        'auto_continue': False,
    }
    request = make_mocked_request('POST', '/api/live_research/continue')
    monkeypatch.setattr(request, 'json', AsyncMock(return_value=body), raising=False)

    await api_mod.live_research_continue_handler(request)

    # task 已 done → route 走 else（不掛 done-callback、_cont_slot_release_deferred 仍 False）
    # → finally 當場 release **兩個** slot。
    assert limiter.active_count(search_key) == 0, "detach 但 task 已 done → finally 應當場 release search slot"
    assert limiter.active_count(conc_key) == 0, "detach 但 task 已 done → finally 應當場 release LR conc slot"


@pytest.mark.asyncio
async def test_finish_response_idempotent_no_double_write_eof():
    """finish_response 呼叫兩次：第二次因 connection_alive=False 跳過 write_eof（冪等）。

    路 A 後成功 + 例外路徑都可能呼叫 finish_response（互斥分支，深度防禦驗冪等）。
    finish_response 末尾設 connection_alive=False（aiohttp_streaming_wrapper.py:165）→
    第二次呼叫 guard（:159）跳過 write_eof。鎖此冪等行為防回歸。
    """
    from webserver.aiohttp_streaming_wrapper import AioHttpStreamingWrapper

    w = AioHttpStreamingWrapper.__new__(AioHttpStreamingWrapper)
    w.heartbeat_task = None
    w.connection_alive = True
    resp = MagicMock()
    resp._eof_sent = False
    resp.write_eof = AsyncMock()
    w.response = resp

    await w.finish_response()  # 第一次：connection_alive True + _eof_sent False → write_eof
    assert resp.write_eof.await_count == 1
    assert w.connection_alive is False

    await w.finish_response()  # 第二次：connection_alive False → guard 跳過
    assert resp.write_eof.await_count == 1  # 仍 1，未重複


# ---------------------------------------------------------------------------
# Task 3: 移除 route 層 trailing 冗餘 _save_state（CEO-Locked #2）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runquery_no_route_level_save_on_success(monkeypatch):
    """正常完成：persist 由背景 task 內部負責；runQuery return-path 不再額外 _save_state（無雙寫）。

    持久化單一責任歸背景 task 內部 _persist_checkpoint_boundary → _persist_progress →
    _save_state（每 boundary 都寫、idempotent）。route 層 trailing save 已移除
    （plan: lr-sse-connection-release-fix, 2026-06-22, CEO-Locked #2）：detach 後保留會與
    task 內最後寫雙寫、可能用舊 snapshot 覆寫新。

    此 test 的 fake_orch.start 不跑真實 persist 鏈 → 唯一的 _save_state 呼叫就是要移除的
    trailing save。移除後 await_count 應為 0。
    """
    h = _make_handler()  # h._save_state 已是 AsyncMock

    fake_state = MagicMock(current_stage=1, checkpoint_prompt="cp")

    async def _fast_start(**kwargs):
        return fake_state

    fake_orch = MagicMock()
    fake_orch.start = _fast_start
    monkeypatch.setattr(
        "methods.live_research.LiveResearchOrchestrator",
        lambda **kw: fake_orch,
    )
    monkeypatch.setattr(h, "prepare", AsyncMock())
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="sess-rel"))
    h.query_done = False

    await h.runQuery()

    # route-path（runQuery 本體）不再呼叫 _save_state；持久化責任在 orchestrator task 內部
    # （此 test 的 fake_orch.start 不跑真實 _persist_checkpoint_boundary，故 _save_state 應為 0 次）
    assert h._save_state.await_count == 0
