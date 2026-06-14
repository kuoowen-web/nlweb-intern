"""Tests for _emit_checkpoint evidence_list payload extension.

P0 #5 — stage 1/2 checkpoint payload 帶 evidence_list。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from reasoning.schemas_live import EvidencePoolEntry


def _make_handler_with_sender():
    sender = MagicMock()
    sender.send_message = AsyncMock()
    h = MagicMock()
    h.site = "all"
    h.query_params = {}
    h.message_sender = sender
    h.connection_alive_event = None
    h.request_handler = None
    return h


@pytest.mark.asyncio
async def test_emit_checkpoint_includes_evidence_list():
    """_emit_checkpoint 帶 evidence_list 時，SSE payload 應含 evidence_list 欄位。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler_with_sender()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)

    evidence_list = [
        {"id": 1, "title": "台灣再生能源報告", "url": "https://cna.com.tw/a",
         "source_domain": "cna.com.tw", "published_at": "2024-03-15", "source": "internal"},
        {"id": 2, "title": "AI 背景知識補充", "url": "urn:llm:knowledge:renewable",
         "source_domain": "", "published_at": None, "source": "llm_knowledge"},
    ]

    await orch._emit_checkpoint(stage=1, proposal="研究提案", evidence_list=evidence_list)

    handler.message_sender.send_message.assert_called_once()
    payload = handler.message_sender.send_message.call_args[0][0]
    assert payload["message_type"] == "live_research_checkpoint"
    assert payload["stage"] == 1
    assert "evidence_list" in payload
    assert len(payload["evidence_list"]) == 2
    assert payload["evidence_list"][0]["id"] == 1
    assert payload["evidence_list"][0]["source_domain"] == "cna.com.tw"


@pytest.mark.asyncio
async def test_emit_checkpoint_evidence_list_defaults_to_empty():
    """_emit_checkpoint 不帶 evidence_list 時，payload evidence_list 為空 list（向後兼容）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler_with_sender()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)

    await orch._emit_checkpoint(stage=3, proposal="提案")

    payload = handler.message_sender.send_message.call_args[0][0]
    assert payload.get("evidence_list") == []


@pytest.mark.asyncio
async def test_emit_checkpoint_no_sender_does_not_raise(caplog):
    """message_sender 與 http_handler 皆 None 時：不 raise，且必留 WARN（不可 silent）。

    O5+O5b 升級（2026-06-10 review 收斂點 6）：原版只 assert 不 raise，且 h 為
    MagicMock 時 http_handler 是 auto-attr mock — 新行為下 emit_sse 會 await 其
    write_stream（回傳 MagicMock 非 awaitable → TypeError 被 except 接住）仍綠，
    但語意誤導。故顯式設 http_handler = None 走「兩路皆無」分支 + assert WARN 含
    message_type，防未來有人以「測試綠」認定 silent no-op 可接受。
    """
    import logging
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    h = MagicMock()
    h.message_sender = None
    h.http_handler = None
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    with caplog.at_level(logging.WARNING):
        # Should not raise
        await orch._emit_checkpoint(stage=1, proposal="x", evidence_list=[{"id": 1}])

    assert any("live_research_checkpoint" in r.message for r in caplog.records), (
        f"expected WARN naming dropped message_type, got: "
        f"{[r.message for r in caplog.records]}"
    )


# ============================================================================
# Task 2: _build_topic_evidence_list helper
# ============================================================================

from reasoning.schemas_live import ContextMap, ContextMapTopic


def _make_pool():
    return {
        1: EvidencePoolEntry(
            evidence_id=1, title="再生能源報告", url="https://cna.com.tw/a",
            source_domain="cna.com.tw", published_at="2024-03-15", source="internal",
        ),
        2: EvidencePoolEntry(
            evidence_id=2, title="AI知識", url="urn:llm:knowledge:abc",
            source_domain="", published_at=None, source="llm_knowledge",
        ),
        3: EvidencePoolEntry(
            evidence_id=3, title="外部報導", url="https://web.com/b",
            source_domain="web.com", published_at="2023-11-01", source="web",
        ),
    }


def test_build_topic_evidence_list_returns_correct_subset():
    """_build_topic_evidence_list 依 topic.evidence_ids 過濾 pool，回傳 dict list。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    h = MagicMock()
    h.message_sender = None
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    topic = ContextMapTopic(
        topic_id="t1", name="光電", domain="能源", relevance="core",
        evidence_ids=[1, 3],
    )
    pool = _make_pool()

    result = orch._build_topic_evidence_list(topic, pool)
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert ids == {1, 3}
    assert 2 not in ids


