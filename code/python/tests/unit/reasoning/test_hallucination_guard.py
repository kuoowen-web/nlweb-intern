"""Unit tests for LR per-section Hallucination Guard (Task 9).

Port from DR `orchestrator.py:1095-1131` to LR `_write_section`:
- subset check: sources_used ⊆ valid_evidence_ids
- literal placeholder regex check（LR 特有）
- 觸發後自動修正 sources / confidence_level 依嚴重度分級降級（severe→Low，
  否則降一級 High→Medium/Medium→Low）/ methodology_note 註記
"""

import pytest

from reasoning.schemas_live import LiveWriterSectionOutput, CitationInline
from reasoning.live_research.hallucination_guard import apply_hallucination_guard


def test_section_with_invalid_source_id_corrected():
    """sources_used 含白名單外 id → 自動修正；本例無 typed citation（corrected_citations 空）
    → severe → confidence='Low'（G4 分級降級：severe case 才打到 Low）。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="正常內容",
        sources_used=[1, 2, 99],  # 99 不在白名單
        confidence_level="High",
    )
    whitelist = {1, 2, 3}

    corrected, was_corrected = apply_hallucination_guard(section, whitelist)

    assert was_corrected is True
    assert set(corrected.sources_used) == {1, 2}
    assert corrected.confidence_level == "Low"
    note = (corrected.methodology_note or "")
    assert "自動修正" in note
    assert "99" in note  # 報告哪個 id 被移除


def test_section_with_all_valid_sources_unchanged():
    """sources_used 全在白名單 + 內容乾淨 → 不修正。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="完全乾淨的內容，無 placeholder。",
        sources_used=[1, 2],
        confidence_level="High",
    )
    whitelist = {1, 2, 3}
    corrected, was_corrected = apply_hallucination_guard(section, whitelist)
    assert was_corrected is False
    # 應該回原 instance（不 mutate）
    assert corrected.confidence_level == "High"
    assert corrected.methodology_note is None
    assert set(corrected.sources_used) == {1, 2}


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — Task 5:
# entity_grounding_check — cheap LLM call 列出 section 出現但 evidence 找不到的具體 entity
# ============================================================================


@pytest.mark.asyncio
async def test_entity_grounding_check_flags_ungrounded_entities(monkeypatch):
    """寫了「弗萊堡」「Horns Rev」但 evidence 中只有「丹麥」→ 兩者 flag。
    三段式：抽取 stub 給定 candidate → 字面捷徑 miss（不在 evidence）→ 語意層判 ungrounded。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.live_research.hallucination_guard import entity_grounding_check
    from unittest.mock import MagicMock

    section = LiveWriterSectionOutput(
        section_title="國外案例",
        section_content="德國弗萊堡的綠能案例顯示...Horns Rev 風場提供...丹麥則...",
        sources_used=[1],
        confidence_level="Medium",
    )
    chapter_evidence_text = (
        "[1] 丹麥再生能源 — 描述丹麥的綠能轉型"
    )

    async def fake_extract(content, handler, level="low", **kwargs):
        return ["弗萊堡", "Horns Rev", "丹麥"]
    monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract)

    # 語意層 LLM 回傳：偵測到「弗萊堡」「Horns Rev」（丹麥走字面捷徑被濾掉，不進語意層）
    async def fake_ask_llm(prompt, schema, **kwargs):
        return {"ungrounded_entities": ["弗萊堡", "Horns Rev"]}

    monkeypatch.setattr("core.llm.ask_llm", fake_ask_llm)

    ungrounded = await entity_grounding_check(
        section=section,
        chapter_evidence_text=chapter_evidence_text,
        handler=MagicMock(query_params={}),
    )
    assert "弗萊堡" in ungrounded
    assert "Horns Rev" in ungrounded


@pytest.mark.asyncio
async def test_entity_grounding_check_returns_empty_when_all_grounded(monkeypatch):
    """全部 entity 語意有支撐 → 回 []（三段式：抽取 → 捷徑 miss → 語意判全 grounded）。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.live_research.hallucination_guard import entity_grounding_check
    from unittest.mock import MagicMock

    section = LiveWriterSectionOutput(
        section_title="t", section_content="丹麥王國的政策", sources_used=[1],
        confidence_level="High",
    )

    async def fake_extract(content, handler, level="low", **kwargs):
        return ["丹麥王國"]  # 字面不在「丹麥」evidence（全名 vs 簡稱）→ 進語意層
    monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract)

    async def fake_ask_llm(prompt, schema, **kwargs):
        return {"ungrounded_entities": []}

    monkeypatch.setattr("core.llm.ask_llm", fake_ask_llm)

    ungrounded = await entity_grounding_check(
        section=section, chapter_evidence_text="丹麥",
        handler=MagicMock(query_params={}),
    )
    assert ungrounded == []


