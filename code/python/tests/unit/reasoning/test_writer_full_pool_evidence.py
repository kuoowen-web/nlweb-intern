"""P2 全局 evidence 模型：writer evidence_lookup 改讀全 pool（W2/W3）.

W2：analyst_citations / planned 降為「優先 tier 排序提示」，不再界定 writer 可見集。
W3：evidence_lookup = dict(evidence_pool) 全 pool（與 Critic 對齊）。
本檔鎖「analyst_citations（優先 tier）與 evidence_lookup（writer 可見集）解耦」。
"""
import pytest
from unittest.mock import patch, MagicMock


def _build_orch_and_capture(monkeypatch):
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock(query_params={})
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    captured = {}
    from reasoning.schemas_live import LiveWriterSectionOutput

    async def fake_compose(self, **kw):
        captured.update(kw)
        return LiveWriterSectionOutput(
            section_title=kw["section_title"], section_content="c",
            sources_used=[2], confidence_level="Medium",
            narration="x", chapter_summary="s",
        )
    monkeypatch.setattr(
        "reasoning.agents.writer.WriterAgent.compose_section", fake_compose)
    return orch, captured


def _scenario(monkeypatch):
    """chapter[1].planned_evidence_ids=[1]，pool={1,2,3}。"""
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, EvidencePoolEntry, BookOutline, ChapterPlan,
        GroundedClaim,
    )
    from reasoning.live_research.stage_state import LiveResearchStageState
    orch, captured = _build_orch_and_capture(monkeypatch)
    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=[1, 2, 3])],
    )
    state = LiveResearchStageState()
    # 讓 narrative 非空（繞 post-render gate），用 pool 內 claim
    state.evidence_usage = {
        1: [GroundedClaim(claim="claim1", reasoning_type="induction",
                          confidence="high", source_topic="t1",
                          source_iteration=1).model_dump()],
    }
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url="u",
                                 snippet="s") for i in (1, 2, 3)}
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="前言", brief="x",
                    planned_evidence_ids=[1], role="intro"),
        ChapterPlan(chapter_index=1, title="本章", brief="y",
                    planned_evidence_ids=[1], role="body"),
    ], overall_arc="x", redundancy_warnings=[])
    return orch, captured, cm, state, pool, book_outline


@pytest.mark.asyncio
async def test_writer_evidence_lookup_includes_full_pool(monkeypatch):
    orch, captured, cm, state, pool, book_outline = _scenario(monkeypatch)
    await orch._write_section(
        context_map=cm, topic={"name": "本章", "outline": "y"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=1, all_evidence_ids=[1, 2, 3],
        book_outline=book_outline, current_chapter_index=1, state=state,
    )
    # W3：writer 拿得到 planned 外但 pool 內的 evidence（全 pool）
    assert set(captured["evidence_lookup"].keys()) == {1, 2, 3}


@pytest.mark.asyncio
async def test_analyst_citations_no_longer_bounds_writer_visible_set(monkeypatch):
    orch, captured, cm, state, pool, book_outline = _scenario(monkeypatch)
    await orch._write_section(
        context_map=cm, topic={"name": "本章", "outline": "y"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=1, all_evidence_ids=[1, 2, 3],
        book_outline=book_outline, current_chapter_index=1, state=state,
    )
    # W2/W3 解耦：evidence_lookup 全 pool；analyst_citations 仍 = planned 優先 tier
    assert set(captured["evidence_lookup"].keys()) == {1, 2, 3}
    assert captured["analyst_citations"] == [1]
