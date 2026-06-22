"""Tests for Writer evidence_lookup parameter wiring (Tasks 6, 7).

Task 6 — compose_section 介面新增 evidence_lookup 參數
Task 7 — build_section_compose_prompt 注入 evidence 對照表
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.schemas_live import EvidencePoolEntry


# ============================================================================
# Task 6: compose_section accepts evidence_lookup parameter
# ============================================================================

@pytest.mark.asyncio
async def test_compose_section_accepts_evidence_lookup():
    """介面新增 evidence_lookup（default None）不 break 既有 caller。"""
    from reasoning.agents.writer import WriterAgent

    handler = MagicMock()
    writer = WriterAgent(handler=handler, timeout=10)

    # Mock the underlying LLM call_llm_validated
    mock_result = MagicMock()
    mock_result.sources_used = [1]
    mock_result.confidence_level = "High"
    mock_result.section_content = "stub"
    writer.call_llm_validated = AsyncMock(return_value=(mock_result, 0, False))

    # Call without evidence_lookup (既有 caller 行為)
    result = await writer.compose_section(
        section_title="X",
        section_outline="outline",
        relevant_findings="findings",
        analyst_citations=[1, 2],
    )
    assert result is mock_result

    # Call WITH evidence_lookup
    lookup = {1: EvidencePoolEntry(evidence_id=1, title="T", url="https://u.com")}
    result2 = await writer.compose_section(
        section_title="X",
        section_outline="outline",
        relevant_findings="findings",
        analyst_citations=[1, 2],
        evidence_lookup=lookup,
    )
    assert result2 is mock_result


@pytest.mark.asyncio
async def test_compose_section_passes_lookup_to_prompt_builder():
    """Mock prompt_builder.build_section_compose_prompt，assert evidence_lookup 被傳遞。"""
    from reasoning.agents.writer import WriterAgent

    handler = MagicMock()
    writer = WriterAgent(handler=handler, timeout=10)
    writer.call_llm_validated = AsyncMock(
        return_value=(MagicMock(sources_used=[], confidence_level="Medium", section_content=""), 0, False)
    )

    lookup = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b.com"),
    }

    captured = {}
    original = writer.prompt_builder.build_section_compose_prompt

    def spy(**kwargs):
        captured.update(kwargs)
        return original(**{k: v for k, v in kwargs.items() if k != "evidence_lookup"})

    with patch.object(writer.prompt_builder, "build_section_compose_prompt", side_effect=spy):
        await writer.compose_section(
            section_title="X",
            section_outline="o",
            relevant_findings="f",
            analyst_citations=[1, 2],
            evidence_lookup=lookup,
        )

    assert "evidence_lookup" in captured
    assert captured["evidence_lookup"] == lookup


@pytest.mark.asyncio
async def test_compose_section_passes_writer_view_to_prompt_builder():
    """P2 W7：compose_section 接受並透傳 writer_evidence_view 給 prompt builder。"""
    from reasoning.agents.writer import WriterAgent

    handler = MagicMock()
    writer = WriterAgent(handler=handler, timeout=10)
    writer.call_llm_validated = AsyncMock(
        return_value=(MagicMock(sources_used=[], confidence_level="Medium",
                                section_content=""), 0, False)
    )

    captured = {}

    def fake_build(**kw):
        captured.update(kw)
        return "PROMPT"

    with patch.object(writer.prompt_builder, "build_section_compose_prompt",
                      side_effect=fake_build):
        await writer.compose_section(
            section_title="X", section_outline="o", relevant_findings="f",
            analyst_citations=[1], evidence_lookup={1: EvidencePoolEntry(
                evidence_id=1, title="T", url="u")},
            writer_evidence_view="VIEW-ALL-POOL",
        )

    assert captured["writer_evidence_view"] == "VIEW-ALL-POOL"


def test_build_section_compose_prompt_injects_writer_view():
    """P2 W7：writer_evidence_view 非空 → 注入 prompt（全 pool grounding 視圖段）。"""
    from reasoning.prompts.writer import WriterPromptBuilder
    prompt = WriterPromptBuilder().build_section_compose_prompt(
        section_title="X", section_outline="o", relevant_findings="f",
        analyst_citations=[1],
        evidence_lookup={1: EvidencePoolEntry(evidence_id=1, title="T", url="u",
                                              snippet="s")},
        writer_evidence_view="### [9] 全 pool 來源九\n台積電擴廠案",
    )
    assert "台積電擴廠案" in prompt          # view 內容注入
    # None → 不注入（向後相容）
    prompt_none = WriterPromptBuilder().build_section_compose_prompt(
        section_title="X", section_outline="o", relevant_findings="f",
        analyst_citations=[1],
        evidence_lookup={1: EvidencePoolEntry(evidence_id=1, title="T", url="u",
                                              snippet="s")},
    )
    assert "台積電擴廠案" not in prompt_none


# ============================================================================
# Task 7: prompt body 注入 evidence_lookup 對照表
# ============================================================================

def _make_lookup():
    return {
        1: EvidencePoolEntry(
            evidence_id=1,
            title="台灣光電發展",
            url="https://example.com/1",
            source_domain="example.com",
            snippet="光電裝置容量近年成長迅速...",
        ),
        2: EvidencePoolEntry(
            evidence_id=2,
            title="離岸風電進度",
            url="https://example.com/2",
            source_domain="example.com",
            snippet="離岸風場建置面臨多項挑戰...",
        ),
    }


def test_prompt_contains_evidence_lookup_block():
    """給定 evidence_lookup → prompt 含「白名單 ID 對應的真實來源」段落。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="光電",
        section_outline="光電發展",
        relevant_findings="findings",
        analyst_citations=[1, 2],
        citation_format="numeric",
        evidence_lookup=_make_lookup(),
    )
    assert "白名單 ID 對應的真實來源" in prompt