@pytest.mark.asyncio
async def test_entity_grounding_check_extract_failure_returns_empty(monkeypatch):
    """抽取階段 LLM 失敗 → 回 [] (R1：抽不出 candidate = 沒東西要查 = 不放行幻覺，
    抽取階段 fail-open 安全)。注意：語意階段失敗則 raise（見 *_raises 測試）。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.live_research.hallucination_guard import entity_grounding_check
    from unittest.mock import MagicMock

    section = LiveWriterSectionOutput(
        section_title="t", section_content="x", sources_used=[1],
        confidence_level="High",
    )

    # 抽取階段 ask_llm 失敗 → _extract_entities_for_grounding 內 graceful 回 []
    async def fake_ask_llm(prompt, schema, **kwargs):
        raise RuntimeError("LLM timeout")

    monkeypatch.setattr("core.llm.ask_llm", fake_ask_llm)

    ungrounded = await entity_grounding_check(
        section=section, chapter_evidence_text="x",
        handler=MagicMock(query_params={}),
    )
    assert ungrounded == []


def test_section_with_literal_placeholder_flagged_author_year():
    """Section content 出現字面 (Author, Year) → flag hallucination_corrected。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="這個議題很重要 (Author, Year)。",
        sources_used=[1],
        confidence_level="High",
    )
    whitelist = {1, 2}
    corrected, was_corrected = apply_hallucination_guard(section, whitelist)
    assert was_corrected is True
    assert corrected.confidence_level == "Low"
    note = (corrected.methodology_note or "").lower()
    assert "placeholder" in note or "佔位" in note or "占位" in (corrected.methodology_note or "")


def test_section_with_literal_placeholder_flagged_chinese():
    """Section content 出現字面 (作者, 年份) → flag。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="某段論述 (作者, 年份)。",
        sources_used=[1],
        confidence_level="High",
    )
    whitelist = {1}
    corrected, was_corrected = apply_hallucination_guard(section, whitelist)
    assert was_corrected is True
    assert corrected.confidence_level == "Low"


def test_section_with_bare_n_placeholder_flagged():
    """Section content 含裸 [N] 字面 placeholder → flag。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="台積電股價上漲 [N]。",
        sources_used=[1],
        confidence_level="High",
    )
    whitelist = {1}
    corrected, was_corrected = apply_hallucination_guard(section, whitelist)
    assert was_corrected is True
    assert corrected.confidence_level == "Low"


