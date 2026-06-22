"""Target 3 — Writer typed citations + strict placeholder tests.

Plan: lr-typeagent-refactor (2026-05-19, CEO 拍板 OQ-5: 立刻 strict 不 dual mode)。
- CitationInline + LiveWriterSectionOutput.citations 欄位
- EvidencePoolEntry author / year 欄位
- _render_section_citations post-process
- OQ-3: APA 模式中文 author 整名 render
"""
import pytest
from pydantic import ValidationError


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3.1: CitationInline + LiveWriterSectionOutput.citations schema
# ──────────────────────────────────────────────────────────────────────────────

def test_citation_inline_basic():
    from reasoning.schemas_live import CitationInline
    c = CitationInline(evidence_id=3)
    assert c.evidence_id == 3


def test_citation_inline_evidence_id_required():
    from reasoning.schemas_live import CitationInline
    with pytest.raises(ValidationError):
        CitationInline()


def test_live_writer_section_output_citations_default_empty():
    """既有 fixture 不傳 citations → default empty list（backward compat）。"""
    from reasoning.schemas_live import LiveWriterSectionOutput
    out = LiveWriterSectionOutput(
        section_title="前言", section_content="...", sources_used=[],
    )
    assert out.citations == []


def test_live_writer_section_output_citations_typed():
    from reasoning.schemas_live import LiveWriterSectionOutput, CitationInline
    out = LiveWriterSectionOutput(
        section_title="前言",
        section_content="再生能源占比 32.5%{cite:1}。",
        sources_used=[1],
        citations=[CitationInline(evidence_id=1)],
    )
    assert len(out.citations) == 1
    assert out.citations[0].evidence_id == 1


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3.2: EvidencePoolEntry author/year
# ──────────────────────────────────────────────────────────────────────────────

def test_evidence_pool_entry_author_year_default_empty():
    """舊 row deserialize 不爆 — author/year default 空字串。"""
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(evidence_id=1, title="T", url="u")
    assert e.author == ""
    assert e.year == ""


def test_evidence_pool_entry_with_author_year():
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(
        evidence_id=1, title="T", url="u", author="王立人", year="2022",
    )
    assert e.author == "王立人"
    assert e.year == "2022"


def test_evidence_pool_legacy_json_deserialize():
    """既有 DB row 沒 author/year → 不爆 + 預設空字串。"""
    from reasoning.schemas_live import deserialize_evidence_pool
    legacy_json = '{"1": {"evidence_id": 1, "title": "T", "url": "u"}}'
    pool = deserialize_evidence_pool(legacy_json)
    assert pool[1].author == ""
    assert pool[1].year == ""


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3.3: _render_section_citations post-process
# ──────────────────────────────────────────────────────────────────────────────

