"""LR analytics complete/backfill（票 2026-07-28-k Task 3/4）：
- runQuery 在 lr_session_id 生成後回填 queries.conversation_id
- _log_lr_query_complete 冪等 helper 全終態覆蓋
"""
import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from methods.live_research import LiveResearchHandler  # noqa: E402


def _make_handler(with_analytics=True):
    qp = {"query": "Q", "dry_run": "true", "session_id": "sess-an"}
    h = LiveResearchHandler(qp, MagicMock())
    h.final_retrieved_items = []
    h._save_state = AsyncMock()
    if with_analytics:
        h.query_id = "query_777"
        h._lr_analytics_query_start_time = time.time()
    return h


def _patch_fast_orch(monkeypatch, h, result=None, side_effect=None):
    """orchestrator.start 立即完成（或拋例外）的 fake。"""
    fake_state = result or MagicMock(current_stage=1, checkpoint_prompt="cp")

    async def _fast_start(**kwargs):
        if side_effect is not None:
            raise side_effect
        return fake_state

    fake_orch = MagicMock()
    fake_orch.start = _fast_start
    monkeypatch.setattr(
        "methods.live_research.LiveResearchOrchestrator", lambda **kw: fake_orch,
    )
    monkeypatch.setattr(h, "prepare", AsyncMock())
    h.query_done = False
    return fake_orch


@pytest.mark.asyncio
async def test_runquery_backfills_conversation_id_with_lr_session_id(monkeypatch):
    """runQuery：_create_lr_session 回權威 UUID 後，呼叫
    update_query_conversation_id(query_id, lr_session_id)。"""
    h = _make_handler()
    _patch_fast_orch(monkeypatch, h)
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="lr-uuid-A"))

    ql = MagicMock()
    monkeypatch.setattr("core.query_logger.get_query_logger", lambda: ql)

    await h.runQuery()

    ql.update_query_conversation_id.assert_called_once_with("query_777", "lr-uuid-A")


@pytest.mark.asyncio
async def test_runquery_no_backfill_when_no_query_id(monkeypatch):
    """route 未打點（無 query_id）→ 不呼叫回填、不 raise（向後相容既有測試/呼叫路徑）。"""
    h = _make_handler(with_analytics=False)
    _patch_fast_orch(monkeypatch, h)
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="lr-uuid-A"))

    ql = MagicMock()
    monkeypatch.setattr("core.query_logger.get_query_logger", lambda: ql)

    await h.runQuery()

    ql.update_query_conversation_id.assert_not_called()


def _install_ql(monkeypatch):
    ql = MagicMock()
    monkeypatch.setattr("core.query_logger.get_query_logger", lambda: ql)
    return ql


@pytest.mark.asyncio
async def test_complete_logged_once_on_normal_finish(monkeypatch):
    """正常完成：done-callback 記 complete（error_occurred=False）且全程只記一次。"""
    h = _make_handler()
    _patch_fast_orch(monkeypatch, h)
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="lr-uuid-A"))
    ql = _install_ql(monkeypatch)

    await h.runQuery()
    await asyncio.sleep(0)  # 讓 done-callback 排程執行
    await asyncio.sleep(0)

    ql.log_query_complete.assert_called_once()
    kwargs = ql.log_query_complete.call_args.kwargs
    assert kwargs["query_id"] == "query_777"
    assert kwargs["error_occurred"] is False
    assert kwargs["latency_total_ms"] >= 0
    # cost_usd 留空（non-goal，票 2026-07-28-l）
    assert kwargs.get("cost_usd", 0) == 0


@pytest.mark.asyncio
async def test_complete_logged_once_on_task_exception(monkeypatch):
    """task 例外：runQuery raise + done-callback 都會跑 → 冪等 flag 保證只記一次，
    error_occurred=True 帶錯誤訊息。"""
    h = _make_handler()
    _patch_fast_orch(monkeypatch, h, side_effect=RuntimeError("boom"))
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="lr-uuid-A"))
    ql = _install_ql(monkeypatch)

    with pytest.raises(RuntimeError):
        await h.runQuery()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    ql.log_query_complete.assert_called_once()
    kwargs = ql.log_query_complete.call_args.kwargs
    assert kwargs["error_occurred"] is True
    assert "boom" in kwargs["error_message"]


@pytest.mark.asyncio
async def test_complete_on_research_cancelled_is_not_error(monkeypatch):
    """ResearchCancelledError（user-stop / 防呆上限）：error_occurred=False +
    error_message 前綴 cancelled（不污染 error_rate，仍可查）。

    關鍵陷阱鎖：ResearchCancelledError 繼承 Exception（orchestrator_base.py:15）→
    task re-raise 時 done-callback 與 runQuery 的 except Exception 兜底都會跑、
    **順序不保證**（實測 done-callback 先跑）、先到者記終態——兩掛點必須用同一個
    分類 helper（_log_lr_query_complete_from_exc），否則任一先到者把 cancel 誤記成
    error、冪等 flag 讓後到者修不回來。"""
    from reasoning.orchestrator_base import ResearchCancelledError

    h = _make_handler()
    _patch_fast_orch(monkeypatch, h, side_effect=ResearchCancelledError("user stop"))
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="lr-uuid-A"))
    ql = _install_ql(monkeypatch)

    with pytest.raises(ResearchCancelledError):
        await h.runQuery()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    ql.log_query_complete.assert_called_once()
    kwargs = ql.log_query_complete.call_args.kwargs
    assert kwargs["error_occurred"] is False
    assert kwargs["error_message"].startswith("cancelled")