def test_section_preserves_existing_methodology_note():
    """既有 methodology_note 不被覆蓋，自動修正內容用 append 方式加入。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="ok",
        sources_used=[99],
        confidence_level="High",
        methodology_note="原本的說明",
    )
    whitelist = {1}
    corrected, was_corrected = apply_hallucination_guard(section, whitelist)
    assert was_corrected is True
    assert "原本的說明" in (corrected.methodology_note or "")
    assert "自動修正" in (corrected.methodology_note or "")


# ============================================================================
# C.1：deterministic 字面捷徑 helper（grounded shortcut）
# ============================================================================


def test_deterministic_grounded_filter_exact_substring():
    from reasoning.live_research.hallucination_guard import (
        _deterministic_grounded_filter,
    )
    evidence = "[3] 台鹽綠能在台南設案場 — 泰國蝦養殖戶受影響\n[22] 嘉義縣某水泥廠"
    candidates = ["台鹽綠能", "泰國蝦", "嘉義縣", "彰化外海風場"]
    # 前三字面在 evidence → 捷徑判 grounded（移除）；只剩「彰化外海風場」交給 LLM
    needs_llm = _deterministic_grounded_filter(candidates, evidence)
    assert needs_llm == ["彰化外海風場"]


def test_deterministic_grounded_filter_normalizes_whitespace_and_width():
    from reasoning.live_research.hallucination_guard import (
        _deterministic_grounded_filter,
    )
    evidence = "二０３０年 目標"  # 全形 + 空白
    candidates = ["二030年"]      # 半形
    needs_llm = _deterministic_grounded_filter(candidates, evidence)
    assert needs_llm == []        # 正規化後命中 → 不需 LLM


# ============================================================================
# C.2：entity_grounding_check 三段式（抽取→字面捷徑→語意 low tier）+ R1 fail-closed
# ============================================================================


@pytest.mark.asyncio
async def test_grounding_check_literal_shortcut_skips_llm(monkeypatch):
    """所有 entity 字面在 evidence → 捷徑全過濾 → 不打 LLM、回 []。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.schemas_live import LiveWriterSectionOutput

    called = {"n": 0}

    async def fake_ask_llm(*a, **kw):
        called["n"] += 1
        return {"ungrounded_entities": ["不該被呼叫"]}

    monkeypatch.setattr("core.llm.ask_llm", fake_ask_llm)
    # 抽取階段也走 LLM；stub 它直接給定 entity（隔離捷徑層）
    async def fake_extract(content, handler, level="low", **kwargs):
        return ["台鹽綠能", "泰國蝦", "嘉義縣"]
    monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract)

    section = LiveWriterSectionOutput(
        section_title="t",
        section_content="台鹽綠能、泰國蝦、嘉義縣。",
        sources_used=[3], confidence_level="Medium",
    )
    evidence = "[3] 台鹽綠能台南案場 泰國蝦 嘉義縣"
    handler = type("H", (), {"query_params": {}})()
    out = await hg.entity_grounding_check(section, evidence, handler)
    assert out == []
    assert called["n"] == 0  # 全字面命中 → 完全不打語意 LLM


@pytest.mark.asyncio
async def test_grounding_check_semantic_llm_uses_low_tier(monkeypatch):
    """字面不命中的殘餘 → 語意 LLM 用 low tier（CEO 決策①：tier 維持 low，不升 high）。
    資料源做好（全 pool + 不截斷 + prior entities）後，low model 也判得出同義改寫。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.schemas_live import LiveWriterSectionOutput

    seen = {"level": None}

    async def fake_ask_llm(prompt, schema, level="low", **kw):
        seen["level"] = level
        return {"ungrounded_entities": []}

    monkeypatch.setattr("core.llm.ask_llm", fake_ask_llm)
    async def fake_extract(content, handler, level="low", **kwargs):
        return ["台電"]  # 字面不在 evidence（evidence 寫全名）→ 進語意層
    monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract)

    section = LiveWriterSectionOutput(
        section_title="t", section_content="台電推綠能。",
        sources_used=[3], confidence_level="Medium",
    )
    evidence = "[3] 台灣電力公司 推動再生能源"
    handler = type("H", (), {"query_params": {}})()
    # orchestrator 兩呼叫點不傳 grounding_level → 預設 low（CEO 決策①）
    out = await hg.entity_grounding_check(section, evidence, handler)
    assert seen["level"] == "low"   # 語意判定維持 low tier（不升 high）
    assert out == []                # low model + 全名 evidence → 判「台電」grounded


@pytest.mark.asyncio
async def test_grounding_check_semantic_llm_failure_raises(monkeypatch):
    """R1 fail-closed：語意層 LLM exception → raise GroundingCheckUnavailable
    （不 return [] 當全 grounded）。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.schemas_live import LiveWriterSectionOutput

    async def fake_extract(content, handler, level="low", **kwargs):
        return ["台電"]  # 字面不在 evidence → 進語意層
    monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract)

    async def boom(*a, **kw):
        raise RuntimeError("context window exceeded")
    monkeypatch.setattr("core.llm.ask_llm", boom)

    section = LiveWriterSectionOutput(
        section_title="t", section_content="台電推綠能。",
        sources_used=[3], confidence_level="Medium",
    )
    handler = type("H", (), {"query_params": {}})()
    with pytest.raises(hg.GroundingCheckUnavailable):
        await hg.entity_grounding_check(section, "[3] 台灣電力公司", handler)