def test_render_citations_author_year_chinese_fullname():
    """OQ-3 CEO 拍板：APA mode 中文 author 整名 render「(王立人, 2022)」。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言",
        section_content="再生能源占比 32.5%{cite:1}，超越預期{cite:2}。",
        sources_used=[1, 2],
        citations=[CitationInline(evidence_id=1), CitationInline(evidence_id=2)],
    )
    lookup = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u1", author="王立人", year="2022"),
        2: EvidencePoolEntry(evidence_id=2, title="T2", url="u2", author="林秀美", year="2021"),
    }
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    assert "(王立人, 2022)" in rendered.section_content
    assert "(林秀美, 2021)" in rendered.section_content
    assert "{cite:1}" not in rendered.section_content
    assert "{cite:2}" not in rendered.section_content


def test_render_citations_numeric():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="占比 32.5%{cite:1}。",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(evidence_id=1, title="T", url="u")}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="numeric",
    )
    assert "[1]" in rendered.section_content
    assert "{cite:1}" not in rendered.section_content


def test_render_citations_footnote():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="占比 32.5%{cite:1}。",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(evidence_id=1, title="T", url="u")}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="footnote",
    )
    assert "¹" in rendered.section_content
    assert "{cite:1}" not in rendered.section_content


def test_render_citations_none_mode_removes_placeholder():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="占比 32.5%{cite:1}。",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(evidence_id=1, title="T", url="u")}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="none",
    )
    assert "{cite:1}" not in rendered.section_content
    assert "[1]" not in rendered.section_content


def test_render_citations_missing_author_fallback_with_note():
    """APA title fallback (2026-06-18): author 空時用文章標題取代作者位置（APA 7th
    標準），**不** fallback source_domain（避免 cna.com.tw 偽裝成 author 誤導 user）。
    title="T"（短於 N）→ 整個標題加全形引號 render「(「T」, n.d.)」。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(
        evidence_id=1, title="T", url="u", source_domain="cna.com.tw",
    )}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    # author 缺 → title 取代（全形引號），year 缺 → n.d.
    assert "(「T」, n.d.)" in rendered.section_content
    # 不再退化「來源不明」（title 有，不該落極端兜底）
    assert "來源不明" not in rendered.section_content
    # RCA 區分仍守：source_domain 不可被當作 author render（cna.com.tw 偽裝為人名）
    assert "(cna.com.tw" not in rendered.section_content
    assert "cna.com.tw" not in rendered.section_content
    assert rendered.methodology_note is not None
    # methodology_note 必須 emit 缺 metadata 警告（明示，no silent fail）
    assert "metadata" in rendered.methodology_note or "缺" in rendered.methodology_note


def test_render_citations_missing_author_counts_in_methodology():
    """methodology_note 應 emit 缺 author 的 citation 數量（多筆缺 author）。
    缺 author 的兩筆改用 title 取代（全形引號），不再「來源不明」。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言",
        section_content="X{cite:1}Y{cite:2}Z{cite:3}.",
        sources_used=[1, 2, 3],
        citations=[
            CitationInline(evidence_id=1),
            CitationInline(evidence_id=2),
            CitationInline(evidence_id=3),
        ],
    )
    lookup = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u1"),  # 缺 author/year
        2: EvidencePoolEntry(evidence_id=2, title="T2", url="u2", author="王立人", year="2022"),
        3: EvidencePoolEntry(evidence_id=3, title="T3", url="u3"),  # 缺 author/year
    }
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    # 兩筆缺 author → title 取代（全形引號），一筆有 author OK
    assert "(「T1」, n.d.)" in rendered.section_content
    assert "(「T3」, n.d.)" in rendered.section_content
    assert "(王立人, 2022)" in rendered.section_content
    # 不再退化「來源不明」（title 有）
    assert "來源不明" not in rendered.section_content
    # methodology_note 應記錄 2 筆缺 author
    note = rendered.methodology_note or ""
    assert "2" in note  # count quoted


def test_render_citations_long_title_truncated_with_ellipsis():
    """APA title fallback：title 超過 N 字 → 截前 N 字 + 全形省略號（引號內）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    # 18 字標題，N=10 → 截前 10 字 + 「…」，整段包全形引號
    long_title = "臺灣農漁村再生能源發展與社區衝突研究"
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(
        evidence_id=1, title=long_title, url="u", published_at="2024-03-01",
    )}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    maxlen = LiveResearchOrchestrator._TITLE_FALLBACK_MAXLEN
    expected = f"(「{long_title[:maxlen]}…」, 2024)"
    assert expected in rendered.section_content
    # 省略號在引號內、後接 year
    assert "…」, 2024)" in rendered.section_content
    assert "來源不明" not in rendered.section_content