def test_build_topic_evidence_list_includes_required_fields():
    """回傳的每個 dict 包含 id / title / url / source_domain / published_at / source。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    h = MagicMock()
    h.message_sender = None
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    topic = ContextMapTopic(
        topic_id="t1", name="X", domain="Y", relevance="core",
        evidence_ids=[1],
    )
    result = orch._build_topic_evidence_list(topic, _make_pool())
    item = result[0]
    assert item["id"] == 1
    assert item["title"] == "再生能源報告"
    assert item["url"] == "https://cna.com.tw/a"
    assert item["source_domain"] == "cna.com.tw"
    assert item["published_at"] == "2024-03-15"
    assert item["source"] == "internal"


def test_build_topic_evidence_list_skips_phantom_ids():
    """topic.evidence_ids 含 pool 沒有的 ID → 該 ID 被 filter，不插入 None。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    h = MagicMock()
    h.message_sender = None
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    topic = ContextMapTopic(
        topic_id="t1", name="X", domain="Y", relevance="core",
        evidence_ids=[1, 99],  # 99 phantom
    )
    result = orch._build_topic_evidence_list(topic, _make_pool())
    assert len(result) == 1
    assert result[0]["id"] == 1


def test_build_topic_evidence_list_empty_topic_returns_empty():
    """topic.evidence_ids 為空 → 回傳 []，不 raise。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    h = MagicMock()
    h.message_sender = None
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    topic = ContextMapTopic(
        topic_id="t1", name="X", domain="Y", relevance="core",
        evidence_ids=[],
    )
    result = orch._build_topic_evidence_list(topic, _make_pool())
    assert result == []


# ============================================================================
# Task 3: Stage 1 / Stage 2 checkpoint calls 帶 evidence_list
# ============================================================================

from unittest.mock import patch


@pytest.mark.asyncio
async def test_stage1_checkpoint_includes_evidence_list():
    """Stage 1 checkpoint SSE payload 應含 evidence_list，每個 topic 有對應 evidence。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import serialize_evidence_pool

    handler = _make_handler_with_sender()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = False

    pool = _make_pool()
    cm = ContextMap(
        research_question="台灣再生能源",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="光電", domain="能源", relevance="core",
                evidence_ids=[1, 3],
            ),
        ],
    )

    mock_engine = MagicMock()
    mock_engine.run_loop = AsyncMock(return_value=cm)
    mock_engine.initial_context_map = cm
    mock_engine.executed_searches = []
    mock_engine.evidence_pool = pool

    orch._emit_stage_change = AsyncMock()

    with patch("reasoning.live_research.orchestrator.BABLoopEngine", return_value=mock_engine):
        state = LiveResearchStageState()
        await orch._run_stage_1(state, query="再生能源", initial_items=None)

    # Find the live_research_checkpoint call
    all_calls = handler.message_sender.send_message.call_args_list
    checkpoint_calls = [
        c.args[0] for c in all_calls
        if c.args[0].get("message_type") == "live_research_checkpoint"
    ]
    assert len(checkpoint_calls) == 1
    payload = checkpoint_calls[0]
    assert "evidence_list" in payload
    assert isinstance(payload["evidence_list"], list)
    assert len(payload["evidence_list"]) == 2  # topic t1 has evidence_ids [1,3]
    sources = {e["id"] for e in payload["evidence_list"]}
    assert sources == {1, 3}


@pytest.mark.asyncio
async def test_stage2_checkpoint_includes_evidence_list():
    """Stage 2 checkpoint payload 應含 evidence_list（mock_bab path）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import serialize_evidence_pool

    handler = _make_handler_with_sender()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True  # mock_bab path: skips real BAB loop, uses existing ContextMap

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()

    # Seed state with Stage 1 evidence pool (simulate what _run_stage_1 persists)
    pool = _make_pool()
    state.evidence_pool_json = serialize_evidence_pool(pool)

    # Seed state with a context map containing topics (simulate Stage 1 output)
    cm = ContextMap(
        research_question="台灣再生能源",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="光電", domain="能源", relevance="core",
                evidence_ids=[1],
            ),
            ContextMapTopic(
                topic_id="t2", name="外部報導", domain="媒體", relevance="supporting",
                evidence_ids=[3],
            ),
        ],
    )
    state.context_map_json = cm.model_dump_json()
    state.initial_context_map_json = cm.model_dump_json()

    await orch._run_stage_2(state)

    all_calls = handler.message_sender.send_message.call_args_list
    checkpoint_calls = [
        c.args[0] for c in all_calls
        if c.args[0].get("message_type") == "live_research_checkpoint"
    ]
    assert checkpoint_calls, "Stage 2 mock_bab path should emit a checkpoint"
    payload = checkpoint_calls[-1]
    assert "evidence_list" in payload
    assert isinstance(payload["evidence_list"], list)
    ids = {e["id"] for e in payload["evidence_list"]}
    # pool has id 1 and 3; topics reference them
    assert 1 in ids
    assert 3 in ids
