"""LR 一致性監控暫停「長牙齒」修復回歸鎖（plan: lr-consistency-pause-teeth）。

修復意圖：漂移暫停時 (1) 不誤標 completed（掛載點 A，Stage 2）(2) 真的在 checkpoint
彈 drift banner 問 user（掛載點 B，Stage 1+2）(3) 回補那趟抑制再次暫停防 soft-lock。
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import ConsistencyReview, EvidencePoolEntry
from reasoning.live_research import lr_copy


def test_drift_pause_banner_includes_drift_description():
    """helper 把 ConsistencyReview.drift_description 拼進 banner proposal。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    review = ConsistencyReview(
        drift_level="moderate",
        drift_description="研究重心從『再生能源政策』偏移到『電網技術規格』",
        dubao_voice_message="等等，方向好像偏了...",
        recommended_action="pause_confirm",
    )
    proposal = orch._build_drift_pause_proposal(review)
    # banner 主體文案（明說方向可能偏了、問要繼續還是調整）
    assert lr_copy.DRIFT_PAUSE_BANNER in proposal
    # 具體漂移描述被拼進去（讓 user 知道偏到哪）
    assert "電網技術規格" in proposal


# =============================================================================
# Task 2/3: Stage 2 漂移暫停 fixtures + tests
# =============================================================================


def _orch_stage2(*, alive=True):
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


def _make_drift_review():
    return ConsistencyReview(
        drift_level="moderate",
        drift_description="研究重心偏移到不相關子題",
        dubao_voice_message="等等，方向好像偏了...",
        recommended_action="pause_confirm",
    )


@pytest.mark.asyncio
async def test_stage2_consistency_pause_does_not_mark_completed(monkeypatch):
    """漂移暫停（engine.paused_by_consistency=True）→ topic 不標 completed、evidence 落盤。"""
    orch = _orch_stage2(alive=True)
    state = _two_core_topics_state()

    class FakeEnginePaused:
        _calls = {"n": 0}

        def __init__(self, **kw):
            FakeEnginePaused._calls["n"] += 1
            self._n = FakeEnginePaused._calls["n"]
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            new_id = (max(self.evidence_pool.keys()) if self.evidence_pool else 0) + 1
            self.evidence_pool[new_id] = EvidencePoolEntry(
                evidence_id=new_id, url=f"https://d{self._n}", title=f"D{self._n}",
                snippet="s", source="web")
            self._evidence_counter = new_id
            self.executed_searches = [f"q{self._n}"]
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            # 漂移暫停：非 offline（stopped_early=False），是 consistency pause
            self.stopped_early = False
            self.paused_by_consistency = (self._n == 1)
            self.consistency_review = _make_drift_review()

        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json())

        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEnginePaused
    )

    result = await orch._run_stage_2(state)

    # 核心斷言：t1 漂移暫停 → 不在 completed_sections（resume 會重進補齊）
    assert "t1" not in result.completed_sections
    # evidence 仍落盤（不浪費已蒐集）
    pool = json.loads(result.evidence_pool_json)
    assert len(pool) >= 2
    assert orch._persist_progress.await_count >= 1
    # engine 只建一次（t1 暫停後提早 return，不繼續跑 t2）
    assert FakeEnginePaused._calls["n"] == 1