def test_render_citations_author_missing_year_from_published_at_no_coupling():
    """連坐解除：author 空但 published_at 有 → title 取代 + year 走 published_at，
    不因 author 缺而連坐 n.d.。render「(「標題」, 2024)」。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(
        evidence_id=1, title="丹麥綠能", url="u", published_at="2024-03-01",
    )}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    # year 從 published_at derive（2024），不因 author 缺連坐 n.d.
    assert "(「丹麥綠能」, 2024)" in rendered.section_content
    assert "n.d." not in rendered.section_content


def test_render_citations_author_missing_title_present_year_missing():
    """連坐解除：author 空、title 有、year/published_at 全缺 → title 取代 + n.d. 並存。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(evidence_id=1, title="某篇文章", url="u")}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    # title 取代 author + n.d. 並存（驗連坐已解除）
    assert "(「某篇文章」, n.d.)" in rendered.section_content
    assert "來源不明" not in rendered.section_content


def test_render_citations_author_and_title_both_empty_extreme_fallback():
    """極端兜底：author 空、title 空、year 空 → 仍「(來源不明, n.d.)」（no silent fail）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    # title 留空（極端，理論上 retrieval 一定有 title）
    lookup = {1: EvidencePoolEntry(evidence_id=1, title="", url="u")}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    assert "(來源不明, n.d.)" in rendered.section_content
    # methodology_note 仍 emit 警告（明示降級）
    note = rendered.methodology_note or ""
    assert "metadata" in note or "缺" in note


def test_render_citations_empty_citations_noop():
    """empty citations list → no-op，content 不變。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="無 placeholder 內容。",
        sources_used=[], citations=[],
    )
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, {}, citation_format="author_year",
    )
    assert rendered.section_content == "無 placeholder 內容。"


def test_render_citations_missing_evidence_id_in_lookup():
    """citation evidence_id 不在 lookup 中 → 移除 placeholder（guard 後續會標 Low）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:99}.",
        sources_used=[99], citations=[CitationInline(evidence_id=99)],
    )
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, {}, citation_format="author_year",
    )
    # placeholder 移除
    assert "{cite:99}" not in rendered.section_content


# ──────────────────────────────────────────────────────────────────────────────
# FIX-3 (Cayenne #10, sprint 2026-05-28): real-retrieval evidence 填 author/year
# → APA inline citation 不再「(來源不明, n.d.)」。
# 三層：(1) _normalize_item 抽 author；(2) EvidencePoolEntry 帶 author；
# (3) render 用 author + 從 published_at 取 year。
# ──────────────────────────────────────────────────────────────────────────────


def test_normalize_item_extracts_author_dict():
    """schema.org author 為 dict({"@type":"Person","name":...}) → 抽 name。"""
    import json
    from reasoning.live_research.loop_engine import BABLoopEngine
    schema = json.dumps({
        "description": "再生能源占比上升。",
        "datePublished": "2023-04-15T08:00:00",
        "author": {"@type": "Person", "name": "王立人"},
    }, ensure_ascii=False)
    item = BABLoopEngine._normalize_item(["https://x.com/a", schema, "標題", "cna"])
    assert item["author"] == "王立人"
    assert item["datePublished"].startswith("2023-04-15")


def test_normalize_item_extracts_author_str_and_list():
    """author 為 str 直接用；為 list 取首筆 name。"""
    import json
    from reasoning.live_research.loop_engine import BABLoopEngine
    item_str = BABLoopEngine._normalize_item(
        ["u", json.dumps({"author": "李四"}), "t", "s"]
    )
    assert item_str["author"] == "李四"
    item_list = BABLoopEngine._normalize_item(
        ["u", json.dumps({"author": [{"@type": "Person", "name": "張三"}]}), "t", "s"]
    )
    assert item_list["author"] == "張三"


def test_normalize_item_missing_author_empty():
    """schema 無 author → item['author'] == '' (不 KeyError)。"""
    import json
    from reasoning.live_research.loop_engine import BABLoopEngine
    item = BABLoopEngine._normalize_item(["u", json.dumps({"description": "x"}), "t", "s"])
    assert item["author"] == ""


def test_render_citations_year_derived_from_published_at():
    """FIX-3 核心：year 欄空但有 published_at → 從 published_at 取年份 render，
    不再退化「來源不明」(real-retrieval evidence 只填 published_at，year 欄常空)。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="占比上升{cite:1}。",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    # author 有、year 欄空、published_at 有 → 應 render (王立人, 2023)
    lookup = {1: EvidencePoolEntry(
        evidence_id=1, title="T", url="u",
        author="王立人", year="", published_at="2023-04-15",
    )}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    assert "(王立人, 2023)" in rendered.section_content
    assert "來源不明" not in rendered.section_content


