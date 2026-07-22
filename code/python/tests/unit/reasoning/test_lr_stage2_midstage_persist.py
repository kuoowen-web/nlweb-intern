"""Stage 2 per-topic 中途 persist + offline cap（plan: lr-disconnect-midstage-persist）。

root cause 回歸鎖：舊 code 只在整個 per-topic loop 跑完後才更新 state.evidence_pool_json
（orchestrator.py:2202-2203）+ persist（:2239）。中途斷線 raise → 全蒸發。
修法：每個 topic 跑完就把 evidence 落進 state + _persist_progress；topic 間查 offline cap。

R1 AR blocker 消化（D-7）：新增 test_stage2_midtopic_offline_break_does_not_mark_completed
——topic 執行到一半被 offline 打斷（engine.stopped_early=True）時，evidence 仍落盤但
**不**標記 completed_sections（區別於「正常收斂完成」與「topic 都還沒開始跑就 cap」
兩種既有 test case）。
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import EvidencePoolEntry


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _orch(*, alive=True):
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.mock_bab = False
    orch.dry_run = False
    orch.features = {}
    handler = MagicMock()
    evt = MagicMock(); evt.is_set.return_value = alive
    handler.connection_alive_event = evt
    handler._client_offline_since = None
    orch.handler = handler
    orch.associator = MagicMock()
    orch._offline_advance_counted_this_call = False
    # side-effect-free stubs
    orch._emit_stage_change = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._emit_stage2_consolidation = AsyncMock()
    orch._build_topic_evidence_list = MagicMock(return_value=[])
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_progress = AsyncMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    return orch


def _two_core_topics_state():
    """兩個 core topic 的 Stage 1 出場 state（seed evidence 2 筆）。"""
    cm = {
        "research_question": "q",
        "topics": [
            {"topic_id": "t1", "name": "T1", "domain": "d", "relevance": "core",
             "evidence_ids": [], "description": ""},
            {"topic_id": "t2", "name": "T2", "domain": "d", "relevance": "core",
             "evidence_ids": [], "description": ""},
        ],
        "working_hypothesis": "",
    }
    s = LiveResearchStageState()
    s.current_stage = 1
    s.context_map_json = json.dumps(cm)
    s.initial_context_map_json = json.dumps(cm)
    s.evidence_pool_json = json.dumps({
        "1": {"url": "https://seed1", "title": "S1", "snippet": "x", "source": "web",
              "author": "", "iteration_origin": 0, "evidence_id": 1},
    })
    s.executed_searches = []
    return s


@pytest.mark.asyncio
async def test_stage2_persists_after_each_topic(monkeypatch):
    """每個 topic 正常收斂完成 → _persist_progress 至少被呼叫（中途落盤）+ 標記完成。"""
    orch = _orch(alive=True)
    state = _two_core_topics_state()

    # patch BABLoopEngine：每個 topic 回一個「多 1 筆 evidence」的 engine（正常收斂，stopped_early=False）
    made = {"n": 0}

    class FakeEngine:
        def __init__(self, **kw):
            made["n"] += 1
            self._n = made["n"]
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            # 每個 topic 加一筆新 evidence
            new_id = (max(self.evidence_pool.keys()) if self.evidence_pool else 0) + 1
            self.evidence_pool[new_id] = EvidencePoolEntry(
                evidence_id=new_id, url=f"https://t{self._n}", title=f"T{self._n}", snippet="s", source="web")
            self._evidence_counter = new_id
            self.executed_searches = [f"q{self._n}"]
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            # D-7: 正常收斂完成，非 offline 打斷
            self.stopped_early = False
        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json()) \
                if hasattr(kw.get("existing_context_map"), "model_dump_json") else kw["existing_context_map"]
        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEngine
    )

    await orch._run_stage_2(state)

    # 中途 persist：2 topics → _persist_progress 至少 2 次（每 topic 一次）
    assert orch._persist_progress.await_count >= 2
    # evidence_pool_json 反映累積（seed 1 + t1 + t2 = 至少 3 筆）
    pool = json.loads(state.evidence_pool_json)
    assert len(pool) >= 3
    # 兩個 topic 都正常收斂完成 → 標完成
    assert "t1" in state.completed_sections
    assert "t2" in state.completed_sections


@pytest.mark.asyncio
async def test_stage2_midtopic_offline_break_does_not_mark_completed(monkeypatch):
    """R1 AR BLOCKER 消化（D-7）：topic 執行到一半被 offline 打斷

    （engine.stopped_early=True，模擬 max_iterations=2 中第一個 iteration 都還沒跑完
    就被 cooperative break）→ evidence 仍落盤（不浪費已做的研究），但**不**標記
    completed_sections（半套研究不能算完成，否則 resume 永久 skip 這個 topic）。

    對照既有 test_stage2_persists_after_each_topic（全程在線正常收斂）與
    test_stage2_offline_cap_returns_early（topic 開始前就斷線，cap 已達，engine
    根本不會被建立）——本 test 是三者中間那個情境：engine **有**被建立、
    **有**跑、但沒跑完就被打斷。

    R2 AR SHOULD-FIX 1（SF-1）追加：這裡也鎖住 stopped_early=True 分支必須呼叫
    _mark_offline_since——斷言 result.offline_since is not None，回歸鎖住「wall-clock
    cap 起點在 topic 中途第一次偵測到離線時就被記下」，不是要等到下一次 resume
    重新進入這個 topic、跑到「topic 開始前」檢查點才補寫。
    """
    orch = _orch(alive=True)  # _run_stage_2 本身不查 alive（D-6），engine 內部斷線
    state = _two_core_topics_state()

    class FakeEngineInterrupted:
        """模擬 t1 跑到一半被 offline 打斷；t2 正常跑完（驗證 t1 不擋 t2 續跑，
        但如果本 test 只想驗證單一 topic 中斷，executor 可簡化成只建一個 topic 的
        state——這裡刻意留兩個 topic 以同時驗「未完成 topic 不會被永久 skip」的
        resume 語意鄰接效果）。
        """
        _calls = {"n": 0}

        def __init__(self, **kw):
            FakeEngineInterrupted._calls["n"] += 1
            self._n = FakeEngineInterrupted._calls["n"]
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            new_id = (max(self.evidence_pool.keys()) if self.evidence_pool else 0) + 1
            self.evidence_pool[new_id] = EvidencePoolEntry(
                evidence_id=new_id, url=f"https://mid{self._n}", title=f"Mid{self._n}", snippet="s", source="web")
            self._evidence_counter = new_id
            self.executed_searches = [f"q{self._n}"]
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            # 第一個 topic（t1）被打斷；第二個（若被呼叫，代表 bug：t1 未完成卻繼續跑 t2）
            self.stopped_early = (self._n == 1)

        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json()) \
                if hasattr(kw.get("existing_context_map"), "model_dump_json") else kw["existing_context_map"]

        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEngineInterrupted
    )

    result = await orch._run_stage_2(state)

    # evidence 仍落盤（不浪費已做的研究）：seed 1 筆 + mid1 = 至少 2 筆
    pool = json.loads(result.evidence_pool_json)
    assert len(pool) >= 2
    # persist 仍被呼叫（中途落盤）
    assert orch._persist_progress.await_count >= 1
    # 核心斷言：t1 沒有正常收斂完成 → 不在 completed_sections
    assert "t1" not in result.completed_sections
    # engine 只被建立一次（t1 被打斷後 _run_stage_2 提早 return，不繼續跑 t2）
    assert FakeEngineInterrupted._calls["n"] == 1
    # R2 AR SHOULD-FIX 1（SF-1）回歸鎖：stopped_early=True 分支必須呼叫
    # _mark_offline_since，把 wall-clock cap 的起點記下來——否則要等下一次 resume
    # 重新進入這個 topic、走到「topic 開始前」檢查點才會補寫，起點會被推遲。
    assert result.offline_since is not None


@pytest.mark.asyncio
async def test_stage2_offline_cap_returns_early(monkeypatch):
    """offline 且達 cap → 標 offline_capped + persist + return（不跑後續 topic）。"""
    orch = _orch(alive=False)  # 斷線
    state = _two_core_topics_state()
    state.offline_since = 1.0  # 很久以前 → wall-clock 上限必達
    # offline_max_wall_seconds default 900 → time.time()-1.0 遠超

    class FakeEngine:  # 不該被呼叫（cap 在第一個 topic 前就 return）
        def __init__(self, **kw):
            raise AssertionError("BABLoopEngine 不該被建立——offline cap 應提早 return")

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEngine
    )

    result = await orch._run_stage_2(state)
    assert result.offline_capped is True
    assert orch._persist_progress.await_count >= 1
    # 沒有任何 topic 被跑（FakeEngine 會 assert）
    assert "t1" not in state.completed_sections