@pytest.mark.asyncio
async def test_stage2_consistency_pause_emits_drift_banner(monkeypatch):
    """漂移暫停 → set_checkpoint 為 drift banner + emit checkpoint；不記 offline_since。"""
    orch = _orch_stage2(alive=True)
    state = _two_core_topics_state()

    class FakeEnginePaused:
        def __init__(self, **kw):
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            self._evidence_counter = max(self.evidence_pool.keys()) if self.evidence_pool else 0
            self.executed_searches = []
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            self.stopped_early = False
            self.paused_by_consistency = True
            self.consistency_review = _make_drift_review()

        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json())

        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEnginePaused
    )

    result = await orch._run_stage_2(state)

    # checkpoint proposal = drift banner（含具體漂移描述）
    assert lr_copy.DRIFT_PAUSE_BANNER in result.checkpoint_prompt
    assert "研究重心偏移到不相關子題" in result.checkpoint_prompt
    # 停在 checkpoint 等 user（不是 completed）
    assert result.stage_status == "checkpoint"
    # 漂移不是離線 → 不記 offline_since（不污染 offline wall-clock cap）
    assert result.offline_since is None
    # emit_checkpoint 被呼叫（drift banner 推到前端）
    assert orch._emit_checkpoint.await_count >= 1
    # AR R1 blocker 消化：drift checkpoint 標記 stage2_drift_paused，
    # 讓 continue_from_checkpoint 走 continue/adjust 分流（Task 6）
    assert result.stage2_drift_paused is True
    # AR R1 nit 消化：drift checkpoint 帶 evidence metadata（對齊正常 Stage 2 checkpoint）
    _emit_kwargs = orch._emit_checkpoint.await_args.kwargs
    assert "evidence_list" in _emit_kwargs
    assert _emit_kwargs.get("evidence_total") == len(json.loads(result.evidence_pool_json))


@pytest.mark.asyncio
async def test_stage2_offline_break_still_silent_no_banner(monkeypatch):
    """regression：純 offline 中斷維持既有靜默行為——記 offline_since、不彈 drift banner。"""
    orch = _orch_stage2(alive=True)
    state = _two_core_topics_state()

    class FakeEngineOffline:
        def __init__(self, **kw):
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            self._evidence_counter = max(self.evidence_pool.keys()) if self.evidence_pool else 0
            self.executed_searches = []
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            # 純 offline：stopped_early=True、paused_by_consistency=False
            self.stopped_early = True
            self.paused_by_consistency = False
            self.consistency_review = None

        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json())

        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEngineOffline
    )

    result = await orch._run_stage_2(state)

    # offline 記 wall-clock cap 起點（既有 D-7 / SF-1 行為不被本修復破壞）
    assert result.offline_since is not None
    # offline 不彈 drift banner（proposal 不含 banner 文案）
    assert lr_copy.DRIFT_PAUSE_BANNER not in (result.checkpoint_prompt or "")
    # t1 未完成（offline 中斷也不標 completed）
    assert "t1" not in result.completed_sections


def test_stage2_drift_paused_survives_persist_round_trip():
    """AR R3 NIT：drift flag 經 to_dict→from_dict round-trip 保留（resume 後不丟）。"""
    s = LiveResearchStageState()
    s.stage2_drift_paused = True
    restored = LiveResearchStageState.from_dict(s.to_dict())
    assert restored.stage2_drift_paused is True


def test_stage2_drift_paused_cleared_on_reset_and_advance():
    """AR R3 NIT：reset / advance_to_stage 清除 drift flag（不殘留到下個 stage）。

    偏差聲明：plan 用 s.reset()，但 stage_state.py 無 reset() 方法——實際 reset 入口
    為 reset_to_stage(target) / reset_for_recollect()。用 reset_to_stage(1) 對齊實況。
    """
    s = LiveResearchStageState()
    s.stage2_drift_paused = True
    s.reset_to_stage(1)
    assert s.stage2_drift_paused is False

    s2 = LiveResearchStageState()
    s2.stage2_drift_paused = True
    s2.advance_to_stage(3)
    assert s2.stage2_drift_paused is False


# =============================================================================
# Task 4: Stage 1 漂移暫停 fixtures + tests
# =============================================================================


def _orch_stage1(*, drift=False):
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.mock_bab = False
    orch.dry_run = True   # skip _maybe_extract_initial_format 的真 LLM 呼叫
    orch.features = {}
    orch.max_bab_iterations = 3
    orch.handler = MagicMock()
    orch.associator = MagicMock()
    orch._offline_advance_counted_this_call = False
    orch._emit_stage_change = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._build_topic_evidence_list = MagicMock(return_value=[])
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._drift = drift
    return orch