def test_prompt_includes_url_and_title():
    """Prompt 內含每個 entry 的 title + URL。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="光電",
        section_outline="o",
        relevant_findings="f",
        analyst_citations=[1, 2],
        citation_format="numeric",
        evidence_lookup=_make_lookup(),
    )
    assert "台灣光電發展" in prompt
    assert "https://example.com/1" in prompt
    assert "離岸風電進度" in prompt
    assert "https://example.com/2" in prompt


def test_prompt_omits_block_when_lookup_empty():
    """空 lookup 或 None → 不出現該段落。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    p1 = builder.build_section_compose_prompt(
        section_title="X",
        section_outline="o",
        relevant_findings="f",
        analyst_citations=[1, 2],
        citation_format="numeric",
        evidence_lookup=None,
    )
    assert "白名單 ID 對應的真實來源" not in p1

    p2 = builder.build_section_compose_prompt(
        section_title="X",
        section_outline="o",
        relevant_findings="f",
        analyst_citations=[1, 2],
        citation_format="numeric",
        evidence_lookup={},
    )
    assert "白名單 ID 對應的真實來源" not in p2


# ============================================================================
# Plan 2 Phase 3: Chapter override evidence allocation + writer prompt 提示
# ============================================================================


def test_prompt_replaces_chapter_override_notice_with_grounding_discipline():
    """Track A (sprint 2026-05-28): chapter_override_notice 綠燈整段移除,
    改為 grounding discipline。is_chapter_override=True + 空 citations →
    「本章資料不足」紀律 + 禁止硬塞 [N] (取代原「可以使用敘事性、總結性語句」綠燈)。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="國內案例",
        section_outline="台灣案例",
        relevant_findings="",
        analyst_citations=[],
        citation_format="numeric",
        is_chapter_override=True,
    )
    # 原綠燈措辭整段移除
    assert "使用者自訂結構" not in prompt
    assert "可以使用敘事性" not in prompt
    assert "不要強行加 [N]" not in prompt
    # 新紀律就位
    assert "本章資料不足" in prompt
    assert "禁止硬塞" in prompt or "不可輸出任何" in prompt


def test_prompt_omits_chapter_override_notice_when_flag_but_citations_present():
    """is_chapter_override=True 但 analyst_citations 非空 → 不加提示語（第一章拿 union evidence）。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="前言",
        section_outline="動機",
        relevant_findings="findings",
        analyst_citations=[1, 2, 3],
        citation_format="numeric",
        is_chapter_override=True,
    )
    # 第一章拿到所有 evidence_ids → 不需要「無 [N]」提示
    assert "使用者自訂結構" not in prompt