@pytest.mark.asyncio
async def test_complete_on_pre_task_cancel_records_cancelled(monkeypatch):
    """AR R1 三家同抓的懸掛縫：coroutine 在背景 task 建立前被 cancel
    （_create_lr_session await 期間）→ 無 done-callback 可記 → CancelledError
    分支的 local `_task_created` flag 條件補記 cancelled，start row 不懸掛。"""
    h = _make_handler()
    ql = _install_ql(monkeypatch)
    monkeypatch.setattr(
        h, "_create_lr_session", AsyncMock(side_effect=asyncio.CancelledError())
    )

    with pytest.raises(asyncio.CancelledError):
        await h.runQuery()

    ql.log_query_complete.assert_called_once()
    kwargs = ql.log_query_complete.call_args.kwargs
    assert kwargs["error_occurred"] is False
    assert kwargs["error_message"].startswith("cancelled: before task creation")


@pytest.mark.asyncio
async def test_continue_complete_on_pre_task_cancel_records_cancelled(monkeypatch):
    """3k 對稱回歸鎖（AR R2 Codex）：continueResearch 在 `_load_state` await 期間被
    cancel（task 未建立）→ CancelledError 分支條件補記 cancelled，start row 不懸掛。
    （方法名以 code 現況為準；紅階段若名稱不符即 stop-and-report。）"""
    h = _make_handler()
    ql = _install_ql(monkeypatch)
    monkeypatch.setattr(
        h, "_load_state", AsyncMock(side_effect=asyncio.CancelledError())
    )

    with pytest.raises(asyncio.CancelledError):
        await h.continueResearch(user_message="繼續")

    ql.log_query_complete.assert_called_once()
    kwargs = ql.log_query_complete.call_args.kwargs
    assert kwargs["error_occurred"] is False
    assert kwargs["error_message"].startswith("cancelled: before task creation")


@pytest.mark.asyncio
async def test_complete_on_query_done_early_return(monkeypatch):
    """prepare() 擋下（clarification / guardrail → query_done）早退：也是終態，
    記 complete（error_occurred=False）。非 dry_run 路徑才會踩 query_done 檢查。"""
    qp = {"query": "Q", "session_id": "sess-an"}  # 無 dry_run → 走 prepare 分支
    h = LiveResearchHandler(qp, MagicMock())
    h.final_retrieved_items = []
    h._save_state = AsyncMock()
    h.query_id = "query_777"
    h._lr_analytics_query_start_time = time.time()

    async def _prepare_sets_done():
        h.query_done = True

    monkeypatch.setattr(h, "prepare", _prepare_sets_done)
    monkeypatch.setattr(h, "_create_lr_session", AsyncMock(return_value="lr-uuid-A"))
    monkeypatch.setattr(h, "_is_mock_bab", lambda: False)
    ql = _install_ql(monkeypatch)

    await h.runQuery()

    ql.log_query_complete.assert_called_once()
    assert ql.log_query_complete.call_args.kwargs["error_occurred"] is False


@pytest.mark.asyncio
async def test_complete_on_continue_state_not_found(monkeypatch):
    """continueResearch 早退（state_not_found）：也要記 complete（error_occurred=True）。
    dry_run + 空 store → _load_state 自然回 None。"""
    h = _make_handler()
    from methods.live_research import _DRY_RUN_STATE_STORE
    _DRY_RUN_STATE_STORE.clear()
    ql = _install_ql(monkeypatch)

    result = await h.continueResearch(user_message="繼續")

    assert result["status"] == "error"
    ql.log_query_complete.assert_called_once()
    kwargs = ql.log_query_complete.call_args.kwargs
    assert kwargs["error_occurred"] is True
    assert "state_not_found" in kwargs["error_message"]


@pytest.mark.asyncio
async def test_complete_helper_idempotent_and_safe_without_query_id():
    """冪等：連呼兩次只記一次；無 query_id（route 未打點）→ no-op 不 raise。"""
    h = _make_handler()
    ql = MagicMock()
    import core.query_logger as ql_mod
    orig = ql_mod.get_query_logger
    ql_mod.get_query_logger = lambda: ql
    try:
        h._log_lr_query_complete()
        h._log_lr_query_complete(error_occurred=True, error_message="late")
        assert ql.log_query_complete.call_count == 1

        h2 = _make_handler(with_analytics=False)
        h2._log_lr_query_complete()  # 不 raise
        assert ql.log_query_complete.call_count == 1  # h2 無 query_id → 未記
    finally:
        ql_mod.get_query_logger = orig
