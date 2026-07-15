"""Tests for LR orchestrator evidence_pool wiring.

Tasks 4, 5, 8 — Stage 1 持久化 / mock_bab fixture / Writer evidence_lookup 傳遞。
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import (
    ContextMap,
    ContextMapTopic,
    EvidencePoolEntry,
    deserialize_evidence_pool,
    serialize_evidence_pool,
)


def _make_handler():
    """Minimal handler stub for LiveResearchOrchestrator."""
    h = MagicMock()
    h.site = "all"
    h.query_params = {}
    h.message_sender = None
    h.connection_alive_event = None
    h.http_handler = None
    return h


def _make_minimal_context_map() -> ContextMap:
    return ContextMap(
        research_question="台灣再生能源發展",
        topics=[
            ContextMapTopic(
                topic_id="T1",
                name="光電發展",
                domain="能源政策",
                description="光電在台灣的發展",
                relevance="core",
                evidence_ids=[1, 2],
            ),
        ],
    )


# ============================================================================
# Task 4: Stage 1 持久化 evidence_pool
# ============================================================================

@pytest.mark.asyncio
async def test_stage_1_writes_evidence_pool():
    """Stage 1 跑完 BAB loop 後 state.evidence_pool_json 應該非空且能 deserialize。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = False  # 直接跑真實路徑（mock 掉 engine）

    state = LiveResearchStageState()

    # Mock engine.run_loop 回傳預設 context_map，並 mock engine.evidence_pool
    expected_pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b.com"),
    }

    mock_engine = MagicMock()
    mock_engine.run_loop = AsyncMock(return_value=_make_minimal_context_map())
    mock_engine.initial_context_map = _make_minimal_context_map()
    mock_engine.executed_searches = ["test query"]
    mock_engine.evidence_pool = expected_pool

    with patch(
        "reasoning.live_research.orchestrator.BABLoopEngine",
        return_value=mock_engine,
    ):
        # Patch SSE / checkpoint emitters to no-ops
        orch._emit_stage_change = AsyncMock()
        orch._emit_checkpoint = AsyncMock()

        state = await orch._run_stage_1(state, query="再生能源", initial_items=None)

    assert state.evidence_pool_json != ""
    restored = deserialize_evidence_pool(state.evidence_pool_json)
    assert set(restored.keys()) == {1, 2}
    assert restored[1].url == "https://a.com"
    assert restored[2].title == "B"


# ============================================================================
# Task 5: mock_bab fixture 載入 evidence_pool
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_loads_fixture_evidence_pool():
    """mock_bab=True → state.evidence_pool_json 含 fixture 內 36 條 evidence (prod session 5767ae4a)。

    2026-06-09: fixture 換為 prod session 5767ae4a 真語料 (36 筆)，舊 21 筆 fake 已棄用。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="台灣再生能源", initial_items=None)

    assert state.evidence_pool_json != ""
    restored = deserialize_evidence_pool(state.evidence_pool_json)
    assert len(restored) == 36
    assert set(restored.keys()) == set(range(1, 37))


@pytest.mark.asyncio
async def test_mock_bab_evidence_ids_cover_fixture_topic_evidence_ids():
    """Fixture topic.evidence_ids 引用範圍 1-36 的 IDs 全部對應到 pool entries。

    2026-06-09: 真實 fixture (session 5767ae4a) context_map topics 引用了
    evidence IDs 1-56（BAB v8 迭代規劃），但 evidence_pool 只有 1-36（實際蒐集到的）。
    IDs 37-56 是 planned（context_map 搜尋計畫）但未實際入池的 future evidence。
    本測試只驗證 pool 範圍內的 IDs（1-36）都存在。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)

    cm = ContextMap.model_validate_json(state.context_map_json)
    used_eids = set()
    for topic in cm.topics:
        used_eids.update(topic.evidence_ids)

    restored = deserialize_evidence_pool(state.evidence_pool_json)
    pool_keys = set(restored.keys())

    # 只驗證 pool 範圍內（1-36）的 topic reference IDs 全部存在
    # IDs > 36 是 BAB planned-but-not-fetched future evidence（context_map search_seeds 計畫）
    in_pool_range = {eid for eid in used_eids if eid <= 36}
    missing = in_pool_range - pool_keys
    assert not missing, f"Topic evidence_ids (1-36 range) 引用了 pool 沒有的 ID: {missing}"


# ============================================================================
# Task 8: Orchestrator 串接 evidence_lookup 進 Writer
# ============================================================================

@pytest.mark.asyncio
async def test_write_section_passes_evidence_lookup_to_writer():
    """_write_section 從 evidence_pool 抽出 topic.evidence_ids 對應子集，傳 compose_section。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)

    topic = ContextMapTopic(
        topic_id="T1",
        name="光電",
        domain="能源",
        description="x",
        relevance="core",
        evidence_ids=[1, 3],  # 只引用 1 和 3
    )

    context_map = ContextMap(
        research_question="再生能源",
        topics=[topic],
    )

    evidence_pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b"),
        3: EvidencePoolEntry(evidence_id=3, title="C", url="https://c"),
    }

    captured = {}

    # Mock WriterAgent.compose_section to capture evidence_lookup kwarg
    async def fake_compose(**kwargs):
        captured.update(kwargs)
        return MagicMock(
            section_title=kwargs["section_title"],
            section_content="content",
            sources_used=[1],
            confidence_level="High",
        )

    with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
        instance = MockWriter.return_value
        instance.compose_section = fake_compose

        section_output, was_corrected = await orch._write_section(
            context_map=context_map,
            topic=topic,
            style_features=None,
            format_specs={},
            evidence_pool=evidence_pool,
        )

    assert "evidence_lookup" in captured
    lookup = captured["evidence_lookup"]
    # 只應包含 topic.evidence_ids 對應的 entries
    assert set(lookup.keys()) == {1, 3}
    assert 2 not in lookup


@pytest.mark.asyncio
async def test_write_section_excludes_phantom_ids():
    """topic.evidence_ids 含 evidence_pool 沒有的 ID → evidence_lookup 不含該 ID（不塞 None）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)

    topic = ContextMapTopic(
        topic_id="T1",
        name="X",
        domain="d",
        description="x",
        relevance="core",
        evidence_ids=[1, 99],  # 99 是 phantom
    )
    context_map = ContextMap(research_question="q", topics=[topic])
    evidence_pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a"),
    }

    captured = {}

    async def fake_compose(**kwargs):
        captured.update(kwargs)
        return MagicMock(
            section_title="X", section_content="c",
            sources_used=[1], confidence_level="High",
        )

    with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
        MockWriter.return_value.compose_section = fake_compose

        section_output, was_corrected = await orch._write_section(
            context_map=context_map,
            topic=topic,
            style_features=None,
            format_specs={},
            evidence_pool=evidence_pool,
        )

    lookup = captured["evidence_lookup"]
    assert set(lookup.keys()) == {1}
    assert 99 not in lookup  # phantom 被 filter 掉，不是 None entry