def test_render_citations_author_and_year_both_present():
    """author + year 欄皆有（year 欄優先於 published_at derive）→ 正常 render。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(
        evidence_id=1, title="T", url="u", author="陳明", year="2021",
    )}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    assert "(陳明, 2021)" in rendered.section_content


def test_render_citations_author_present_no_year_renders_author_with_nd():
    """連坐解除（2026-06-18）：author 有但 year 與 published_at 皆缺 →
    year 走標準 n.d.，author 照常 render「(王立人, n.d.)」。year 缺不再連坐
    author 落「來源不明」。author 未缺 → 不計入 missing_metadata_count。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CitationInline, EvidencePoolEntry,
    )
    section = LiveWriterSectionOutput(
        section_title="前言", section_content="X{cite:1}.",
        sources_used=[1], citations=[CitationInline(evidence_id=1)],
    )
    lookup = {1: EvidencePoolEntry(
        evidence_id=1, title="T", url="u", author="王立人",  # year 空、published_at None
    )}
    rendered = LiveResearchOrchestrator._render_section_citations(
        section, lookup, citation_format="author_year",
    )
    # author 有 → 照常 render；year 缺 → 標準 n.d.（不連坐來源不明）
    assert "(王立人, n.d.)" in rendered.section_content
    assert "來源不明" not in rendered.section_content
    # author 未缺 → 不計入 missing_metadata_count → 無 metadata 缺警告
    assert not rendered.methodology_note


def test_evidence_pool_entry_backward_compat_no_author_key():
    """舊 session JSONB（Track A 寫入、無 author key）model_validate 不爆，
    author/year 落 default 空字串（backward-compat 沿 Optional 擴張 lemma）。
    """
    from reasoning.schemas_live import EvidencePoolEntry
    old_jsonb = {
        "evidence_id": 7,
        "title": "舊報告",
        "url": "https://old.example.com",
        "source_domain": "old.example.com",
        "snippet": "舊 evidence 無 author/year/published_at key。",
        # 故意不含 author / year / published_at / source（模擬舊 schema JSONB）
    }
    e = EvidencePoolEntry.model_validate(old_jsonb)
    assert e.author == ""
    assert e.year == ""
    assert e.published_at is None
    assert e.source == "internal"  # default backward-compat


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3.4: Writer prompt 改 {cite:N} placeholder + strict reject inline
# ──────────────────────────────────────────────────────────────────────────────


def test_writer_prompt_uses_cite_placeholder():
    """Writer prompt 教 LLM 用 {cite:N} placeholder，不自由 render。"""
    from reasoning.prompts.writer import WriterPromptBuilder
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="t", section_outline="o", relevant_findings="f",
        analyst_citations=[1], citation_format="author_year",
    )
    assert "{cite:" in prompt


def test_writer_prompt_strict_no_inline_author_year():
    """OQ-5 CEO 拍板：立刻 strict — prompt 明確禁止 inline 寫 (Author, Year)。"""
    from reasoning.prompts.writer import WriterPromptBuilder
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="t", section_outline="o", relevant_findings="f",
        analyst_citations=[1], citation_format="author_year",
    )
    # 必須含明示「不要寫 (Author, Year)」字眼
    assert "不要" in prompt or "禁止" in prompt or "❌" in prompt


def test_writer_prompt_schema_example_includes_citations():
    """schema 範例 JSON 含 citations 欄位 + {cite:N} placeholder usage."""
    from reasoning.prompts.writer import WriterPromptBuilder
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="t", section_outline="o", relevant_findings="f",
        analyst_citations=[1], citation_format="numeric",
    )
    assert "citations" in prompt
    assert "evidence_id" in prompt


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3.5: Hallucination guard citation_id whitelist check
# ──────────────────────────────────────────────────────────────────────────────


