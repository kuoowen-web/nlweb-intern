"""LR 離線防呆燒錢上限 orchestrator enforcement（plan: lr-sse-reconnect-resume, 2026-06-15）。

CEO 拍板：
- 離線上限 = 跨到下個 checkpoint 就停（offline_max_checkpoint_advances=1）。
- 上限計數**進 DB state**（offline_checkpoint_advances），不放 orchestrator instance counter。
  → 兩個不同 instance（模擬重連 new orchestrator）共用同一 state，計數從 state 累積，不歸零。
- off-by-one：increment → 立刻判 capped → persist（順序寫死）。
"""
import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState


def _make_handler(alive: bool = False, offline_since=None):
    handler = MagicMock()
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=alive)
    handler._client_offline_since = offline_since
    handler._save_state = AsyncMock()
    return handler


def _orch(handler):
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        return LiveResearchOrchestrator(handler=handler)


# ── _mark_offline_since ─────────────────────────────────────────────

def test_mark_offline_since_sets_from_handler():
    handler = _make_handler(alive=False, offline_since=1718400000.0)
    orch = _orch(handler)
    state = LiveResearchStageState()
    orch._mark_offline_since(state)
    assert state.offline_since == 1718400000.0


def test_mark_offline_since_does_not_overwrite():
    handler = _make_handler(alive=False, offline_since=9999.0)
    orch = _orch(handler)
    state = LiveResearchStageState(offline_since=1111.0)
    orch._mark_offline_since(state)
    assert state.offline_since == 1111.0  # 不覆寫原始起點


def test_mark_offline_since_falls_back_to_now_when_handler_none():
    handler = _make_handler(alive=False, offline_since=None)
    orch = _orch(handler)
    state = LiveResearchStageState()
    before = time.time()
    orch._mark_offline_since(state)
    assert state.offline_since is not None
    assert state.offline_since >= before


# ── _offline_cap_reached ────────────────────────────────────────────

def test_offline_cap_reached_wall_seconds():
    handler = _make_handler(alive=False)
    orch = _orch(handler)
    state = LiveResearchStageState(offline_since=time.time() - 100000)  # 久遠
    assert orch._offline_cap_reached(state) is True
    assert state.offline_cap_reason == "wall_seconds"


def test_offline_cap_reached_checkpoint_advances():
    handler = _make_handler(alive=False)
    orch = _orch(handler)
    # advances 已達 default max=1
    state = LiveResearchStageState(offline_since=time.time(), offline_checkpoint_advances=1)
    assert orch._offline_cap_reached(state) is True
    assert state.offline_cap_reason == "next_checkpoint"


def test_offline_cap_not_reached_fresh():
    handler = _make_handler(alive=False)
    orch = _orch(handler)
    state = LiveResearchStageState(offline_since=time.time(), offline_checkpoint_advances=0)
    assert orch._offline_cap_reached(state) is False


# ── persist + offline counting at checkpoint boundary ───────────────

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_checkpoint_persist_counts_advance_and_caps_when_offline():
    """離線時跑到 checkpoint：increment→立刻標 capped（advances=1>=max=1）→persist。"""
    handler = _make_handler(alive=False, offline_since=time.time())
    orch = _orch(handler)
    state = LiveResearchStageState(current_stage=1, offline_checkpoint_advances=0)
    _run(orch._persist_checkpoint_boundary(state))
    assert state.offline_checkpoint_advances == 1
    assert state.offline_capped is True
    assert state.offline_cap_reason == "next_checkpoint"
    handler._save_state.assert_awaited()  # persist 有跑


def test_checkpoint_persist_when_online_does_not_count():
    """連線正常：不計數、不 cap、仍 persist（既有行為不變）。"""
    handler = _make_handler(alive=True)
    orch = _orch(handler)
    state = LiveResearchStageState(current_stage=1, offline_checkpoint_advances=0)
    _run(orch._persist_checkpoint_boundary(state))
    assert state.offline_checkpoint_advances == 0
    assert state.offline_capped is False
    handler._save_state.assert_awaited()


def test_counter_persists_across_instances_not_reset():
    """CEO 拍板核心：兩個不同 orchestrator instance（重連 new instance）共用同一 state。
    計數從 state 累積，不因 new instance 歸零。"""
    state = LiveResearchStageState(current_stage=5, offline_since=time.time(),
                                   offline_checkpoint_advances=0)
    # instance 1：離線跑到一個 checkpoint
    h1 = _make_handler(alive=False, offline_since=state.offline_since)
    orch1 = _orch(h1)
    _run(orch1._persist_checkpoint_boundary(state))
    assert state.offline_checkpoint_advances == 1
    assert state.offline_capped is True
    # instance 2（重連 new orchestrator）：同一 state 進來，仍離線 → cap 立即 True
    h2 = _make_handler(alive=False, offline_since=state.offline_since)
    orch2 = _orch(h2)
    assert orch2._offline_cap_reached(state) is True  # 計數沒歸零


def test_counter_count_once_per_call_even_if_helper_called_twice():
    """同一 continue call 內若多 boundary 被穿越，計數只 +1（per-call guard）。"""
    handler = _make_handler(alive=False, offline_since=time.time())
    orch = _orch(handler)
    state = LiveResearchStageState(current_stage=5, offline_checkpoint_advances=0)
    # 同一 call 內呼叫兩次（模擬 _handle_stage_5_response→_run_stage_5 各一 boundary）
    _run(orch._persist_checkpoint_boundary(state))
    _run(orch._persist_checkpoint_boundary(state))
    assert state.offline_checkpoint_advances == 1  # 不是 2