@pytest.mark.asyncio
async def test_grounding_check_unparseable_response_raises(monkeypatch):
    """R1 fail-closed：語意層回傳無法解析（缺 key）→ raise（不當全 grounded）。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.schemas_live import LiveWriterSectionOutput

    async def fake_extract(content, handler, level="low", **kwargs):
        return ["台電"]
    monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract)

    async def bad_resp(*a, **kw):
        return {"wrong_key": []}  # 缺 ungrounded_entities
    monkeypatch.setattr("core.llm.ask_llm", bad_resp)

    section = LiveWriterSectionOutput(
        section_title="t", section_content="台電推綠能。",
        sources_used=[3], confidence_level="Medium",
    )
    handler = type("H", (), {"query_params": {}})()
    with pytest.raises(hg.GroundingCheckUnavailable):
        await hg.entity_grounding_check(section, "[3] 台灣電力公司", handler)


# ============================================================================
# Task 2.1：split_and_filter_ungrounded_sentences（R3 句子分類，只刪純未驗證句）
# ============================================================================


def test_split_and_filter_removes_only_pure_unverified_sentences():
    from reasoning.live_research.hallucination_guard import (
        split_and_filter_ungrounded_sentences,
    )
    content = (
        "台鹽綠能在台南設置案場，引發養殖戶疑慮。"
        "某水泥公司在當地的爭議也被提及。"      # 純未驗證句 → 刪
        "整體而言，社區溝通是關鍵。"
    )
    kept, removed, unsafe = split_and_filter_ungrounded_sentences(
        content, ungrounded_entities=["某水泥公司"], grounded_entities=[],
    )
    assert "台鹽綠能在台南設置案場" in kept
    assert "社區溝通是關鍵" in kept
    assert "某水泥公司" not in kept
    assert removed == 1
    assert unsafe == 0


def test_split_and_filter_keeps_mixed_sentence_with_verified_entity():
    """R3：同句又含已驗證 entity → 不硬刪（避免連 grounded 內容一起殺），回報 unsafe。"""
    from reasoning.live_research.hallucination_guard import (
        split_and_filter_ungrounded_sentences,
    )
    content = "台鹽綠能與某水泥公司在台南共同推動案場。"  # 混合句：台鹽綠能已驗證
    kept, removed, unsafe = split_and_filter_ungrounded_sentences(
        content, ungrounded_entities=["某水泥公司"], grounded_entities=["台鹽綠能"],
    )
    assert kept == content      # 不硬刪混合句
    assert removed == 0
    assert unsafe == 1          # 回報：有不可安全硬刪的句子 → caller 應走退化 (a)


def test_split_and_filter_keeps_sentence_with_citation():
    """R3：含 citation 的句子不硬刪（即使含 ungrounded entity 字面）。"""
    from reasoning.live_research.hallucination_guard import (
        split_and_filter_ungrounded_sentences,
    )
    content = "某水泥公司的爭議見諸報導[3]。"
    kept, removed, unsafe = split_and_filter_ungrounded_sentences(
        content, ungrounded_entities=["某水泥公司"], grounded_entities=[],
    )
    assert kept == content
    assert removed == 0
    assert unsafe == 1


def test_split_and_filter_keeps_conjunction_bound_sentence():
    """R3：被連接詞綁定有上下文依賴 → 不硬刪（避免句子殘骸/指代不明）。"""
    from reasoning.live_research.hallucination_guard import (
        split_and_filter_ungrounded_sentences,
    )
    content = "案場引發疑慮。因此某水泥公司也被點名。社區溝通是關鍵。"
    kept, removed, unsafe = split_and_filter_ungrounded_sentences(
        content, ungrounded_entities=["某水泥公司"], grounded_entities=[],
    )
    assert "因此某水泥公司也被點名" in kept   # 連接詞綁定句保留
    assert removed == 0
    assert unsafe == 1


def test_split_and_filter_all_pure_unverified_returns_empty():
    from reasoning.live_research.hallucination_guard import (
        split_and_filter_ungrounded_sentences,
    )
    content = "X機構做了Y。X機構又做了Z。"
    kept, removed, unsafe = split_and_filter_ungrounded_sentences(
        content, ungrounded_entities=["X機構"], grounded_entities=[],
    )
    assert kept.strip() == ""
    assert removed == 2
    assert unsafe == 0


# ── LLMError sentinel 注入回歸護網（#14 fail-open / #15 fail-closed，不打真 LLM）──


@pytest.mark.asyncio
async def test_candidate_extraction_fail_open_on_llmerror():
    """#14 fail-open：ask_llm 回 LLMError → fail-open 回 []，且觸發 on_extraction_failed callback。

    FIX-1（Skeptic I-1）：commit 4936392c 後 ask_llm 失敗 **return** LLMError 不 raise，
    故障不進 except 分支。若沒有顯式 LLMError 偵測，callback 對主要故障模式從未觸發、user
    看不到旁白。此測試釘死：LLMError → fail-open([]) **且** callback 觸發。
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.llm import LLMError
    from reasoning.live_research import hallucination_guard as hg
    # guard 內是 `from core.llm import ask_llm` 的 local import → 必須 patch 來源 core.llm.ask_llm
    called = {"n": 0}
    with patch("core.llm.ask_llm", new=AsyncMock(return_value=LLMError("timeout", "x"))):
        out = await hg._extract_entities_for_grounding(
            "某段落含台電與 5GW", handler=MagicMock(query_params={}),
            on_extraction_failed=lambda: called.__setitem__("n", called["n"] + 1))
    assert out == []   # fail-open 保留：sentinel falsy → []
    assert called["n"] == 1, "LLMError 是主要故障模式，必須觸發 callback 讓 caller 補旁白"