@pytest.mark.asyncio
async def test_stage1_consistency_pause_emits_drift_banner(monkeypatch):
    """Stage 1 漂移暫停 → checkpoint proposal 為 drift banner，非『研究結構提案』。"""
    orch = _orch_stage1(drift=True)
    state = LiveResearchStageState()
    state.advance_to_stage(1)

    cm_json = json.dumps({
        "research_question": "q",
        "topics": [{"topic_id": "t1", "name": "T1", "domain": "d", "relevance": "core",
                    "evidence_ids": [], "description": ""}],
        "working_hypothesis": "",
    })

    class FakeEngineStage1:
        def __init__(self, **kw):
            from reasoning.schemas_live import ContextMap
            self._cm = ContextMap.model_validate_json(cm_json)
            self.initial_context_map = self._cm.model_copy(deep=True)
            self.evidence_pool = {}
            self._evidence_counter = 0
            self.executed_searches = []
            self.state = None
            self._current_stage = ""
            self.stopped_early = False
            self.paused_by_consistency = True
            self.consistency_review = _make_drift_review()

        async def run_loop(self, **kw):
            return self._cm

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEngineStage1
    )

    result = await orch._run_stage_1(state, "q", None)

    # 漂移暫停 → proposal 為 drift banner，不是「研究結構提案」
    assert lr_copy.DRIFT_PAUSE_BANNER in result.checkpoint_prompt
    assert "研究結構提案" not in result.checkpoint_prompt
    assert result.stage_status == "checkpoint"


@pytest.mark.asyncio
async def test_stage1_no_drift_emits_normal_proposal(monkeypatch):
    """regression：Stage 1 無漂移 → 維持既有『研究結構提案』checkpoint，不彈 banner。"""
    orch = _orch_stage1(drift=False)
    state = LiveResearchStageState()
    state.advance_to_stage(1)

    cm_json = json.dumps({
        "research_question": "q",
        "topics": [{"topic_id": "t1", "name": "T1", "domain": "d", "relevance": "core",
                    "evidence_ids": [], "description": ""}],
        "working_hypothesis": "",
    })

    class FakeEngineStage1OK:
        def __init__(self, **kw):
            from reasoning.schemas_live import ContextMap
            self._cm = ContextMap.model_validate_json(cm_json)
            self.initial_context_map = self._cm.model_copy(deep=True)
            self.evidence_pool = {}
            self._evidence_counter = 0
            self.executed_searches = []
            self.state = None
            self._current_stage = ""
            self.stopped_early = False
            self.paused_by_consistency = False   # 無漂移
            self.consistency_review = None

        async def run_loop(self, **kw):
            return self._cm

    monkeypatch.setattr(
        "reasoning.live_research.orchestrator.BABLoopEngine", FakeEngineStage1OK
    )

    result = await orch._run_stage_1(state, "q", None)

    assert "研究結構提案" in result.checkpoint_prompt
    assert lr_copy.DRIFT_PAUSE_BANNER not in result.checkpoint_prompt


# =============================================================================
# Task 5: 兩條中斷路徑正交鎖
# =============================================================================


@pytest.mark.asyncio
async def test_two_interrupt_paths_are_orthogonal(monkeypatch):
    """正交鎖：同一 caller，offline flag 與 drift flag 各自獨立觸發對應處置，不交叉。

    - offline-only（stopped_early=True, paused_by_consistency=False）→ 記 offline_since、無 banner
    - drift-only（stopped_early=False, paused_by_consistency=True）→ 無 offline_since、有 banner
    """
    async def run_once(*, stopped_early, paused):
        orch = _orch_stage2(alive=True)
        state = _two_core_topics_state()

        class FakeEngine:
            def __init__(self, **kw):
                self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
                self._evidence_counter = max(self.evidence_pool.keys()) if self.evidence_pool else 0
                self.executed_searches = []
                self.state = None
                self._current_topic_id = ""
                self._current_stage = ""
                self.stopped_early = stopped_early
                self.paused_by_consistency = paused
                self.consistency_review = _make_drift_review() if paused else None

            async def run_loop(self, **kw):
                from reasoning.schemas_live import ContextMap
                return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json())

            async def emit_evidence_sufficiency_narration(self):
                return None

        monkeypatch.setattr("reasoning.live_research.orchestrator.BABLoopEngine", FakeEngine)
        return await orch._run_stage_2(state)

    offline_res = await run_once(stopped_early=True, paused=False)
    assert offline_res.offline_since is not None
    assert lr_copy.DRIFT_PAUSE_BANNER not in (offline_res.checkpoint_prompt or "")

    drift_res = await run_once(stopped_early=False, paused=True)
    assert drift_res.offline_since is None
    assert lr_copy.DRIFT_PAUSE_BANNER in drift_res.checkpoint_prompt


