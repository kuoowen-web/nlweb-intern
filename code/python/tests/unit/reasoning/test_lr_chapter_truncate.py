"""LR 章節字數 overshoot 透明化旁白（改回軟約束：content 不動、只發旁白）。

背景（2026-07 regression 修復）：commit 9371337b 曾把「超標→發旁白、內容保留」的軟約束
改成「超標→硬切內容+補省略號」，CEO 見報告「完整句子。…」斷尾「切掉就不能用了」。
本檔反轉：驗證 (a) 只發偏長旁白、content 一字不動；(b) user 沒指定字數 → 不切也不旁白。

保留 _count_chapter_words（字數度量還要用來判「是否偏長」）；硬切函式與三常數已移除。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.live_research.orchestrator import _count_chapter_words
from reasoning.live_research import lr_copy


def _make_orch():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock()
    handler.query = "字數測試"
    handler.query_params = {}
    handler.site = "all"
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=True)
    handler.final_retrieved_items = []
    handler._save_state = AsyncMock()
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    return orch


def _section(content, status="drafted", title="國內案例文獻"):
    from reasoning.schemas_live import LiveWriterSectionOutput
    return LiveWriterSectionOutput(
        section_title=title, section_content=content,
        sources_used=[1], confidence_level="Medium", status=status,
    )


def _capture_narration(orch):
    captured = []

    async def fake_emit(text):
        captured.append(text)

    orch._emit_narration = fake_emit
    return captured


# ── _count_chapter_words 保留（字數度量） ──

def test_count_chapter_words_strips_cite_placeholders():
    assert _count_chapter_words("台灣綠能政策推動。") == len("台灣綠能政策推動。")
    content = "再生能源占比達 32.5%{cite:1}。德國經驗{cite:12}值得借鏡。"
    expected = len("再生能源占比達 32.5%。德國經驗值得借鏡。")
    assert _count_chapter_words(content) == expected
    assert _count_chapter_words("") == 0
    assert _count_chapter_words(None) == 0


# ── (a) user 指定字數 + 超標 → 發旁白、content 不動 ──

@pytest.mark.asyncio
async def test_overshoot_narrates_but_keeps_content_when_user_specified():
    orch = _make_orch()
    captured = _capture_narration(orch)
    sentence = "台灣綠能政策推動逐步展開，地方與中央在土地使用上時有摩擦。"
    section = _section(sentence * 60)  # ~2000+ 字，遠超 target=800
    original = section.section_content

    was = await orch._maybe_narrate_word_overshoot(
        section_output=section, target=800, user_specified_word_count=True,
    )
    assert was is True
    assert section.section_content == original  # content 不動
    assert "…" not in section.section_content
    assert len(captured) == 1
    assert "節略" not in captured[0]
    assert "完整保留" in captured[0] or "沒有刪節" in captured[0]
    assert "國內案例文獻" in captured[0]
    assert "800" in captured[0]


# ── (b) user 沒指定字數 → 不切也不旁白（即使超長）──

@pytest.mark.asyncio
async def test_no_narration_when_user_did_not_specify_word_count():
    orch = _make_orch()
    captured = _capture_narration(orch)
    section = _section("台灣綠能政策。" * 500)  # 超長
    original = section.section_content

    was = await orch._maybe_narrate_word_overshoot(
        section_output=section, target=800, user_specified_word_count=False,
    )
    assert was is False
    assert section.section_content == original
    assert captured == []


# ── 閾值：未過閾值不發 ──

@pytest.mark.asyncio
async def test_no_narration_within_threshold():
    orch = _make_orch()
    captured = _capture_narration(orch)
    section = _section("綠能。" * 300)  # 900 字，target=800 → 900 <= 1040，未過閾值
    assert _count_chapter_words(section.section_content) == 900
    was = await orch._maybe_narrate_word_overshoot(
        section_output=section, target=800, user_specified_word_count=True,
    )
    assert was is False
    assert captured == []


# ── status 非 drafted / target<=0 → 不發 ──

@pytest.mark.asyncio
async def test_no_narration_when_non_drafted_or_zero_target():
    orch = _make_orch()
    captured = _capture_narration(orch)

    blocked = _section("[本章資料不足] ..." * 200, status="guard_failed")
    was = await orch._maybe_narrate_word_overshoot(
        section_output=blocked, target=500, user_specified_word_count=True,
    )
    assert was is False
    assert captured == []

    section = _section("綠能政策。" * 400, status="drafted")
    was = await orch._maybe_narrate_word_overshoot(
        section_output=section, target=0, user_specified_word_count=True,
    )
    assert was is False
    assert captured == []


# ── SF1：resolver per-chapter word_target 優先（outline 漏抄不 no-op）──

def test_resolve_chapter_target_prefers_per_chapter_word_target():
    """user 對本章指定 word_target=1200，但 outline planner 漏抄（target=0）→
    resolver 仍回 1200（user 真實 surface form 優先），不 silent no-op。"""
    state = MagicMock()
    state.format_specs = {"chapters": [{}, {}, {}, {"word_target": 1200}, {}]}
    book_outline = MagicMock()
    book_outline.chapters = [MagicMock(target_word_count=0) for _ in range(5)]
    orch = _make_orch()
    got = orch._resolve_chapter_target_words(book_outline, state, 3, user_specified=True)
    assert got == 1200  # per-chapter word_target 優先，非 outline 的 0