@pytest.mark.asyncio
async def test_candidate_extraction_empty_entities_does_not_invoke_callback():
    """FIX-1 負向：ask_llm 真回應但 entities 為空（合法『沒 candidate』）→ 不觸發 callback。

    語意區分必須保住：LLM 正常回應的空 entities ≠ LLM 故障。
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from reasoning.live_research import hallucination_guard as hg
    called = {"n": 0}
    with patch("core.llm.ask_llm", new=AsyncMock(return_value={"entities": []})):
        out = await hg._extract_entities_for_grounding(
            "這是一段抽象論述沒有具體名稱", handler=MagicMock(query_params={}),
            on_extraction_failed=lambda: called.__setitem__("n", called["n"] + 1))
    assert out == []
    assert called["n"] == 0, "合法空 entities ≠ 抽取故障，不可誤觸發 callback"


@pytest.mark.asyncio
async def test_semantic_grounding_fail_closed_on_llmerror():
    """#15 fail-closed：ask_llm 回 LLMError → 缺 'ungrounded_entities' key → raise GroundingCheckUnavailable。"""
    from unittest.mock import AsyncMock, MagicMock, patch
    from core.llm import LLMError
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.live_research.hallucination_guard import GroundingCheckUnavailable
    with patch("core.llm.ask_llm", new=AsyncMock(return_value=LLMError("provider_error", "boom"))):
        with pytest.raises(GroundingCheckUnavailable):
            await hg._semantic_grounding_check(
                ["台電"], "evidence text", handler=MagicMock(query_params={}), level="low")


# ============================================================================
# Task 3: 抽取層 fail-open 無旁白補齊（不改 fail-open 方向本身）
# ============================================================================


