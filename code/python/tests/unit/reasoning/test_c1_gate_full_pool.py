"""P2 W10：C-1 gate 改判全局模型（§0 #5/#7 + R2-3 intro/conclusion guard）.

入口 gate：只擋「pool 完全空」（render 前能判的），保留 _is_intro_or_conclusion guard。
post-render gate：改判「writer view + narrative 都實質空」才擋（narrative 空 ≠ pool 空）。
"""
import pytest
from unittest.mock import patch, MagicMock


def _build_orch(monkeypatch):
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    handler = MagicMock(query_params={})
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False

    async def fake_compose(self, **kw):
        return LiveWriterSectionOutput(
            section_title=kw["section_title"], section_content="c",
            sources_used=[1], confidence_level="Medium",
            narration="x", chapter_summary="s",
        )
    monkeypatch.setattr(
        "reasoning.agents.writer.WriterAgent.compose_section", fake_compose)
    return orch


def _make(monkeypatch, *, pool_ids, planned, role="body", usage_eids=()):
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, EvidencePoolEntry, BookOutline, ChapterPlan,
        GroundedClaim,
    )
    from reasoning.live_research.stage_state import LiveResearchStageState
    orch = _build_orch(monkeypatch)
    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=list(pool_ids))],
    )
    state = LiveResearchStageState()
    state.evidence_usage = {
        e: [GroundedClaim(claim=f"c{e}", reasoning_type="induction",
                          confidence="high", source_topic="t1",
                          source_iteration=1).model_dump()]
        for e in usage_eids
    }
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url="u",
                                 snippet="s") for i in pool_ids}
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="前言", brief="x",
                    planned_evidence_ids=[], role="intro"),
        ChapterPlan(chapter_index=1, title="本章", brief="y",
                    planned_evidence_ids=list(planned), role=role),
    ], overall_arc="x", redundancy_warnings=[])
    return orch, cm, state, pool, book_outline


@pytest.mark.asyncio
async def test_c1_gate_does_not_block_when_pool_nonempty(monkeypatch):
    """planned 空但 pool 非空（有 claim）→ 不擋（writer 讀全 pool）。"""
    orch, cm, state, pool, book_outline = _make(
        monkeypatch, pool_ids=[1, 2, 3], planned=[], usage_eids=[1])
    out, _ = await orch._write_section(
        context_map=cm, topic={"name": "本章", "outline": "y"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=1, all_evidence_ids=[1, 2, 3],
        book_outline=book_outline, current_chapter_index=1, state=state,
    )
    assert getattr(out, "status", None) != "blocked_no_evidence"


@pytest.mark.asyncio
async def test_c1_gate_blocks_only_when_pool_truly_empty(monkeypatch):
    """pool 完全空 → 入口 gate 擋。"""
    orch, cm, state, pool, book_outline = _make(
        monkeypatch, pool_ids=[1, 2, 3], planned=[])
    out, _ = await orch._write_section(
        context_map=cm, topic={"name": "本章", "outline": "y"},
        style_features=None, format_specs={}, evidence_pool={},
        chapter_index=1, all_evidence_ids=[],
        book_outline=book_outline, current_chapter_index=1, state=state,
    )
    assert getattr(out, "status", None) == "blocked_no_evidence"


@pytest.mark.asyncio
async def test_c1_gate_intro_conclusion_not_blocked_when_pool_empty(monkeypatch):
    """intro/conclusion + pool 空 → 不擋（R2-3 guard）。chapter_index=0 = intro 章。"""
    orch, cm, state, pool, book_outline = _make(
        monkeypatch, pool_ids=[1], planned=[], role="body")
    # current_chapter_index=0 是 intro（book_outline.chapters[0].role="intro"）
    out, _ = await orch._write_section(
        context_map=cm, topic={"name": "前言", "outline": "x"},
        style_features=None, format_specs={}, evidence_pool={},
        chapter_index=0, all_evidence_ids=[],
        book_outline=book_outline, current_chapter_index=0, state=state,
    )
    assert getattr(out, "status", None) != "blocked_no_evidence"


@pytest.mark.asyncio
async def test_post_render_gate_blocks_when_pool_present_but_all_empty(monkeypatch):
    """pool 有 entry 但無 title/snippet 且無 claim → writer view + narrative 都空 → 擋。"""
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, EvidencePoolEntry, BookOutline, ChapterPlan,
    )
    from reasoning.live_research.stage_state import LiveResearchStageState
    orch = _build_orch(monkeypatch)
    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=[1])],
    )
    state = LiveResearchStageState()
    state.evidence_usage = {}   # 無 claim → narrative 空
    # entry 非空（pool 有 key）繞入口 gate；但 entry 無 title/snippet → view/narrative 都空
    pool = {1: EvidencePoolEntry(evidence_id=1, title="", url="", snippet="")}
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="前言", brief="x",
                    planned_evidence_ids=[], role="intro"),
        ChapterPlan(chapter_index=1, title="本章", brief="y",
                    planned_evidence_ids=[1], role="body"),
    ], overall_arc="x", redundancy_warnings=[])
    out, _ = await orch._write_section(
        context_map=cm, topic={"name": "本章", "outline": "y"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=1, all_evidence_ids=[1],
        book_outline=book_outline, current_chapter_index=1, state=state,
    )
    assert getattr(out, "status", None) == "blocked_no_evidence"