def test_guard_rejects_phantom_citation_evidence_id():
    from reasoning.schemas_live import LiveWriterSectionOutput, CitationInline
    from reasoning.live_research.hallucination_guard import apply_hallucination_guard

    section = LiveWriterSectionOutput(
        section_title="t", section_content="X{cite:99}",
        sources_used=[1],
        citations=[CitationInline(evidence_id=99)],  # 99 不在白名單
    )
    corrected, was_corrected = apply_hallucination_guard(section, valid_evidence_ids={1, 2})
    assert was_corrected
    # phantom citation 已移除
    assert all(c.evidence_id in {1, 2} for c in corrected.citations)
    assert corrected.confidence_level == "Low"


def test_guard_passes_valid_citations():
    """citations 全部 valid → guard 不觸發 correction。"""
    from reasoning.schemas_live import LiveWriterSectionOutput, CitationInline
    from reasoning.live_research.hallucination_guard import apply_hallucination_guard

    section = LiveWriterSectionOutput(
        section_title="t", section_content="X{cite:1}",
        sources_used=[1],
        citations=[CitationInline(evidence_id=1)],
    )
    corrected, was_corrected = apply_hallucination_guard(section, valid_evidence_ids={1, 2})
    assert not was_corrected
    assert corrected is section  # no mutation


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3.6: _normalize_item snippet fallback — internal source articleBody fix
# ──────────────────────────────────────────────────────────────────────────────


def test_normalize_item_internal_source_fills_snippet_from_articleBody():
    """內部語料 schema 只有 articleBody（無 description）→ snippet/description
    必須 fallback 取 articleBody 內文（現況 bug：回傳 ''）。"""
    import json
    from reasoning.live_research.loop_engine import BABLoopEngine
    schema = json.dumps({
        "@type": "NewsArticle",
        "headline": "內部語料標題",
        "url": "https://internal.example/a",
        "articleBody": "這是內部語料的文章本文，應該出現在 snippet。",
        "source": "cna",
    }, ensure_ascii=False)
    item = BABLoopEngine._normalize_item(
        ["https://internal.example/a", schema, "內部語料標題", "cna"]
    )
    assert item["snippet"] == "這是內部語料的文章本文，應該出現在 snippet。"
    assert item["description"] == "這是內部語料的文章本文，應該出現在 snippet。"


def test_normalize_item_web_source_prefers_description_unchanged():
    """web 語料 schema 有 description → 必須優先取 description，articleBody
    不存在時行為不變（回歸保護：不破 web 路徑）。"""
    import json
    from reasoning.live_research.loop_engine import BABLoopEngine
    schema = json.dumps({
        "description": "web 來源的 snippet 內文。",
        "headline": "web 標題",
        "url": "https://web.example/b",
        "provider": "Google Search",
    }, ensure_ascii=False)
    item = BABLoopEngine._normalize_item(
        ["https://web.example/b", schema, "web 標題", "web.example"]
    )
    assert item["snippet"] == "web 來源的 snippet 內文。"
    assert item["description"] == "web 來源的 snippet 內文。"


def test_normalize_item_description_priority_over_articleBody():
    """同時有 description 與 articleBody → description 優先（短路），
    確保 web 行為在邊界 case 下不被 articleBody 污染。"""
    import json
    from reasoning.live_research.loop_engine import BABLoopEngine
    schema = json.dumps({
        "description": "優先的 description。",
        "articleBody": "次要的 articleBody。",
        "url": "https://both.example/c",
    }, ensure_ascii=False)
    item = BABLoopEngine._normalize_item(
        ["https://both.example/c", schema, "標題", "both.example"]
    )
    assert item["snippet"] == "優先的 description。"
    assert item["description"] == "優先的 description。"