def test_prompt_omits_chapter_override_notice_when_flag_false():
    """is_chapter_override 預設 False → 既有行為 (core_topics path) 不變。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="X",
        section_outline="o",
        relevant_findings="f",
        analyst_citations=[],
        citation_format="numeric",
    )
    assert "使用者自訂結構" not in prompt


# ============================================================================
# Plan 4 Phase 3: outline_list + previous_chapter_summary block injection
# ============================================================================


def _make_book_outline():
    from reasoning.schemas_live import BookOutline, ChapterPlan
    return BookOutline(
        chapters=[
            ChapterPlan(
                chapter_index=0, title="前言", brief="動機與目的",
                target_word_count=500, role="intro", transition_hint="",
                planned_evidence_ids=[1],
            ),
            ChapterPlan(
                chapter_index=1, title="案例", brief="台灣案例聚焦",
                target_word_count=1500, role="body", transition_hint="承接前言",
                planned_evidence_ids=[1, 2],
            ),
            ChapterPlan(
                chapter_index=2, title="結論", brief="政策建議與展望",
                target_word_count=500, role="conclusion", transition_hint="承接案例分析",
                planned_evidence_ids=[],
            ),
        ],
        overall_arc="動機 → 案例 → 政策建議",
        redundancy_warnings=["第2、第3章都會碰到政策面，請 writer 注意分工"],
    )


def test_section_compose_prompt_includes_outline_list_when_book_outline_present():
    """book_outline 非 None → prompt 含「全書章節結構」block 列所有章節 + 目前位置標記。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    outline = _make_book_outline()
    prompt = builder.build_section_compose_prompt(
        section_title="案例",
        section_outline="台灣案例",
        relevant_findings="[1] data",
        analyst_citations=[1, 2],
        citation_format="numeric",
        book_outline=outline,
        current_chapter_index=1,
        previous_chapter_summary="前言摘要：研究動機是探討台灣綠能政策。",
    )
    assert "全書章節結構" in prompt
    assert "前言" in prompt
    assert "案例" in prompt
    assert "結論" in prompt
    # 目前位置標記（第 2 章 / 共 3 章）
    assert "第 2 章" in prompt and "共 3 章" in prompt
    # redundancy warning 應出現
    assert "第2、第3章都會碰到政策面" in prompt
    # 前一章摘要 block
    assert "前一章摘要" in prompt
    assert "前言摘要：研究動機是探討台灣綠能政策。" in prompt