@pytest.mark.asyncio
async def test_extraction_failure_invokes_callback_and_keeps_fail_open(monkeypatch):
    """Task 3: 抽取 LLM 故障 → callback 被呼叫（讓 caller 補旁白）；行為仍 fail-open 回 []。

    驗 plumbing（故障訊號可被 caller 觀察），不改 fail-open 方向本身。
    """
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock

    async def _boom(*a, **k):
        raise RuntimeError("simulated extraction LLM failure")

    # 抽取走的是 core.llm.ask_llm（在 hallucination_guard 內 import）
    monkeypatch.setattr("core.llm.ask_llm", _boom)

    called = {"n": 0}
    def _on_fail():
        called["n"] += 1

    section = LiveWriterSectionOutput(
        section_title="x", section_content="弗萊堡 5 GW 風場",
        sources_used=[1], confidence_level="Medium",
    )
    handler = MagicMock(query_params={})
    result = await hg.entity_grounding_check(
        section=section,
        chapter_evidence_text="[1] 弗萊堡相關",
        handler=handler,
        on_extraction_failed=_on_fail,
    )
    assert result == [], "fail-open 方向不變：抽取故障仍回 []（grounding 跳過）"
    assert called["n"] == 1, "抽取故障必須觸發 callback 讓 caller 補旁白"


@pytest.mark.asyncio
async def test_extraction_success_does_not_invoke_callback(monkeypatch):
    """Task 3: 抽取成功（即使回空 entities）不觸發故障 callback —— 區分『沒 candidate』與『抽取故障』。"""
    from reasoning.live_research import hallucination_guard as hg
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock

    async def _empty(*a, **k):
        return {"entities": []}  # 抽取成功但本章真的沒 candidate

    monkeypatch.setattr("core.llm.ask_llm", _empty)
    called = {"n": 0}
    section = LiveWriterSectionOutput(
        section_title="x", section_content="這是一段抽象論述沒有具體名稱",
        sources_used=[], confidence_level="Medium",
    )
    handler = MagicMock(query_params={})
    result = await hg.entity_grounding_check(
        section=section, chapter_evidence_text="[1] e", handler=handler,
        on_extraction_failed=lambda: called.__setitem__("n", called["n"] + 1),
    )
    assert result == []
    assert called["n"] == 0, "真的沒 candidate ≠ 抽取故障，不可誤觸發旁白"


def test_orch_extraction_failed_callback_sets_pending_flag():
    """Task 3: orchestrator callback 同步安全，只 set pending flag（emit 在 async 區補）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from unittest.mock import MagicMock
    orch = LiveResearchOrchestrator(handler=MagicMock(query_params={}), dry_run=True)
    assert orch._grounding_extraction_failed_pending is False
    orch._narrate_grounding_extraction_failed()
    assert orch._grounding_extraction_failed_pending is True


@pytest.mark.asyncio
async def test_orch_extraction_failed_emit_helper_dedup():
    """Task 3: dedup helper —— pending 時播一次旁白；重入不重播（per-run dedup）。
    三 callsite 共用此 helper，故只需測 helper 一次即覆蓋三點的 dedup 行為。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research import lr_copy
    from unittest.mock import MagicMock, AsyncMock
    orch = LiveResearchOrchestrator(handler=MagicMock(query_params={}), dry_run=True)
    captured = []
    orch._emit_narration = AsyncMock(side_effect=lambda t: captured.append(t))

    # pending 未 set → helper 不播
    await orch._emit_grounding_extraction_failed_if_pending()
    assert captured == []

    # callback set pending → helper 播一次
    orch._narrate_grounding_extraction_failed()
    await orch._emit_grounding_extraction_failed_if_pending()
    # 重入（模擬第二/三 callsite 也呼叫 helper）→ 不重播
    await orch._emit_grounding_extraction_failed_if_pending()
    assert captured.count(lr_copy.GROUNDING_EXTRACTION_FAILED_NARRATION) == 1


# ============================================================================
# G3 時序錯位修復：_CITATION_RE 須同時認得 render 前 {cite:N} 與 render 後 [N]
# ============================================================================


