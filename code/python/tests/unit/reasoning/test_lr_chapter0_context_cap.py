"""LR chapter-0 / evidence aggregate cap helper 單元測 + _write_section 整合測。

驗 cap 策略：planned_evidence_ids 全保留 + remaining stratified 均勻抽樣（不頭部
截斷、不 topic starvation）+ char budget 為主 binding cap（N=80 次要 backstop）。
本檔驗的是 writer evidence_lookup 這條鏈的 cap；critic chapter_evidence_text
自 模塊1 A.2（43bd5c61）起改為全 pool 視圖、受 R2 GROUNDING_VIEW_CHAR_BUDGET
管轄，與本 cap 不同源（不再「同步受限」）。
"""
import pytest
from reasoning.schemas_live import EvidencePoolEntry


def _pool(n, snippet_len=200):
    """造 n 筆 evidence_pool，evidence_id 1..n，snippet 各 snippet_len 字。"""
    return {
        i: EvidencePoolEntry(
            evidence_id=i,
            title=f"T{i}",
            url=f"http://e/{i}",
            snippet="x" * snippet_len,
        )
        for i in range(1, n + 1)
    }


def test_cap_keeps_all_planned_evidence():
    """planned_evidence_ids ∩ pool 必全保留（不抽樣、不截斷）。

    防 topic starvation：planned 是 LLM 對該章做的 topic assignment，
    各 topic 都該有代表 → 全保留。
    """
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    pool = _pool(60, snippet_len=50)
    # planned 故意挑分散在頭中尾的 id（模擬多 topic），全都要被保留
    planned = [2, 30, 58]
    citations = list(range(1, 61))
    capped = _cap_evidence_citations(citations, pool, planned_evidence_ids=planned)
    assert set(planned) <= set(capped), "planned evidence 必須全保留"


def test_cap_backfill_is_stratified_not_head_truncation():
    """remaining 補位用均勻 stratified 抽樣橫跨時間軸，不做頭部截斷。

    這是兩家外部模型收斂反對的核心：頭部截斷 = topic starvation。
    驗：補進來的 id 跨越頭中尾（不是只取 remaining 最小那一段）。
    """
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    # 200 筆 realistic snippet（>200 → 截到 200，每筆 ~303 字）→ char budget 先 bind
    pool = _pool(200, snippet_len=300)
    citations = list(range(1, 201))
    capped = _cap_evidence_citations(citations, pool, planned_evidence_ids=[])
    # 反 starvation：stratified 必橫跨時間軸 → 含尾段 id（頭部截斷會讓 max 落在前段）
    assert max(capped) > 150, (
        f"stratified 抽樣應橫跨時間軸取到尾段 id，"
        f"頭部截斷會讓 max 落前段；實得 max={max(capped)}"
    )
    assert min(capped) <= 5, "也應含頭段（覆蓋頭中尾）"


def test_cap_char_budget_is_primary_binding_cap():
    """char budget 為主 binding cap：realistic snippet 下，由字數（非 N=80）先 drop。

    驗 (a) 累計字數 ≤ MAX_EVIDENCE_CHARS（+1 筆 overshoot 保底）；
       (b) 保留筆數 < MAX_EVIDENCE_ITEMS（證明 char budget 比 N backstop 先觸發）。
    """
    from reasoning.live_research.orchestrator import (
        _cap_evidence_citations, MAX_EVIDENCE_CHARS, MAX_EVIDENCE_ITEMS,
        _EVIDENCE_SNIPPET_CHARS, _EVIDENCE_OVERHEAD_PER_ITEM,
    )
    # 200 筆（> N=80），每筆 snippet 截到 200 → ~303 字；80*303≈24240>20000 → char 先 bind
    pool = _pool(200, snippet_len=600)
    citations = list(range(1, 201))
    capped = _cap_evidence_citations(citations, pool, planned_evidence_ids=[])
    total = sum(
        min(len(pool[e].snippet), _EVIDENCE_SNIPPET_CHARS)
        + len(pool[e].title) + _EVIDENCE_OVERHEAD_PER_ITEM
        for e in capped
    )
    per_item = _EVIDENCE_SNIPPET_CHARS + 3 + _EVIDENCE_OVERHEAD_PER_ITEM
    assert total <= MAX_EVIDENCE_CHARS + per_item, (
        f"char budget 應為 binding cap；累計 {total} 超 {MAX_EVIDENCE_CHARS}"
    )
    # char budget 先觸發 → 保留筆數 < N backstop（~66 筆 < 80）
    assert len(capped) < MAX_EVIDENCE_ITEMS, (
        f"char budget 應比 N={MAX_EVIDENCE_ITEMS} 先 drop；實得 {len(capped)} 筆"
    )