# =============================================================================
# Task 6/7/8（合併 land unit）: drift checkpoint 回覆分流 + 回補 helper + suppress
# =============================================================================


def _has_incomplete_state():
    """Stage 2 checkpoint、2 個 core topic 但只 1 個 completed（漂移暫停留下的）。

    注意：此 helper Task 6 test 先用（`_drift_paused_state` 依賴它），Task 7 的 helper
    test 也引用同一個定義——放本檔（Task 6 Step 2）一次，Task 7 不重複定義。
    """
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
    s.current_stage = 2
    s.stage_status = "checkpoint"
    s.context_map_json = json.dumps(cm)
    s.initial_context_map_json = json.dumps(cm)
    s.evidence_pool_json = json.dumps({
        "1": {"url": "https://s1", "title": "S1", "snippet": "x", "source": "web",
              "author": "", "iteration_origin": 0, "evidence_id": 1},
    })
    s.completed_sections = ["t1"]   # t2 未完成（漂移暫停）
    s.executed_searches = []
    return s


def _drift_paused_state():
    """Stage 2 drift checkpoint state：2 core topic、1 completed、stage2_drift_paused=True。"""
    s = _has_incomplete_state()   # 2 core topic、t1 completed、t2 未完成
    s.stage2_drift_paused = True
    return s