def test_section_compose_prompt_first_chapter_omits_prev_summary_block():
    """current_chapter_index=0 + previous_chapter_summary='' → 不出現前一章摘要 block。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    outline = _make_book_outline()
    prompt = builder.build_section_compose_prompt(
        section_title="前言",
        section_outline="動機",
        relevant_findings="",
        analyst_citations=[1],
        citation_format="numeric",
        book_outline=outline,
        current_chapter_index=0,
        previous_chapter_summary="",
    )
    # 第一章仍應看到全書 outline
    assert "全書章節結構" in prompt
    # 但不應看到前一章摘要 block
    assert "前一章摘要" not in prompt


def test_section_compose_prompt_omits_outline_block_when_no_outline():
    """book_outline=None → 不出現全書章節結構 block（backward compat）。"""
    from reasoning.prompts.writer import WriterPromptBuilder

    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="案例",
        section_outline="台灣案例",
        relevant_findings="findings",
        analyst_citations=[1],
        citation_format="numeric",
        book_outline=None,
    )
    assert "全書章節結構" not in prompt
    assert "前一章摘要" not in prompt


def test_section_compose_prompt_outline_n_inside_brief_not_misread_as_citation():
    """Plan §9 Hallucination Guard regression：outline brief 含 [N] 字樣的章節 title
    邊緣 case → writer 不應把 outline 內的 N 當引用 ID。Prompt 結構上把 outline 標
    為「結構提示」而不是「引用白名單」，避免 LLM 把 outline 內的方括號數字當 evidence。
    """
    from reasoning.prompts.writer import WriterPromptBuilder
    from reasoning.schemas_live import BookOutline, ChapterPlan

    builder = WriterPromptBuilder()
    outline = BookOutline(
        chapters=[
            ChapterPlan(
                chapter_index=0, title="關於 [5] 的研究",
                brief="探討 [5] 框架在台灣的應用",
                target_word_count=800, role="intro", transition_hint="",
                planned_evidence_ids=[1],
            ),
            ChapterPlan(
                chapter_index=1, title="結論", brief="收尾",
                target_word_count=500, role="conclusion", transition_hint="承接前文",
                planned_evidence_ids=[],
            ),
        ],
        overall_arc="A → B",
    )
    prompt = builder.build_section_compose_prompt(
        section_title="關於 [5] 的研究",
        section_outline="探討",
        relevant_findings="findings",
        analyst_citations=[1],  # 真正白名單只含 1
        citation_format="numeric",
        book_outline=outline,
        current_chapter_index=0,
        previous_chapter_summary="",
    )
    # outline block 必須與 evidence 白名單清楚分離；prompt 須提示「outline 內方括號數字
    # 屬於章節 title/brief 內容，不是 evidence ID」
    assert "全書章節結構" in prompt
    # 白名單仍是 [1]，最大 ID 提醒應顯示 1（不是 5）
    assert "最大 ID = **1**" in prompt


# ============================================================================
# Plan 4 Phase 3: compose_section + LiveWriterSectionOutput.chapter_summary
# ============================================================================


@pytest.mark.asyncio
async def test_compose_section_accepts_book_outline_and_prev_summary():
    """compose_section 新增 book_outline / current_chapter_index / previous_chapter_summary
    參數（default None / 0 / ""）不 break 既有 caller，且傳給 prompt builder。"""
    from reasoning.agents.writer import WriterAgent
    from reasoning.schemas_live import LiveWriterSectionOutput

    handler = MagicMock()
    writer = WriterAgent(handler=handler, timeout=10)
    writer.call_llm_validated = AsyncMock(
        return_value=(
            LiveWriterSectionOutput(
                section_title="案例",
                section_content="...",
                sources_used=[1],
                confidence_level="Medium",
                narration="",
                chapter_summary="本章摘要 50 字",
            ),
            0,
            False,
        )
    )

    outline = _make_book_outline()
    captured = {}
    original = writer.prompt_builder.build_section_compose_prompt

    def spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    with patch.object(writer.prompt_builder, "build_section_compose_prompt", side_effect=spy):
        result = await writer.compose_section(
            section_title="案例",
            section_outline="台灣",
            relevant_findings="findings",
            analyst_citations=[1],
            book_outline=outline,
            current_chapter_index=1,
            previous_chapter_summary="前章摘要",
        )

    assert captured.get("book_outline") is outline
    assert captured.get("current_chapter_index") == 1
    assert captured.get("previous_chapter_summary") == "前章摘要"
    # chapter_summary 從 LLM output schema 取
    assert result.chapter_summary == "本章摘要 50 字"


@pytest.mark.asyncio
async def test_compose_section_backward_compat_no_outline_params():
    """既有 caller 沒傳 book_outline/current_chapter_index/previous_chapter_summary → 不 break。"""
    from reasoning.agents.writer import WriterAgent
    from reasoning.schemas_live import LiveWriterSectionOutput

    handler = MagicMock()
    writer = WriterAgent(handler=handler, timeout=10)
    writer.call_llm_validated = AsyncMock(
        return_value=(
            LiveWriterSectionOutput(
                section_title="X",
                section_content="...",
                sources_used=[],
                confidence_level="Medium",
            ),
            0,
            False,
        )
    )

    result = await writer.compose_section(
        section_title="X",
        section_outline="o",
        relevant_findings="f",
        analyst_citations=[],
    )
    # chapter_summary 預設空字串
    assert result.chapter_summary == ""


def test_hallucination_guard_empty_whitelist_and_empty_sources_does_not_trigger():
    """Plan 2 Phase 3 regression: chapter override 後第二章以後 valid_ids=cm.topics 聯集，
    analyst_citations=[]，writer 不應輸出 [N]，sources_used=[] → guard 不觸發。

    Plan §6 Hallucination Guard regression：空 whitelist + 空 sources_used → no-op。
    """
    from reasoning.live_research.hallucination_guard import apply_hallucination_guard
    from reasoning.schemas_live import LiveWriterSectionOutput

    section = LiveWriterSectionOutput(
        section_title="結論",
        section_content="本章節綜合各章分析，提出政策建議。沒有引用標記。",
        sources_used=[],
        confidence_level="Medium",
        narration="chapter override 第二章以後沒對應 topic evidence",
    )
    corrected, was_corrected = apply_hallucination_guard(section, valid_evidence_ids=set())
    assert was_corrected is False
    assert corrected is section