def test_cap_planned_priority_over_backfill_under_budget():
    """budget 緊時，planned 先佔額度，remaining 才補剩餘 → planned 不被擠掉。"""
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    pool = _pool(50, snippet_len=400)
    planned = [49, 50]  # 故意放尾段，驗它們不因 id 大而被截掉
    citations = list(range(1, 51))
    capped = _cap_evidence_citations(citations, pool, planned_evidence_ids=planned)
    assert 49 in capped and 50 in capped, "planned 必須優先於 remaining 保留"


def test_cap_planned_respects_char_budget_with_floor():
    """Round-2 Must-Fix #1：planned 自身超 char budget 時，受 budget drop 但保底 ≥1 筆。

    現實 bomb：~80 筆 planned × ~300 字 = ~24000 > 20000。planned 若「全保留不檢查
    budget」會 context_length_exceeded（爆窗保證被打破）。驗：
      (a) 累計字數不超 MAX_EVIDENCE_CHARS（+1 筆 overshoot 容忍：保底第 1 筆即使超標也留）；
      (b) 至少保留 1 筆（grounding 不整章消失）。
    """
    from reasoning.live_research.orchestrator import (
        _cap_evidence_citations, MAX_EVIDENCE_CHARS,
        _EVIDENCE_SNIPPET_CHARS, _EVIDENCE_OVERHEAD_PER_ITEM,
    )
    # 100 筆全進 planned，每筆 snippet 截到 200 → ~303 字；100*303≈30300 > 20000
    pool = _pool(100, snippet_len=600)
    planned = list(range(1, 101))
    citations = list(range(1, 101))
    capped = _cap_evidence_citations(citations, pool, planned_evidence_ids=planned)
    assert len(capped) >= 1, "保底：至少留 1 筆 planned，grounding 不消失"
    total = sum(
        min(len(pool[e].snippet), _EVIDENCE_SNIPPET_CHARS)
        + len(pool[e].title) + _EVIDENCE_OVERHEAD_PER_ITEM
        for e in capped
    )
    per_item = _EVIDENCE_SNIPPET_CHARS + 3 + _EVIDENCE_OVERHEAD_PER_ITEM
    assert total <= MAX_EVIDENCE_CHARS + per_item, (
        f"planned 也必須受 char budget（防爆窗）；累計 {total} 遠超 "
        f"{MAX_EVIDENCE_CHARS} 代表 planned 沒受 budget"
    )
    # 證明確實 drop 了大量 planned（不是全 100 筆塞進去）
    assert len(capped) < len(planned), "planned 超 budget 必須被 drop（非全保留）"


def test_cap_step2_continue_preserves_tail_topic_under_underestimate():
    """Round-2 Must-Fix #2：Step 2 用 continue（非 break）→ avg_item 低估、cumulative
    中途撞頂時，尾段 topic 仍被涵蓋（不被斬尾）。

    構造：少數頭段超長 snippet（拉高實際 cumulative，讓粗估 needed 偏多、精確累加
    中途撞頂）+ 多數尾段短 snippet（撞頂後仍塞得下）。若 Step 2 是 break → 尾段全砍、
    max 落前段；用 continue → 尾段短的仍被塞進 → max 觸及尾段。
    """
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    pool = {}
    # 頭段 1..10：超長 snippet（截到 200，但拉高 cumulative 讓估算偏差）
    for i in range(1, 11):
        pool[i] = EvidencePoolEntry(evidence_id=i, title="T" * 50,
                                    url="u", snippet="x" * 300)
    # 尾段 11..200：極短 snippet（撞頂後仍能塞）
    for i in range(11, 201):
        pool[i] = EvidencePoolEntry(evidence_id=i, title="T",
                                    url="u", snippet="y" * 5)
    citations = list(range(1, 201))
    capped = _cap_evidence_citations(citations, pool, planned_evidence_ids=[])
    # continue 行為：撞頂後跳過超標單筆、繼續掃 → 尾段短 snippet 仍被抽中
    assert max(capped) > 150, (
        f"continue 應讓尾段（短 snippet）topic 仍被涵蓋；若是 break 會斬尾使 "
        f"max 落前段。實得 max={max(capped)}"
    )