@pytest.mark.asyncio
async def test_drift_continue_reenters_stage2_backfill(monkeypatch):
    """drift checkpoint + user 回『繼續』(confirm) → 回補未完成 core topic（suppress），不 reframe。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="confirm")
    calls = {"stage2": 0, "stage3": 0, "reframe_emit": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        calls["suppress"] = suppress_consistency_pause
        state.completed_sections = ["t1", "t2"]
        state.stage2_drift_paused = False
        return state

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._run_stage_3 = fake_run_stage_3

    state = _drift_paused_state()
    result = await orch.continue_from_checkpoint(state, user_message="繼續", auto_continue=False)

    assert calls["stage2"] == 1        # 回補
    assert calls["suppress"] is True   # suppress 防 soft-lock
    assert calls["stage3"] == 0        # 不進 Stage 3
    assert calls["reframe_emit"] == 0  # 不 reframe
    assert result.stage2_drift_paused is False  # flag 消費


@pytest.mark.asyncio
async def test_drift_adjust_routes_to_reframe(monkeypatch):
    """drift checkpoint + user 回『我想改成談別的方向』(adjust) → 解 reframe op + emit reframe proposal。"""
    from reasoning.schemas_live import ContextMapRevisionOperation, Stage1ParsedIntent

    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="adjust")

    reframe_op = ContextMapRevisionOperation(
        op_type="reframe_structure",
        new_chapters=[{"name": "新方向章", "description": "", "relevance": "core"}],
    )
    orch._parse_stage_1_intent = AsyncMock(
        return_value=Stage1ParsedIntent(action="adjust", operations=[reframe_op], summary="reframe")
    )
    emitted = {"n": 0, "target_stage": None}

    async def fake_emit_reframe(state, op, cm, summary, target_stage):
        emitted["n"] += 1
        emitted["target_stage"] = target_stage
        state.pending_reframe_json = op.model_dump_json()
        state.stage2_drift_paused = False
        return state

    orch._emit_reframe_proposal = fake_emit_reframe
    calls = {"stage3": 0}

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    orch._run_stage_3 = fake_run_stage_3

    state = _drift_paused_state()
    result = await orch.continue_from_checkpoint(
        state, user_message="我想改成談產業競爭力那個方向", auto_continue=False
    )

    assert emitted["n"] == 1                 # emit reframe proposal
    assert emitted["target_stage"] == 2      # target_stage=2（Stage 2 drift entry）
    assert result.pending_reframe_json != "" # 進 reframe confirm round
    assert calls["stage3"] == 0              # 不推 Stage 3
    assert result.stage2_drift_paused is False


@pytest.mark.asyncio
async def test_drift_adjust_no_reframe_op_reprompts(monkeypatch):
    """adjust 但解不出 reframe op（None / 無 reframe_structure）→ 不 silent advance，re-emit drift checkpoint。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="adjust")
    orch._parse_stage_1_intent = AsyncMock(return_value=None)  # 解不出
    calls = {"stage2": 0, "stage3": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        return state

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._run_stage_3 = fake_run_stage_3

    state = _drift_paused_state()
    result = await orch.continue_from_checkpoint(
        state, user_message="嗯我不確定", auto_continue=False
    )

    assert calls["stage2"] == 0                    # 不回補
    assert calls["stage3"] == 0                    # 不推進（no silent fail）
    assert result.stage2_drift_paused is True      # 保留 flag，等 user 再說
    assert orch._emit_checkpoint.await_count >= 1  # re-emit drift checkpoint


@pytest.mark.asyncio
async def test_pending_reframe_confirm_stage2_reenters_backfill(monkeypatch):
    """_handle_pending_reframe target_stage==2 confirm → apply reframe + 回 Stage 2（suppress）。"""
    from reasoning.schemas_live import ContextMapRevisionOperation

    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._emit_narration = AsyncMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="confirm")
    calls = {"stage2": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        calls["suppress"] = suppress_consistency_pause
        return state

    orch._run_stage_2 = fake_run_stage_2

    reframe_op = ContextMapRevisionOperation(
        op_type="reframe_structure",
        new_chapters=[{"name": "新章", "description": "", "relevance": "core"}],
    )
    state = _two_core_topics_state()
    state.current_stage = 2
    state.stage_status = "checkpoint"
    state.completed_sections = ["t1"]   # reframe 前有殘留 completed（AR R2 NIT）
    state.pending_reframe_json = reframe_op.model_dump_json()

    result = await orch._handle_pending_reframe(state, "OK", target_stage=2)

    assert calls["stage2"] == 1
    assert calls["suppress"] is True
    assert result.pending_reframe_json == ""   # pending 清除
    # AR R2 NIT：reframe 套新結構後 completed_sections 清空（舊 topic ID 不殘留）
    assert result.completed_sections == []


@pytest.mark.asyncio
async def test_continue_from_checkpoint_stage2_pending_reframe_confirm_e2e(monkeypatch):
    """AR R2 BLOCKER 1：Stage 2 drift 後 pending_reframe 已設，continue_from_checkpoint
    真實 dispatch 到 reframe confirm round（不掉進 _handle_stage_2_response）。"""
    from reasoning.schemas_live import ContextMapRevisionOperation

    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    # confirm shortcut：user 回「OK」→ _handle_pending_reframe 判 confirm
    orch._classify_confirmation_intent = AsyncMock(return_value="confirm")
    calls = {"stage2": 0, "stage3": 0, "stage2_response": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        calls["suppress"] = suppress_consistency_pause
        return state

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    # 若 wiring 沒接對（掉進 else），會呼叫這個 → 斷言它沒被呼叫證明 BLOCKER 1 修好
    async def fake_stage2_response(state, msg, auto):
        calls["stage2_response"] += 1
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._run_stage_3 = fake_run_stage_3
    orch._handle_stage_2_response = fake_stage2_response

    reframe_op = ContextMapRevisionOperation(
        op_type="reframe_structure",
        new_chapters=[{"name": "新方向章", "description": "", "relevance": "core"}],
    )
    state = _two_core_topics_state()
    state.current_stage = 2
    state.stage_status = "checkpoint"
    state.pending_reframe_json = reframe_op.model_dump_json()
    state.stage2_drift_paused = False   # adjust 分支已清（狀態改由 pending 接管）

    # user 於 reframe confirm round 回「OK 就用這個」
    result = await orch.continue_from_checkpoint(
        state, user_message="OK 就用這個新方向", auto_continue=False
    )

    # 核心：走 reframe confirm apply → 回 Stage 2 重蒐集（suppress），沒掉進
    # _handle_stage_2_response 把「OK」當 Stage 2 feedback（BLOCKER 1 修復驗證）
    assert calls["stage2"] == 1
    assert calls["suppress"] is True
    assert calls["stage2_response"] == 0   # 沒被當普通 Stage 2 回覆處理
    assert calls["stage3"] == 0            # 沒直接跳 Stage 3
    assert result.pending_reframe_json == ""   # reframe 已 apply、pending 清除


@pytest.mark.asyncio
async def test_continue_from_checkpoint_stage2_pending_reframe_cancel_backfills_e2e(monkeypatch):
    """AR R2 BLOCKER 2：Stage 2 drift → reframe proposal → user cancel，
    continue_from_checkpoint 走回補（不 re-emit dead drift banner、不掉 _handle_stage_2_response）。"""
    from reasoning.schemas_live import ContextMapRevisionOperation

    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="cancel")
    calls = {"stage2": 0, "stage3": 0, "stage2_response": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        calls["suppress"] = suppress_consistency_pause
        return state

    async def fake_stage2_response(state, msg, auto):
        calls["stage2_response"] += 1
        return state

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._handle_stage_2_response = fake_stage2_response
    orch._run_stage_3 = fake_run_stage_3

    reframe_op = ContextMapRevisionOperation(
        op_type="reframe_structure",
        new_chapters=[{"name": "章", "description": "", "relevance": "core"}],
    )
    state = _has_incomplete_state()   # 2 core topic、t2 未完成
    state.current_stage = 2
    state.stage_status = "checkpoint"
    state.pending_reframe_json = reframe_op.model_dump_json()
    state.stage2_drift_paused = False

    result = await orch.continue_from_checkpoint(
        state, user_message="算了不要調整", auto_continue=False
    )

    # cancel==2 → 回補未完成 core topic（不 re-emit dead drift banner、不當 Stage 2 feedback）
    assert calls["stage2"] == 1
    assert calls["suppress"] is True
    assert calls["stage2_response"] == 0
    assert result.pending_reframe_json == ""


@pytest.mark.asyncio
async def test_pending_reframe_cancel_stage2_clears_stale_drift_flag(monkeypatch):
    """AR R3 SHOULD-FIX 2：cancel==2 分支即使進來時 stage2_drift_paused=True（stale：
    持久化 / 舊 state 讓 pending 與 drift flag 同真）也防禦性清成 False，不留 routing 殘留。"""
    from reasoning.schemas_live import ContextMapRevisionOperation

    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._emit_narration = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="cancel")
    calls = {"stage2": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        state.completed_sections = ["t1", "t2"]
        return state

    orch._run_stage_2 = fake_run_stage_2

    reframe_op = ContextMapRevisionOperation(
        op_type="reframe_structure",
        new_chapters=[{"name": "章", "description": "", "relevance": "core"}],
    )
    state = _has_incomplete_state()   # 2 core topic、t2 未完成
    state.pending_reframe_json = reframe_op.model_dump_json()
    state.stage2_drift_paused = True   # stale：兩 flag 同真

    result = await orch._handle_pending_reframe(state, "算了不要調整", target_stage=2)

    # 防禦性清 flag：即使進來 True，cancel==2 分支也清成 False（不留 routing 殘留）
    assert result.stage2_drift_paused is False
    assert result.pending_reframe_json == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["不要調整", "照目前方向繼續", "就照現在這樣"])
async def test_drift_cancel_phrases_route_to_backfill(monkeypatch, phrase):
    """AR R2 SHOULD-FIX 1：三種 cancel-意圖 phrase（classifier 回 cancel）→ 回補、不誤走 reframe。

    document classifier 的 cancel 語意在 drift 情境映射成「照當前方向繼續」。
    """
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="cancel")
    orch._parse_stage_1_intent = AsyncMock(return_value=None)  # 若誤走 reframe 會被呼叫
    calls = {"stage2": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        calls["suppress"] = suppress_consistency_pause
        state.completed_sections = ["t1", "t2"]
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._run_stage_3 = AsyncMock(side_effect=lambda s: s)

    state = _drift_paused_state()   # stage2_drift_paused=True（drift 分流入口）
    result = await orch.continue_from_checkpoint(state, user_message=phrase, auto_continue=False)

    assert calls["stage2"] == 1                       # 回補
    assert calls["suppress"] is True
    orch._parse_stage_1_intent.assert_not_awaited()   # 沒誤走 reframe 解析
    assert result.stage2_drift_paused is False


# =============================================================================
# Task 7: _has_incomplete_core_topics helper unit tests + drift confirm/normal 回歸鎖
# =============================================================================


def test_has_incomplete_core_topics_detects_gap():
    """helper：有 core topic 不在 completed_sections → True。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    state = _has_incomplete_state()
    assert orch._has_incomplete_core_topics(state) is True
    state.completed_sections = ["t1", "t2"]
    assert orch._has_incomplete_core_topics(state) is False


def test_has_incomplete_core_topics_corrupt_context_map_returns_true():
    """AR R1 SHOULD-FIX 2：context_map_json 毀損 → 不 silent 回 False 放行，回 True + log。

    偏差聲明：plan 用 caplog.records 斷言 log，但本專案自製 LazyLogger 不 propagate 到
    root logger（見 test_grounded_claim_schema.py:102），caplog 抓不到。改用 patch.object
    直攔 orchestrator.logger.exception（同 test_emit_checkpoint_evidence.py:107 pattern），
    斷言意圖不變（不吞掉資料毀損＝logger.exception 有被呼叫）。
    """
    from reasoning.live_research import orchestrator as orch_mod
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    state = _has_incomplete_state()
    state.context_map_json = "{not valid json"   # 毀損
    with patch.object(orch_mod, "logger") as mock_logger:
        assert orch._has_incomplete_core_topics(state) is True   # 不放行 Stage 3
    # logger.exception 有記（不吞掉資料毀損）
    assert mock_logger.exception.call_count >= 1
    _exc_msgs = [str(c.args[0]) for c in mock_logger.exception.call_args_list if c.args]
    assert any("context_map_json" in m or "解析失敗" in m for m in _exc_msgs)


@pytest.mark.asyncio
async def test_continue_drift_confirm_reenters_stage2_via_flag(monkeypatch):
    """drift checkpoint（stage2_drift_paused=True）+ confirm → 回補未完成 core topic，不進 Stage 3。

    這是 Task 6 drift 分流 + Task 7 helper 的聯合行為（continue 路徑）。
    """
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value="confirm")
    calls = {"stage2": 0, "stage3": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        calls["suppress"] = suppress_consistency_pause
        state.completed_sections = ["t1", "t2"]
        state.stage2_drift_paused = False
        return state

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._run_stage_3 = fake_run_stage_3

    state = _has_incomplete_state()
    state.stage2_drift_paused = True   # drift checkpoint
    result = await orch.continue_from_checkpoint(state, user_message="繼續", auto_continue=False)

    assert calls["stage2"] == 1
    assert calls["suppress"] is True
    assert calls["stage3"] == 0


@pytest.mark.asyncio
async def test_continue_normal_stage2_not_drift_advances_stage3(monkeypatch):
    """regression：正常 Stage 2 收斂 checkpoint（stage2_drift_paused=False）→ 走既有
    _handle_stage_2_response + _run_stage_3，不被 drift 回補邏輯攔截。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch._maybe_reset_offline_counters = MagicMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._handle_stage_2_response = AsyncMock(side_effect=lambda s, *a, **k: s)
    calls = {"stage2": 0, "stage3": 0}

    async def fake_run_stage_2(state, *, suppress_consistency_pause=False):
        calls["stage2"] += 1
        return state

    async def fake_run_stage_3(state):
        calls["stage3"] += 1
        return state

    orch._run_stage_2 = fake_run_stage_2
    orch._run_stage_3 = fake_run_stage_3

    state = _has_incomplete_state()
    state.stage2_drift_paused = False   # 正常收斂 checkpoint（非 drift）
    result = await orch.continue_from_checkpoint(state, user_message="繼續", auto_continue=True)

    # 正常路徑：不走 drift 回補，直接 _handle_stage_2_response + _run_stage_3
    assert calls["stage2"] == 0
    assert calls["stage3"] == 1


# =============================================================================
# Task 8: 回補趟抑制一致性暫停（engine 接線）
# =============================================================================


@pytest.mark.asyncio
async def test_stage2_suppress_disables_consistency_monitor(monkeypatch):
    """suppress_consistency_pause=True → BABLoopEngine 以 enable_consistency_monitor=False 建構。"""
    orch = _orch_stage2(alive=True)
    orch.features = {"live_research_consistency_monitor": True}  # 全域開著
    state = _two_core_topics_state()
    state.completed_sections = ["t1"]  # 只剩 t2 要補

    captured = {}

    class FakeEngine:
        def __init__(self, **kw):
            captured["enable_consistency_monitor"] = kw.get("enable_consistency_monitor")
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            self._evidence_counter = max(self.evidence_pool.keys()) if self.evidence_pool else 0
            self.executed_searches = []
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            self.stopped_early = False
            self.paused_by_consistency = False   # 抑制後不再暫停
            self.consistency_review = None

        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json())

        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr("reasoning.live_research.orchestrator.BABLoopEngine", FakeEngine)

    await orch._run_stage_2(state, suppress_consistency_pause=True)

    # 回補趟：engine consistency monitor 關（作用於這一趟，全域 flag 未動）
    assert captured["enable_consistency_monitor"] is False
    assert orch.features["live_research_consistency_monitor"] is True   # 全域未被改


