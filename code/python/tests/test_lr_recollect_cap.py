# test_lr_recollect_cap.py
import asyncio
from unittest.mock import AsyncMock, MagicMock
from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import ContextMap


def _orch():
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.features = {}
    orch.handler = MagicMock()
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._run_stage_1 = AsyncMock(side_effect=lambda s, *a, **k: s)
    return orch


def _state():
    s = LiveResearchStageState()
    s.current_stage = 5
    # E (in-house C-1)：evidence_id 必填（EvidencePoolEntry required）。缺 →
    # deserialize_evidence_pool ValidationError 炸在進 reset 前（拿錯 traceback）。
    s.evidence_pool_json = '{"1": {"evidence_id": 1, "url": "u", "title": "t"}}'
    s.context_map_json = ContextMap(research_question="q", topics=[]).model_dump_json()
    return s


def test_dispatch_increments_count_under_cap():
    orch = _orch(); state = _state()
    state.recollect_count = 0
    asyncio.run(orch._dispatch_recollect(state))
    assert state.recollect_count == 1
    orch._run_stage_1.assert_awaited_once()


def test_dispatch_blocks_at_cap():
    orch = _orch(); state = _state()
    state.recollect_count = 2  # = default cap
    asyncio.run(orch._dispatch_recollect(state))
    orch._run_stage_1.assert_not_awaited()
    assert state.recollect_count == 2  # 不再遞增


def test_recollect_cap_default_and_override():
    orch = _orch()
    assert orch._recollect_cap() == 2
    orch.features = {"lr_recollect_cap": 3}
    assert orch._recollect_cap() == 3


def test_old_session_recollect_count_defaults_zero():
    # 舊 session（無欄位）不被誤判 capped
    s = LiveResearchStageState.from_dict({"current_stage": 5})
    assert s.recollect_count == 0
