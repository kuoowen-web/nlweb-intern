# test_lr_run_stage_1_seed.py
import asyncio, inspect
from unittest.mock import AsyncMock, MagicMock, patch
from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState


def test_run_stage_1_accepts_seed_params():
    sig = inspect.signature(LiveResearchOrchestrator._run_stage_1)
    assert "seed_evidence_pool" in sig.parameters, "需新增 seed_evidence_pool 參數"
    assert "seed_counter" in sig.parameters, "需新增 seed_counter 參數（與 pool 同傳，防 ID 衝突）"


def test_run_stage_1_passes_seed_to_engine():
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.mock_bab = False
    orch.dry_run = False
    orch.features = {}
    orch.max_bab_iterations = 1
    orch.associator = MagicMock()
    orch.handler = MagicMock()
    orch._emit_stage_change = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._maybe_reset_offline_counters = MagicMock()
    orch._format_initial_items = MagicMock(return_value=None)
    orch._context_map_to_outline = MagicMock(return_value="outline")
    orch._build_topic_evidence_list = MagicMock(return_value=[])
    orch._emit_checkpoint = AsyncMock()
    orch._persist_checkpoint_boundary = AsyncMock()

    state = LiveResearchStageState()
    seed_pool = {1: object(), 2: object()}  # 模擬既有 2 筆 evidence

    captured = {}
    class FakeEngine:
        def __init__(self, **kw):
            captured.update(kw)
            self.initial_context_map = MagicMock()
            self.initial_context_map.model_dump_json = MagicMock(return_value="{}")
            self.executed_searches = []
            # engine 跑完 pool 不變（dry：模擬「沒補到新 evidence」）
            self.evidence_pool = seed_pool
            self.state = None
            self._current_stage = ""
        async def run_loop(self, **kw):
            cm = MagicMock(); cm.model_dump_json = MagicMock(return_value="{}"); cm.topics = []
            return cm

    with patch("reasoning.live_research.orchestrator.BABLoopEngine", FakeEngine), \
         patch("reasoning.live_research.orchestrator.context_map_to_summary", MagicMock(return_value="")), \
         patch("reasoning.live_research.orchestrator.serialize_evidence_pool", MagicMock(return_value='{"serialized": true}')):
        asyncio.run(orch._run_stage_1(state, "q", None,
                                      seed_evidence_pool=seed_pool, seed_counter=2))

    # engine 建構時必須帶 seed 雙參數
    assert captured.get("seed_evidence_pool") is seed_pool
    assert captured.get("seed_counter") == 2


def test_run_stage_1_merges_seed_even_when_engine_pool_degrades():
    """F: engine 補搜全失敗把 pool 退化成空 → final 仍含 seed（defensive merge）。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.mock_bab = False; orch.dry_run = False; orch.features = {}
    orch.max_bab_iterations = 1
    orch.associator = MagicMock(); orch.handler = MagicMock()
    orch._emit_stage_change = AsyncMock(); orch._emit_narration = AsyncMock()
    orch._maybe_reset_offline_counters = MagicMock()
    orch._format_initial_items = MagicMock(return_value=None)
    orch._context_map_to_outline = MagicMock(return_value="outline")
    orch._build_topic_evidence_list = MagicMock(return_value=[])
    orch._emit_checkpoint = AsyncMock(); orch._persist_checkpoint_boundary = AsyncMock()

    state = LiveResearchStageState()
    seed_pool = {1: object(), 2: object()}

    class DegradingEngine:
        def __init__(self, **kw):
            self.initial_context_map = MagicMock()
            self.initial_context_map.model_dump_json = MagicMock(return_value="{}")
            self.executed_searches = []
            self.evidence_pool = {}  # 模擬補搜全失敗 → pool 退化成空
            self.state = None; self._current_stage = ""
        async def run_loop(self, **kw):
            cm = MagicMock(); cm.model_dump_json = MagicMock(return_value="{}"); cm.topics = []
            return cm

    captured_pool = {}
    def fake_serialize(pool):
        captured_pool["pool"] = pool
        return "{}"

    with patch("reasoning.live_research.orchestrator.BABLoopEngine", DegradingEngine), \
         patch("reasoning.live_research.orchestrator.context_map_to_summary", MagicMock(return_value="")), \
         patch("reasoning.live_research.orchestrator.serialize_evidence_pool", fake_serialize):
        asyncio.run(orch._run_stage_1(state, "q", None,
                                      seed_evidence_pool=seed_pool, seed_counter=2))

    # defensive merge：即使 engine.evidence_pool={} 退化，final 仍含 seed 兩筆
    assert set(captured_pool["pool"].keys()) == {1, 2}