@pytest.mark.asyncio
async def test_stage2_default_keeps_consistency_monitor(monkeypatch):
    """regression：預設（不 suppress）→ engine 沿全域 feature flag 建構（monitor 開）。"""
    orch = _orch_stage2(alive=True)
    orch.features = {"live_research_consistency_monitor": True}
    state = _two_core_topics_state()

    captured = {}

    class FakeEngine:
        def __init__(self, **kw):
            captured["enable_consistency_monitor"] = kw.get("enable_consistency_monitor")
            self.evidence_pool = dict(kw.get("seed_evidence_pool") or {})
            new_id = (max(self.evidence_pool.keys()) if self.evidence_pool else 0) + 1
            self.evidence_pool[new_id] = EvidencePoolEntry(
                evidence_id=new_id, url="https://x", title="X", snippet="s", source="web")
            self._evidence_counter = new_id
            self.executed_searches = []
            self.state = None
            self._current_topic_id = ""
            self._current_stage = ""
            self.stopped_early = False
            self.paused_by_consistency = False
            self.consistency_review = None

        async def run_loop(self, **kw):
            from reasoning.schemas_live import ContextMap
            return ContextMap.model_validate_json(kw["existing_context_map"].model_dump_json())

        async def emit_evidence_sufficiency_narration(self):
            return None

    monkeypatch.setattr("reasoning.live_research.orchestrator.BABLoopEngine", FakeEngine)

    await orch._run_stage_2(state)   # 預設不 suppress

    assert captured["enable_consistency_monitor"] is True
