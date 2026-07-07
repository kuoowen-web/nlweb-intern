"""LR 章節字數 post-process truncate（硬切到 target 附近、切句界、明示節略）。

背景（bug 2026-06-20）：LR stage 5 writer 章節字數 overshoot（prod：target=800
實際=2258）。memory/lessons-live-research.md 已記載「字數硬上限 prompt 層只能逼近
不能精確，要硬切需 post-process truncate」。軟約束（±15% 旁白）已上線但 prod 證實
壓不住 → 本檔新增 post-process truncate（CEO/CTO 認可的方向）。

設計約束：
- 切在中文句界（。！？；），不切在句中。
- no silent fail：節略補省略號 `…` 明示。
- 不破壞 {cite:N} placeholder（不可切出半個 citation）。
- limit 來源 = 該章 target_word_count（完全參數化，不帶任何 magic default）。
- 字數以 _count_chapter_words 度量（剝除 {cite:N} 後的字元數），與 target 對齊。

刻意「不」複用 lr_copy 那個 100 字 _WARN_EXPLANATION_MAX helper（已死碼移除）：
章節內文是 800-2000 字級別，scope 完全不同。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.live_research.orchestrator import (
    _count_chapter_words,
    _truncate_chapter_to_target,
    TRUNCATE_ELLIPSIS,
)
from reasoning.live_research import lr_copy


def _wc(text: str) -> int:
    """測試輔助：用 production 同一把尺度量字數（剝 {cite:N}）。"""
    return _count_chapter_words(text)


# ── 核心 bug repro：2258 字 → target 800，切在句界、有省略號、字數收斂 ──

def test_truncate_long_chapter_to_target_cuts_at_sentence_boundary():
    # 造一段「2258 字級別」含明確句界的中文內文（句末標點 。）。
    sentence = "台灣的綠能政策在近年逐步推動，地方政府與中央在土地使用上時有摩擦。"
    long_text = sentence * 70  # 遠超 target（repro prod bug 2258 字規模）
    assert _wc(long_text) > 2000  # 確認確實 overshoot（repro bug 規模）

    target = 800
    out, was_truncated = _truncate_chapter_to_target(long_text, target)

    assert was_truncated is True
    # 切完字數應接近 target（不超過 target，省略號不計入 padding 太多）
    out_wc = _wc(out)
    assert out_wc <= target, f"truncate 後 {out_wc} 應 <= target {target}"
    # 不該砍掉大半 — 切在 target 附近而非過早（> target * 0.6）
    assert out_wc >= target * 0.6, f"truncate 後 {out_wc} 切太前面（丟太多）"
    # no silent fail：明示節略
    assert TRUNCATE_ELLIPSIS in out
    # 切在句界：省略號前一個有意義字元應是句末標點（。），不是句中
    body = out[: out.rfind(TRUNCATE_ELLIPSIS)]
    assert body.rstrip()[-1] in "。！？；", "應切在句末標點，不可切句中"


def test_no_truncate_when_within_target():
    text = "綠能土地爭議的三個面向。第一是程序。第二是補償。第三是資訊揭露。"
    target = 1000  # text 遠短於 target
    out, was_truncated = _truncate_chapter_to_target(text, target)
    assert was_truncated is False
    assert out == text  # 原樣回傳
    assert TRUNCATE_ELLIPSIS not in out


def test_truncate_preserves_cite_placeholders_no_half_citation():
    # 句界前帶 {cite:N}；truncate 不可切出半個 {cite:
    sentence = "再生能源占比達 32.5%{cite:1}，較去年成長顯著{cite:12}。"
    long_text = sentence * 30
    target = 200
    out, was_truncated = _truncate_chapter_to_target(long_text, target)
    assert was_truncated is True
    # 不可出現殘缺的 citation 片段（半個 {cite: 或 cite:N}）
    import re
    # 移除所有完整 {cite:N} 後，不該再殘留 'cite' 或 '{cite' 碎片
    stripped = re.sub(r"\{cite:\d+\}", "", out)
    assert "{cite" not in stripped
    assert "cite:" not in stripped
    # 完整 citation 數量應 > 0（保留了內容裡的引用）或為 0（剛好切在無 cite 處）皆合法，
    # 但每個出現的 {cite 必須是完整 {cite:N}
    for m in re.finditer(r"\{[^}]*", out):
        frag = m.group(0)
        if frag.startswith("{cite"):
            assert re.match(r"\{cite:\d+\}", out[m.start():]), (
                f"切出半個 citation: {out[m.start():m.start()+15]!r}"
            )


def test_truncate_hard_cut_when_no_nearby_sentence_boundary():
    # 一段在 limit 內「沒有」句末標點的長文（句界離 limit 太遠）→ 寧可硬切補省略號，
    # 不丟掉大半內容（參考既有 helper 的 should-fix：< limit*0.6 寧硬切）。
    no_boundary = "甲" * 500 + "。" + "乙" * 500  # 第一個句界在第 500 字（target=100 內無句界）
    target = 100
    out, was_truncated = _truncate_chapter_to_target(no_boundary, target)
    assert was_truncated is True
    assert TRUNCATE_ELLIPSIS in out
    out_wc = _wc(out)
    # 硬切：字數接近 target，沒丟掉大半（不會因為找不到近的句界就退回切到第 500 字）
    assert out_wc <= target + len(TRUNCATE_ELLIPSIS)
    assert out_wc >= target * 0.6


def test_truncate_target_zero_or_negative_is_noop():
    text = "綠能政策。土地爭議。" * 50
    for target in (0, -5):
        out, was_truncated = _truncate_chapter_to_target(text, target)
        assert was_truncated is False
        assert out == text


def test_truncate_empty_or_none_content():
    for content in ("", None):
        out, was_truncated = _truncate_chapter_to_target(content, 800)
        assert was_truncated is False
        assert out == (content or "")


# ── orchestrator 接線：_maybe_truncate_chapter（切 + 換誠實旁白） ──


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
    """把 orch._emit_narration 換成捕捉用的 async fake，回傳 captured list。"""
    captured = []

    async def fake_emit(text):
        captured.append(text)

    orch._emit_narration = fake_emit
    return captured


@pytest.mark.asyncio
async def test_maybe_truncate_chapter_cuts_and_narrates_truncation():
    orch = _make_orch()
    captured = _capture_narration(orch)

    sentence = "台灣綠能政策推動逐步展開，地方與中央在土地使用上時有摩擦。"
    section = _section(sentence * 60)  # ~2000+ 字，遠超 target=800

    was = await orch._maybe_truncate_chapter(
        section_output=section, target=800,
    )

    assert was is True
    # content 真的被切短了（接近 target，不是原樣）
    assert _count_chapter_words(section.section_content) <= 800
    assert TRUNCATE_ELLIPSIS in section.section_content
    # 發了「已節略」誠實旁白（不是「照常保留」舊文案）
    assert len(captured) == 1
    assert "節略" in captured[0]
    assert "國內案例文獻" in captured[0]
    assert "800" in captured[0]


@pytest.mark.asyncio
async def test_maybe_truncate_chapter_noop_when_within_threshold():
    orch = _make_orch()
    captured = _capture_narration(orch)

    # 900 字、target=800 → 900 <= 800*1.3=1040，未過閾值 → 不切、不發
    section = _section("綠能。" * 300)  # 900 字
    assert _count_chapter_words(section.section_content) == 900
    original = section.section_content

    was = await orch._maybe_truncate_chapter(section_output=section, target=800)
    assert was is False
    assert section.section_content == original
    assert captured == []


@pytest.mark.asyncio
async def test_maybe_truncate_chapter_skips_non_drafted_and_zero_target():
    orch = _make_orch()
    captured = _capture_narration(orch)

    # status 非 drafted（content 是 blocked 替換文）→ 不切
    blocked = _section("[本章資料不足] ..." * 200, status="guard_failed")
    was = await orch._maybe_truncate_chapter(section_output=blocked, target=500)
    assert was is False
    assert captured == []

    # target=0（未指定字數）→ 不切
    section = _section("綠能政策。" * 400, status="drafted")
    was = await orch._maybe_truncate_chapter(section_output=section, target=0)
    assert was is False
    assert captured == []