def test_split_and_filter_keeps_sentence_with_pre_render_cite_placeholder():
    """G3 回歸測試：guard 管線在 orchestrator._render_section_citations 之前跑，
    此時 section content 仍是 {cite:N} placeholder（尚未轉成 [N]）。
    舊 _CITATION_RE 只認 [N]，認不出 {cite:N} → has_citation 恆 False →
    「已引用、其實有根據」的句子被誤判成純未驗證句遭硬刪。
    修法後：句子含 {cite:3} 應被 R3 的 citation 安全閥保住（不硬刪，計入 unsafe）。"""
    from reasoning.live_research.hallucination_guard import (
        split_and_filter_ungrounded_sentences,
    )
    content = "某水泥公司的爭議見諸報導{cite:3}。"
    kept, removed, unsafe = split_and_filter_ungrounded_sentences(
        content, ungrounded_entities=["某水泥公司"], grounded_entities=[],
    )
    assert kept == content
    assert removed == 0
    assert unsafe == 1


# ============================================================================
# G4 過度懲罰修復：依嚴重度分級信心降級，避免單一 phantom 就把整章打到 Low
# ============================================================================


def test_hallucination_guard_single_phantom_with_valid_citation_downgrades_to_medium():
    """G4：sources_used 含 1 個白名單外 phantom，但 citations 仍有 1 個有效引用、
    section_content 無字面 placeholder → 屬「多數 citation 仍有效」情況，
    只降一級（High→Medium），不應打到 Low。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="正常內容，附有效引用。",
        sources_used=[1, 99],  # 99 是 phantom，1 有效
        citations=[CitationInline(evidence_id=1)],  # 有效 citation 保留
        confidence_level="High",
    )
    whitelist = {1, 2, 3}

    corrected, was_corrected = apply_hallucination_guard(section, whitelist)

    assert was_corrected is True
    assert corrected.confidence_level == "Medium"


def test_hallucination_guard_majority_phantom_citations_forces_low():
    """G4（AR R2）：多數 citation 失效（9 phantom vs 1 valid）→ 仍應打到 Low，
    即使還剩 1 個有效 citation。舊邏輯 `severe = placeholder_hit or len(corrected)==0`
    只看「有無任一有效 citation」，9假1真只會降一級（Medium）——但本章 90% 引用是假的，
    語義上應偏 severe。新邏輯改用 phantom vs valid 數量比例：
    len(phantom_citations) >= len(corrected_citations) → severe → Low。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="正常內容，附多筆引用。",
        sources_used=[1, 2, 3, 4, 5, 6, 7, 8, 9, 99],  # 99 白名單外（sources_used 面）
        citations=[
            CitationInline(evidence_id=1),   # 唯一有效
            CitationInline(evidence_id=101),
            CitationInline(evidence_id=102),
            CitationInline(evidence_id=103),
            CitationInline(evidence_id=104),
            CitationInline(evidence_id=105),
            CitationInline(evidence_id=106),
            CitationInline(evidence_id=107),
            CitationInline(evidence_id=108),  # 8 個 phantom citation
        ],
        confidence_level="High",
    )
    whitelist = {1, 2, 3, 4, 5, 6, 7, 8, 9}

    corrected, was_corrected = apply_hallucination_guard(section, whitelist)

    assert was_corrected is True
    assert corrected.confidence_level == "Low"


def test_hallucination_guard_literal_placeholder_still_forces_low():
    """G4 嚴重分支：字面 placeholder 命中（Writer 根本沒照格式寫）→ 仍直接打到 Low，
    不受分級緩解影響。"""
    section = LiveWriterSectionOutput(
        section_title="X",
        section_content="某段論述 (作者, 年份)。",
        sources_used=[1],
        citations=[CitationInline(evidence_id=1)],
        confidence_level="High",
    )
    whitelist = {1}

    corrected, was_corrected = apply_hallucination_guard(section, whitelist)

    assert was_corrected is True
    assert corrected.confidence_level == "Low"