def test_cap_noop_for_small_set():
    """小集合（body 章典型）→ cap 不觸發，全保留（輸出穩定排序）。"""
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    pool = _pool(5, snippet_len=50)
    capped = _cap_evidence_citations([3, 1, 2], pool, planned_evidence_ids=[3, 1, 2])
    assert set(capped) == {1, 2, 3}  # 全保留


def test_cap_empty_returns_empty():
    """空 citations → 空（不爆）。"""
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    assert _cap_evidence_citations([], _pool(3), planned_evidence_ids=[]) == []


def test_cap_skips_missing_pool_entries():
    """citations / planned 含 pool 沒有的 ID（phantom）→ 跳過、不計入、不爆。"""
    from reasoning.live_research.orchestrator import _cap_evidence_citations
    pool = _pool(3, snippet_len=50)
    capped = _cap_evidence_citations([1, 999, 2], pool, planned_evidence_ids=[999])
    assert set(capped) == {1, 2}  # 999 不在 pool → 略過


@pytest.mark.asyncio
async def test_chapter0_large_evidence_capped_stratified_in_writer_and_critic(monkeypatch):
    """chapter 0 raw union（book_outline=None，無 planned）拿 119 筆 → writer 的
    analyst_citations 被 cap 到 <=80，且 stratified 抽樣**橫跨時間軸**（含尾段 id，
    非頭部截斷）；evidence_lookup 同步只含 capped IDs（critic 同源不爆窗）。"""
    from unittest.mock import patch, MagicMock
    from reasoning.live_research.orchestrator import (
        LiveResearchOrchestrator, MAX_EVIDENCE_ITEMS,
    )
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, EvidencePoolEntry,
        LiveWriterSectionOutput,
    )

    handler = MagicMock(query_params={})
    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=list(range(1, 120)))],
    )
    state = LiveResearchStageState()
    state.evidence_usage = {}
    pool = {
        i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url=f"http://e/{i}",
                             snippet="y" * 60)  # snippet ~163 字/筆 → char budget 主導
        for i in range(1, 120)
    }

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False

    captured = {}

    async def fake_compose_section(self, **kw):
        captured.update(kw)
        return LiveWriterSectionOutput(
            section_title=kw["section_title"], section_content="content",
            sources_used=[1], confidence_level="Medium",
            narration="x", chapter_summary="s",
        )
    monkeypatch.setattr(
        "reasoning.agents.writer.WriterAgent.compose_section",
        fake_compose_section,
    )

    # book_outline=None → 觸發 raw union path（最壞情況：chapter 0 = 全 119 筆，無 planned）
    await orch._write_section(
        context_map=cm, topic={"name": "前言", "outline": "intro"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=0, all_evidence_ids=list(range(1, 120)),
        book_outline=None, current_chapter_index=0, state=state,
    )

    # writer 收到的 analyst_citations 已被 cap
    cap_list = captured["analyst_citations"]
    assert len(cap_list) <= MAX_EVIDENCE_ITEMS
    # 反 topic starvation：stratified 抽樣必須橫跨時間軸 → 含頭段也含尾段
    assert min(cap_list) <= 5, "應抽到頭段 id"
    assert max(cap_list) > 60, (
        f"應 stratified 抽到尾段 id（頭部截斷會讓 max<=40），實得 max={max(cap_list)}"
    )
    # evidence_lookup（writer 看的真實來源）也同步只含 capped IDs（→ critic chapter_evidence_text 同源受限）
    assert set(captured["evidence_lookup"].keys()) <= set(cap_list)


@pytest.mark.asyncio
async def test_body_chapter_small_evidence_not_capped(monkeypatch):
    """body 章 planned_evidence_ids 小集合 → cap no-op，analyst_citations 不變。"""
    from unittest.mock import patch, MagicMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, EvidencePoolEntry, BookOutline, ChapterPlan,
        LiveWriterSectionOutput, GroundedClaim,
    )

    handler = MagicMock(query_params={})
    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=[1, 2])],
    )
    state = LiveResearchStageState()
    state.evidence_usage = {
        2: [GroundedClaim(claim="C", reasoning_type="induction",
                          confidence="high", source_topic="t1",
                          source_iteration=1).model_dump()],
    }
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url="u",
                                 snippet="s") for i in (1, 2)}
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="前言", brief="x",
                    planned_evidence_ids=[1], role="intro"),
        ChapterPlan(chapter_index=1, title="本章", brief="y",
                    planned_evidence_ids=[2], role="body"),
    ], overall_arc="x", redundancy_warnings=[])

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    captured = {}

    async def fake_compose(self, **kw):
        captured.update(kw)
        return LiveWriterSectionOutput(
            section_title=kw["section_title"], section_content="c",
            sources_used=[2], confidence_level="Medium",
            narration="x", chapter_summary="s",
        )
    monkeypatch.setattr(
        "reasoning.agents.writer.WriterAgent.compose_section", fake_compose)

    await orch._write_section(
        context_map=cm, topic={"name": "本章", "outline": "y"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=1, all_evidence_ids=[1, 2],
        book_outline=book_outline, current_chapter_index=1, state=state,
    )
    # body 章 planned=[2]，cap no-op
    assert captured["analyst_citations"] == [2]


@pytest.mark.asyncio
async def test_chapter_large_planned_all_preserved_no_starvation(monkeypatch):
    """章節 planned_evidence_ids 含分散在頭中尾的多 topic id（>budget 邊緣）→
    planned 全保留，不因 id 大被截掉（防 topic starvation 的整合驗證）。"""
    from unittest.mock import patch, MagicMock
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, EvidencePoolEntry, BookOutline, ChapterPlan,
        LiveWriterSectionOutput,
    )

    handler = MagicMock(query_params={})
    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=list(range(1, 101)))],
    )
    state = LiveResearchStageState()
    # 設置 evidence_usage 讓 relevant_findings 非空（繞過第二層 gate）
    from reasoning.schemas_live import GroundedClaim
    state.evidence_usage = {
        2: [GroundedClaim(claim="頭段 topic claim", reasoning_type="induction",
                          confidence="high", source_topic="t1",
                          source_iteration=1).model_dump()],
        50: [GroundedClaim(claim="中段 topic claim", reasoning_type="deduction",
                           confidence="high", source_topic="t1",
                           source_iteration=2).model_dump()],
        99: [GroundedClaim(claim="尾段 topic claim", reasoning_type="analogy",
                           confidence="medium", source_topic="t1",
                           source_iteration=3).model_dump()],
    }
    pool = {
        i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url="u",
                             snippet="z" * 100)
        for i in range(1, 101)
    }
    # planned 故意挑頭(2)、中(50)、尾(99) — 模擬 3 個 topic 各有代表
    planned_ids = [2, 50, 99]
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="章", brief="x",
                    planned_evidence_ids=planned_ids, role="intro"),
    ], overall_arc="x", redundancy_warnings=[])

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    captured = {}

    async def fake_compose(self, **kw):
        captured.update(kw)
        return LiveWriterSectionOutput(
            section_title=kw["section_title"], section_content="c",
            sources_used=[2], confidence_level="Medium",
            narration="x", chapter_summary="s",
        )
    monkeypatch.setattr(
        "reasoning.agents.writer.WriterAgent.compose_section", fake_compose)

    await orch._write_section(
        context_map=cm, topic={"name": "章", "outline": "x"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=0, all_evidence_ids=list(range(1, 101)),
        book_outline=book_outline, current_chapter_index=0, state=state,
    )
    cap_list = captured["analyst_citations"]
    # 三個 topic 代表（頭中尾）都必須保留，無一被 starve
    assert set(planned_ids) <= set(cap_list), (
        f"planned {planned_ids} 必須全保留，實得 {sorted(cap_list)}"
    )
