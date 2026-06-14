"""Writer 提示詞 Live Research 強化功能測試。"""
import sys
import os

import pytest
from reasoning.prompts.writer import WriterPromptBuilder


class TestWriterLiveResearch:
    def setup_method(self):
        self.builder = WriterPromptBuilder()

    def test_section_compose_basic(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="第一章：背景與脈絡",
            section_outline="介紹台灣綠能衝突的歷史背景",
            relevant_findings="[1] 台灣2020年開始推動...",
            analyst_citations=[1, 2, 3],
            style_features=None,
            format_spec=None,
            context_map_summary=None
        )
        assert "第一章" in prompt
        assert "分段報告撰寫專家" in prompt
        assert "[1, 2, 3]" in prompt or "1, 2, 3" in prompt

    def test_section_compose_with_style_features(self):
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature
        style = StyleAnalysisOutput(
            features=[
                StyleFeature(dimension="句式結構", observation="平均20字", instruction="保持句子20字以內"),
                StyleFeature(dimension="用詞層次", observation="專業但白話", instruction="避免過度術語"),
                StyleFeature(dimension="段落節奏", observation="先總結後展開", instruction="每段首句為總結"),
            ],
            overall_tone="學術嚴謹但不枯燥"
        )
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            style_features=style,
            format_spec=None,
            context_map_summary=None
        )
        assert "句式結構" in prompt
        assert "學術嚴謹但不枯燥" in prompt
        assert "保持句子20字以內" in prompt

    def test_citation_format_numeric_instruction(self):
        """citation_format='numeric' 應產生數字編號 [N] 指示。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1, 2],
            citation_format="numeric",
        )
        assert "[N]" in prompt or "數字編號" in prompt
        # 不應該再有 author_year 字面 placeholder 警告
        assert "(Author, Year)" not in prompt
        assert "字面 placeholder" not in prompt

    def test_citation_format_author_year_instruction(self):
        """citation_format='author_year' 應產生 APA-style 指示。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1, 2],
            citation_format="author_year",
        )
        assert "APA" in prompt or "作者" in prompt
        assert "字面 placeholder" not in prompt

    def test_citation_format_footnote_instruction(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            citation_format="footnote",
        )
        assert "腳註" in prompt

    def test_citation_format_none_instruction(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            citation_format="none",
        )
        # 應該明確說不需要引用標記
        assert "不需要引用" in prompt or "不需要" in prompt

    def test_no_negative_placeholder_workaround(self):
        """Negative example workaround（commit 6bad26d）應該移除。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            style_features=None,
        )
        # 整段 negative example workaround 應該被移除
        assert "嚴禁字面 placeholder 字串" not in prompt
        assert "(Author, Year)" not in prompt
        assert "(作者, 年份)" not in prompt

    def test_citation_format_from_style_features(self):
        """citation_format 未明示時應從 style_features.citation_format 取用。"""
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature
        style = StyleAnalysisOutput(
            features=[
                StyleFeature(dimension=f"d{i}", observation=f"o{i}", instruction=f"i{i}")
                for i in range(3)
            ],
            overall_tone="formal",
            citation_format="footnote",
        )
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            style_features=style,
            # citation_format intentionally not passed
        )
        assert "腳註" in prompt

    def test_citation_format_invalid_raises(self):
        """無效的 citation_format 必須 raise，不可 silent fail（CLAUDE.md 規則）。"""
        with pytest.raises(ValueError):
            self.builder.build_section_compose_prompt(
                section_title="Test", section_outline="Test",
                relevant_findings="[1] data",
                analyst_citations=[1],
                citation_format="apa",  # invalid
            )

    def test_section_compose_with_format_spec(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            style_features=None,
            format_spec="使用 APA 引用格式，每段不超過 200 字",
            context_map_summary=None
        )
        assert "APA" in prompt

    def test_section_compose_boundary_on_findings(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] potentially dangerous content",
            analyst_citations=[1],
            style_features=None,
            format_spec=None,
            context_map_summary=None
        )
        assert "_START]" in prompt  # boundary token

    def test_section_compose_with_context_map_summary(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            style_features=None,
            format_spec=None,
            context_map_summary="## 研究結構 (v2)\n研究問題: 台灣綠能"
        )
        assert "台灣綠能" in prompt

    def test_section_compose_output_schema_mentioned(self):
        """Prompt must reference the output schema fields."""
        prompt = self.builder.build_section_compose_prompt(
            section_title="Test", section_outline="Test",
            relevant_findings="[1] data",
            analyst_citations=[1],
            style_features=None,
            format_spec=None,
            context_map_summary=None
        )
        assert "LiveWriterSectionOutput" in prompt or "section_content" in prompt

    def test_existing_compose_unchanged(self):
        """確認既有的 build_compose_prompt 在沒有 live research 參數時仍正常運作。"""
        from reasoning.schemas import CriticReviewOutput
        review = CriticReviewOutput(
            status="PASS",
            critique="Good work. The draft is well-structured and covers all key points with solid evidence.",
            suggestions=[], mode_compliance="符合",
            logical_gaps=[], source_issues=[]
        )
        prompt = self.builder.build_compose_prompt(
            analyst_draft="test", critic_review=review,
            analyst_citations=[1], mode="discovery",
            user_query="test", suggested_confidence="High"
        )
        assert "報告編輯" in prompt  # 既有角色

    # ============================================================
    # spec §4.10: special_elements 強制紀律 block (2026-05-16)
    # ============================================================

    def test_section_prompt_renders_special_elements_block(self):
        """收到 special_elements_for_chapter 非空 → emit 強制紀律 block，
        含「必須」語氣 + markdown table syntax 範例。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="結果與討論",
            section_outline="跨國比較",
            relevant_findings="[1] data",
            analyst_citations=[1],
            special_elements_for_chapter=[
                {
                    "type": "table",
                    "target_chapter": "結果與討論",
                    "description": "5 國能源使用率比較",
                },
            ],
        )
        # 紀律 block heading
        assert "必須包含的特殊格式 element" in prompt
        # 紀律語氣（必須 / hard requirement）— 不可弱化為「建議」
        assert "必須" in prompt
        assert "建議生成" not in prompt
        assert "建議使用表格" not in prompt
        # element description 注入
        assert "5 國能源使用率比較" in prompt
        # markdown table syntax 範例（pipe-delimited）
        assert "| 國家 |" in prompt or "| --- |" in prompt

    def test_section_prompt_no_block_when_special_elements_empty(self):
        """special_elements_for_chapter 空 list / None → prompt 不含紀律 block。"""
        prompt_none = self.builder.build_section_compose_prompt(
            section_title="前言",
            section_outline="...",
            relevant_findings="[1] data",
            analyst_citations=[1],
            special_elements_for_chapter=None,
        )
        assert "必須包含的特殊格式 element" not in prompt_none

        prompt_empty = self.builder.build_section_compose_prompt(
            section_title="前言",
            section_outline="...",
            relevant_findings="[1] data",
            analyst_citations=[1],
            special_elements_for_chapter=[],
        )
        assert "必須包含的特殊格式 element" not in prompt_empty

    def test_section_prompt_multiple_special_elements_rendered(self):
        """多個 element 都應在 prompt 內列出。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="結果",
            section_outline="...",
            relevant_findings="[1] data",
            analyst_citations=[1],
            special_elements_for_chapter=[
                {"type": "table", "target_chapter": "結果",
                 "description": "5 國比較表"},
                {"type": "list", "target_chapter": "結果",
                 "description": "政策建議三點"},
            ],
        )
        assert "5 國比較表" in prompt
        assert "政策建議三點" in prompt
        assert "table" in prompt
        assert "list" in prompt


# =====================================================================
# Plan: lr-user-voice-container-and-4-fixes (Phase 4, Fix I-1)
# =====================================================================

class TestRevisionInstructionBlock:
    """Fix I-1: writer prompt 加 `## 段落修改指示` block 條件式注入。

    CEO OQ 2 拍板：revise_instructions 是 List[str] accumulate。
    Writer prompt 把全 list 串起來給 LLM（保留 user incremental 訴求 context）。
    """

    def setup_method(self):
        self.builder = WriterPromptBuilder()

    def test_revise_instruction_emits_block(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="離岸風電",
            section_outline="背景與爭議",
            relevant_findings="f",
            analyst_citations=[1],
            citation_format="numeric",
            revise_instruction="第 3 段太短，請補 IAEA 數據",
            prior_section_content="前一版段落內容...",
        )
        assert "## 段落修改指示" in prompt
        assert "第 3 段太短，請補 IAEA 數據" in prompt
        # 前一版內容也 inject
        assert "前一版段落內容" in prompt

    def test_no_revise_instruction_no_block(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="離岸風電",
            section_outline="o",
            relevant_findings="f",
            analyst_citations=[1],
            citation_format="numeric",
        )
        assert "## 段落修改指示" not in prompt

    def test_empty_revise_instruction_no_block(self):
        prompt = self.builder.build_section_compose_prompt(
            section_title="離岸風電",
            section_outline="o",
            relevant_findings="f",
            analyst_citations=[1],
            citation_format="numeric",
            revise_instruction="",
        )
        assert "## 段落修改指示" not in prompt

    def test_revise_instruction_without_prior_content_still_works(self):
        """單一 instruction 沒有 prior content（edge case）也要 emit block。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="X",
            section_outline="o",
            relevant_findings="f",
            analyst_citations=[1],
            citation_format="numeric",
            revise_instruction="第 2 段論點不清楚",
        )
        assert "## 段落修改指示" in prompt
        assert "第 2 段論點不清楚" in prompt
        # 沒 prior content 時不該出現「上一版段落內容」block
        assert "上一版段落內容" not in prompt


# ============================================================================
# Plan: lr-stage5-writer-revise-obedience (2026-06-01)
# Adversarial 測試發現 Stage 5 writer 對「具體修改要求」服從度低：
#   Case B: revise「縮短到 200 字」→ 從 ~1925 字只降到 ~1910（無視字數約束）
#   Case A: revise「加具體數字」但 evidence 無數字 → 用「相當數量」模糊詞充數
# Root cause: outline_block「本章目標字數約 N 字…不要明顯過短」與 revision_block
#   並存且衝突，且 outline 字數 budget 語氣壓過 revise 訴求；revise block 也缺
#   「revise 訴求優先於 outline 預設」優先級宣告 + 「誠實執行或誠實拒絕」指引。
# ============================================================================


class TestRevisePriorityOverOutline:
    """revise 訴求與 outline 預設字數衝突時，revise 訴求 override outline。
    且 revise block 含「誠實執行或誠實拒絕」指引（不用模糊詞充數、不捏造）。
    """

    def setup_method(self):
        self.builder = WriterPromptBuilder()

    def _book_outline(self, target_word_count=1800):
        from reasoning.schemas_live import BookOutline, ChapterPlan
        return BookOutline(
            chapters=[
                ChapterPlan(
                    chapter_index=0, title="前言", brief="鋪陳動機",
                    target_word_count=500, planned_evidence_ids=[1],
                    transition_hint="", role="intro",
                ),
                ChapterPlan(
                    chapter_index=1, title="本章", brief="深入論證",
                    target_word_count=target_word_count, planned_evidence_ids=[1],
                    transition_hint="", role="body",
                ),
            ],
            overall_arc="從背景到論證",
        )

    def test_revise_block_declares_priority_over_outline_word_budget(self):
        """revise path 下 revise block 必須宣告「與 outline 目標字數衝突時以本訴求為準」。
        這是優先級宣告，解 Case B（outline 字數壓過 revise）。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="本章", section_outline="深入論證",
            relevant_findings="[1] data",
            analyst_citations=[1],
            citation_format="numeric",
            book_outline=self._book_outline(),
            current_chapter_index=1,
            revise_instruction="第 3 段縮短到 200 字以內",
            prior_section_content="（一段很長的 1900 字內容）",
        )
        # outline 字數那句仍會出現（main loop 行為不變）
        assert "本章目標字數" in prompt
        # 但 revise block 必須明確宣告優先級（override outline 預設字數）
        assert "以本修改訴求為準" in prompt or "以本訴求為準" in prompt
        # 優先級宣告必須提到「字數」與 outline 衝突情境
        assert "目標字數" in prompt

    def test_revise_block_has_honest_execution_or_refusal_guidance(self):
        """revise block 必須含「做不到就誠實說明、不要用模糊詞充數、不要捏造」指引。
        解 Case A（evidence 無數字 → 用「相當數量」充數）。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="本章", section_outline="深入論證",
            relevant_findings="[1] data",
            analyst_citations=[1],
            citation_format="numeric",
            book_outline=self._book_outline(),
            current_chapter_index=1,
            revise_instruction="第 2 段加具體數字",
            prior_section_content="（無數字的一段）",
        )
        # 誠實說明資訊不在資料範圍內
        assert "不在資料範圍" in prompt or "不在 evidence" in prompt or "資料範圍內" in prompt
        # 明確禁止模糊量詞充數（點名範例）
        assert "相當數量" in prompt or "模糊" in prompt
        # 禁止捏造
        assert "捏造" in prompt

    def test_revise_word_constraint_is_hard_upper_limit(self):
        """revise 訴求含字數約束時，prompt 須讓 LLM 知道是硬上限（非『不要明顯過短』反向）。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="本章", section_outline="深入論證",
            relevant_findings="[1] data",
            analyst_citations=[1],
            citation_format="numeric",
            book_outline=self._book_outline(),
            current_chapter_index=1,
            revise_instruction="縮短到 200 字以內",
            prior_section_content="（一段很長內容）",
        )
        # 字數約束須被當作硬上限執行（revise block 內專屬措辭，非 BINDING block 的「嚴格遵守」）
        assert "硬性上限" in prompt or "硬上限" in prompt

    def test_non_revise_path_outline_word_budget_unchanged(self):
        """regression: 非 revise path（main writer loop）outline 字數 budget 行為不變。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="本章", section_outline="深入論證",
            relevant_findings="[1] data",
            analyst_citations=[1],
            citation_format="numeric",
            book_outline=self._book_outline(),
            current_chapter_index=1,
            # no revise_instruction
        )
        # main loop：outline 字數那句仍正常出現，含 ±15% / 不要明顯過短
        assert "本章目標字數" in prompt
        assert "明顯過短" in prompt
        # 沒有 revise block → 不該有優先級宣告
        assert "以本修改訴求為準" not in prompt
        assert "## 段落修改指示" not in prompt


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — Task 4:
# writer prompt grounding discipline + 移除綠燈
# ============================================================================


class TestTrackAWriterGroundingDiscipline:
    """Track A Task 4: 移除 chapter_override_notice 綠燈 + 加 grounding discipline block
    (whitelist 非空 → grounded discipline; whitelist 空 → 資料不足 + 禁止硬塞 [N])。
    Gemini Imp-1: low confidence findings 紀律 (保留/推測語氣)。
    """

    def setup_method(self):
        self.builder = WriterPromptBuilder()

    def test_writer_prompt_contains_grounding_discipline_block(self):
        """whitelist 非空時 grounding discipline 紀律出現 (含具體 entity / llm_knowledge 標記)。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="國外案例", section_outline="x",
            relevant_findings="### [1] T1\n- 推論：c",
            analyst_citations=[1],
        )
        assert "Grounding 紀律" in prompt or "grounding 紀律" in prompt.lower()
        assert "嚴格禁止編造" in prompt or "禁止虛構" in prompt
        assert "背景" in prompt  # llm_knowledge tagging 紀律

    def test_writer_prompt_empty_whitelist_emits_insufficient_data_discipline(self):
        """whitelist 空 → 「本章資料不足」紀律 + 禁止硬塞 [N] (不再有「敘事性、總結性」綠燈)。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="國外案例", section_outline="x",
            relevant_findings="", analyst_citations=[],
            is_chapter_override=True,
        )
        assert "本章資料不足" in prompt
        # 不再有綠燈措辭
        assert "可以使用敘事性" not in prompt
        # 必須明示禁止硬塞 [N] (prompt 層防禦 — 不依賴 guard subset check 兜底)
        assert "禁止硬塞" in prompt or "不可輸出任何" in prompt
        # 引用編號類關鍵字出現
        assert "[N]" in prompt or "引用編號" in prompt

    def test_writer_prompt_removed_chapter_override_notice(self):
        """chapter_override_notice 綠燈整段應被移除 (改由統一 grounding discipline 取代)。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="x", section_outline="y",
            relevant_findings="", analyst_citations=[],
            is_chapter_override=True,
        )
        # 原綠燈措辭應全部消失
        assert "可以使用敘事性、總結性語句" not in prompt
        assert "不要強行加 [N] 引用標記" not in prompt

    def test_writer_prompt_contains_low_confidence_discipline(self):
        """Gemini Imp-1 拍板: whitelist 非空時 grounding discipline block 必須含
        low confidence findings 處理紀律 (保留/推測語氣 + 禁止絕對語氣)。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="x", section_outline="y",
            relevant_findings="### [1] T1\n- [confidence: low | critic_status: WARN] 推論：c",
            analyst_citations=[1],
        )
        # 紀律條文出現
        assert "low confidence" in prompt or "保留" in prompt or "推測" in prompt
        # 保留語氣詞範例 (至少 1 個)
        assert any(w in prompt for w in (
            "部分跡象顯示", "可能", "初步觀察", "有研究指出",
            "目前的有限資料顯示", "尚待更多證據確認",
        ))
        # 禁止絕對語氣詞範例 (至少 1 個)
        assert any(w in prompt for w in (
            "事實上", "研究證實", "明確顯示", "確實如此", "無疑",
        ))

    def test_writer_prompt_emits_ungrounded_entity_revision_block(self):
        """Track A Task 5: ungrounded_entities_revision 非空時 prompt 出現 revision 指示。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="t", section_outline="o",
            relevant_findings="f", analyst_citations=[1],
            ungrounded_entities_revision=["弗萊堡", "Horns Rev"],
        )
        assert "弗萊堡" in prompt and "Horns Rev" in prompt
        assert "evidence 中" in prompt and "無對應" in prompt
        # Fix3：引導移除整個無據陳述句（取代舊「改用泛論」誘導）
        assert "移除整個" in prompt

    def test_writer_prompt_omits_ungrounded_block_when_none(self):
        """ungrounded_entities_revision=None → 不出現 revision block (backward compat)。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="t", section_outline="o",
            relevant_findings="f", analyst_citations=[1],
        )
        assert "Ungrounded Entity 重寫指示" not in prompt


class TestTrackAPriorUsedEntitiesBlock:
    """Track A Task 7: 跨章 coherence — prior_used_entities 注入綜合章 prompt。"""

    def setup_method(self):
        self.builder = WriterPromptBuilder()

    def test_writer_prompt_emits_prior_used_entities_block_for_conclusion(self):
        """conclusion 章 (role=conclusion) 注入 prior_used_entities → prompt 出現綜合章紀律。"""
        from reasoning.schemas_live import BookOutline, ChapterPlan
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="結論", brief="綜合討論",
                        planned_evidence_ids=[1], role="conclusion"),
        ], overall_arc="x", redundancy_warnings=[])
        prompt = self.builder.build_section_compose_prompt(
            section_title="結論", section_outline="綜合",
            relevant_findings="f", analyst_citations=[1],
            is_chapter_override=False,
            book_outline=book_outline,
            current_chapter_index=1,
            prior_used_entities=["丹麥", "台電", "經濟部"],
        )
        assert "丹麥" in prompt and "台電" in prompt and "經濟部" in prompt
        assert (
            "嚴格禁止引入前文未提及" in prompt
            or "只能參考前文已出現的實體" in prompt
        )

    def test_writer_prompt_emits_block_when_brief_contains_synthesis_keyword(self):
        """brief 含「綜合」「結論」「討論」也觸發跨章紀律 (即使 role 不是 conclusion)。"""
        from reasoning.schemas_live import BookOutline, ChapterPlan
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="綜合分析", brief="跨案綜合比較",
                        planned_evidence_ids=[1], role="body"),
            ChapterPlan(chapter_index=2, title="結論", brief="x",
                        planned_evidence_ids=[1], role="conclusion"),
        ], overall_arc="x", redundancy_warnings=[])
        prompt = self.builder.build_section_compose_prompt(
            section_title="綜合分析", section_outline="跨案綜合比較",
            relevant_findings="f", analyst_citations=[1],
            book_outline=book_outline,
            current_chapter_index=1,
            prior_used_entities=["丹麥"],
        )
        # brief 含「綜合」→ 觸發紀律
        assert "丹麥" in prompt
        assert (
            "嚴格禁止引入前文未提及" in prompt
            or "只能參考前文已出現的實體" in prompt
        )

    def test_writer_prompt_omits_block_for_body_chapter_without_synthesis_keyword(self):
        """body chapter 且 brief 不含 synthesis keyword → 不出綜合章紀律 (允許引入新實體)。"""
        from reasoning.schemas_live import BookOutline, ChapterPlan
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="國外案例", brief="他國綠能案例",
                        planned_evidence_ids=[1], role="body"),
            ChapterPlan(chapter_index=2, title="結論", brief="x",
                        planned_evidence_ids=[1], role="conclusion"),
        ], overall_arc="x", redundancy_warnings=[])
        prompt = self.builder.build_section_compose_prompt(
            section_title="國外案例", section_outline="他國案例",
            relevant_findings="f", analyst_citations=[1],
            book_outline=book_outline,
            current_chapter_index=1,
            prior_used_entities=["丹麥"],
        )
        # body + 沒 synthesis keyword → 不應出現綜合章紀律
        assert "嚴格禁止引入前文未提及" not in prompt
        assert "只能參考前文已出現的實體" not in prompt

    def test_writer_prompt_omits_block_when_prior_entities_none(self):
        """prior_used_entities=None → 不出現 block (backward compat)。"""
        from reasoning.schemas_live import BookOutline, ChapterPlan
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="結論", brief="綜合",
                        planned_evidence_ids=[1], role="intro"),
        ], overall_arc="x", redundancy_warnings=[])
        prompt = self.builder.build_section_compose_prompt(
            section_title="結論", section_outline="綜合",
            relevant_findings="f", analyst_citations=[1],
            book_outline=book_outline,
            current_chapter_index=0,
        )
        assert "跨章 coherence 紀律" not in prompt
        assert "只能參考前文已出現的實體" not in prompt


class TestTrackEWriterBindingBlock:
    """Track E (sprint 2026-05-28) E5: Writer prompt BINDING block — 強制時間約束。"""

    def setup_method(self):
        self.builder = WriterPromptBuilder()

    def test_writer_prompt_includes_binding_block_when_time_constraint_set(self):
        """time_constraint 設 → writer prompt 含 BINDING block。"""
        from reasoning.schemas_live import TimeRange
        tc = TimeRange(start_date="2024-01-01", raw_phrase="2024 之後", user_selected=True)
        prompt = self.builder.build_section_compose_prompt(
            section_title="國外案例",
            section_outline="…",
            relevant_findings="[1] xxx",
            analyst_citations=[1],
            time_constraint=tc,
        )
        assert "強制時間約束" in prompt or "BINDING TIME CONSTRAINT" in prompt
        assert "2024-01-01" in prompt
        assert "2024 之後" in prompt

    def test_writer_prompt_no_binding_block_when_time_constraint_none(self):
        """time_constraint=None → writer prompt 不含 BINDING block。"""
        prompt = self.builder.build_section_compose_prompt(
            section_title="國外案例",
            section_outline="…",
            relevant_findings="[1] xxx",
            analyst_citations=[1],
            time_constraint=None,
        )
        assert "強制時間約束" not in prompt
        assert "BINDING TIME CONSTRAINT" not in prompt

    def test_writer_prompt_binding_block_strict_when_user_selected(self):
        """user_selected=True → BINDING block 強度升級為 STRICT 措辭。"""
        from reasoning.schemas_live import TimeRange
        tc_strict = TimeRange(
            start_date="2024-01-01", raw_phrase="2024 後", user_selected=True
        )
        prompt_strict = self.builder.build_section_compose_prompt(
            section_title="x",
            section_outline="…",
            relevant_findings="",
            analyst_citations=[],
            time_constraint=tc_strict,
        )
        assert "絕對禁止" in prompt_strict or "嚴格禁止" in prompt_strict

    def test_writer_prompt_binding_block_renders_even_when_whitelist_empty(self):
        """E-AMB-5 拍板：whitelist 空時 BINDING block 也注入（兩 path 都注入）。"""
        from reasoning.schemas_live import TimeRange
        tc = TimeRange(start_date="2024-01-01", raw_phrase="2024 後", user_selected=True)
        prompt = self.builder.build_section_compose_prompt(
            section_title="國外案例",
            section_outline="…",
            relevant_findings="",  # 空
            analyst_citations=[],  # 空
            time_constraint=tc,
        )
        assert "強制時間約束" in prompt


# ============================================================================
# Track C C5 (sprint 2026-05-28) — writer prompt Tier 6 source-type discipline
# ============================================================================


def test_writer_prompt_grounding_block_contains_tier6_source_discipline():
    """grounding_discipline_block (whitelist 非空 path) 含 Tier 6 source-type 紀律段 (Track C C5).

    驗 prompt 紀律段（非僅 findings echo）明標：
    - llm_knowledge 來源不可寫成「研究指出」
    - encyclopedia (Wikipedia) 來源必須明示是 Wikipedia
    """
    from reasoning.prompts.writer import WriterPromptBuilder
    builder = WriterPromptBuilder()
    # findings 故意不含 Tier 6 前綴 — 確保紀律段是 prompt 結構固定的，不是 findings echo
    prompt = builder.build_section_compose_prompt(
        section_title="x",
        section_outline="x",
        relevant_findings="[1] 一般站內 evidence",
        analyst_citations=[1],
    )
    # Track C C5: 紀律段必須提及 Tier 6 source-type 區分紀律
    assert "Tier 6" in prompt, (
        "Writer prompt grounding block should mention Tier 6 source-type discipline"
    )
    # 紀律段必須明標 llm_knowledge 來源引用紀律
    assert "llm_knowledge" in prompt, (
        "Writer prompt grounding block should mention llm_knowledge source-type handling"
    )
    # 紀律段必須提 Wikipedia / encyclopedia 來源引用紀律
    assert "encyclopedia" in prompt or "Wikipedia" in prompt, (
        "Writer prompt grounding block should mention Wikipedia/encyclopedia source-type handling"
    )


# ── Cayenne A: writer 正向強制具體化（grounding block 非空分支）──
def test_grounding_block_has_positive_specificity_imperative():
    """evidence 非空時，prompt 必須含正向強制：主動寫出 evidence 內具體 entity，不准退回抽象總結。"""
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="國外案例文獻",
        section_outline="分析國際案例的衝突起因與化解機制",
        relevant_findings="### [1] 德國某風場（snippet）\n- 論點：回饋金為每年 2 萬歐元",
        analyst_citations=[1],
        citation_format="author_year",
    )
    # 正向強制具體化（A 的核心修法）
    assert "必須主動寫出" in prompt or "主動把" in prompt
    assert "具體" in prompt and ("地名" in prompt or "數字" in prompt or "法規" in prompt)
    assert "不得退回" in prompt or "不可退回" in prompt or "不准退回" in prompt
    # 不可破壞既有禁止式 grounding（regression）
    assert "嚴格禁止編造" in prompt


# ── Cayenne B(b): synthesis 章注入所有前章摘要 ──
def test_synthesis_block_injects_all_prior_chapter_summaries():
    """synthesis 章（role=conclusion）prompt 含所有前章摘要 + 開場 recap 約束。"""
    from reasoning.schemas_live import BookOutline, ChapterPlan
    # 注意：BookOutline schema 禁 body 在 index 0（intro 位置）→ ch0 用 intro。
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="國內案例", role="intro", brief="台灣案例"),
        ChapterPlan(chapter_index=1, title="國外案例", role="body", brief="國際案例"),
        ChapterPlan(chapter_index=2, title="結果與討論", role="conclusion", brief="綜合討論"),
    ])
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="結果與討論",
        section_outline="綜合國內外案例討論啟示",
        relevant_findings="### [1] T（snippet）\n- 論點：x",
        analyst_citations=[1],
        citation_format="author_year",
        book_outline=book_outline,
        current_chapter_index=2,
        prior_used_entities=["苗栗", "德國北萊茵"],
        all_prior_chapter_summaries=[
            "第1章：苗栗某案場因回饋金分配引發爭議。",
            "第2章：德國北萊茵以社區合作社化解衝突。",
        ],
    )
    # 前章實際內容被注入（不只 entity 名稱）
    assert "苗栗某案場因回饋金分配" in prompt
    assert "德國北萊茵以社區合作社化解" in prompt
    # 開場 recap 約束（user 逐字想逼 LLM 做的）
    assert "開場" in prompt and ("先" in prompt)
    # 既有 entity 約束仍在（regression）
    assert "嚴格禁止引入前文未提及的新案例" in prompt


def test_non_synthesis_chapter_no_prior_summaries_block():
    """body 章（非 synthesis）不注入 all_prior_chapter_summaries block。"""
    from reasoning.schemas_live import BookOutline, ChapterPlan
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="前言", role="intro", brief="背景"),
        ChapterPlan(chapter_index=1, title="國內案例", role="body", brief="台灣案例"),
    ])
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="國內案例", section_outline="x", relevant_findings="### [1] T（s）\n- 論點：y",
        analyst_citations=[1], citation_format="author_year",
        book_outline=book_outline, current_chapter_index=1,
        prior_used_entities=["前言提到的概念"],
        all_prior_chapter_summaries=["第1章前言摘要"],
    )
    assert "## 跨章 coherence 紀律" not in prompt  # body 章不觸發 synthesis block


def test_ungrounded_revision_block_guides_removal_not_vagueness():
    from reasoning.prompts.writer import WriterPromptBuilder
    b = WriterPromptBuilder()
    prompt = b.build_section_compose_prompt(
        section_title="t", section_outline="o", relevant_findings="f",
        analyst_citations=[1], ungrounded_entities_revision=["台泥"],
    )
    assert "移除" in prompt and "整個" in prompt        # 引導移除整句
    assert "模糊" in prompt or "代稱" in prompt          # 禁模糊化
    assert "改用泛論" not in prompt                       # 舊誘導已移除
    assert "本章資料不足" not in prompt                   # R4：死胡同系統標籤指示已移除
    assert "資料不足" not in prompt                       # R4：不教 LLM 發明任何「資料不足」字樣


# ============================================================================
# 模塊5 Task 5: 條件式 writer calibration（通道 B）
# 薄弱章（thin）放行保守措辭；充足章（ok）維持逼具體；critical 章走既有「資料不足」branch
# ============================================================================


def test_thin_chapter_prompt_has_conservative_calibration():
    """薄弱章（evidence_sufficiency='thin'）writer prompt 應含保守指示：
    允許說此面向證據有限、不要硬編具體數字 / 案例名。"""
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="某子議題", section_outline="o",
        relevant_findings="[1] 某來源片段", analyst_citations=[1],
        citation_format="numeric",
        evidence_sufficiency="thin",
    )
    # 保守 calibration block 的標誌字串
    assert "證據有限" in prompt
    assert "不要硬編" in prompt or "不可虛構具體" in prompt


def test_sufficient_chapter_prompt_no_conservative_calibration():
    """充足章（evidence_sufficiency='ok'）不應放保守指示，
    維持既有逼具體紀律（grounding block 第 0 點仍在）。"""
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="某子議題", section_outline="o",
        relevant_findings="[1] a [2] b [3] c [4] d",
        analyst_citations=[1, 2, 3, 4],
        citation_format="numeric",
        evidence_sufficiency="ok",
    )
    # 不含保守 calibration block
    assert "本章證據有限" not in prompt
    # 既有逼具體紀律仍在（grounding block 第 0 點）
    assert "具體化是硬要求" in prompt


def test_none_sufficiency_backward_compatible():
    """evidence_sufficiency=None（既有 caller 不帶）→ 行為不變（不加 calibration block）。"""
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="t", section_outline="o",
        relevant_findings="[1] x", analyst_citations=[1],
        citation_format="numeric",
    )
    assert "本章證據有限" not in prompt


def test_critical_chapter_no_calibration_uses_existing_branch():
    """critical 章（whitelist 空、evidence_sufficiency='critical'）**不**加 calibration block——
    交給既有「資料不足」branch 處理，避免雙重 block 文字 / 語氣衝突
    （reviewer Gemini 第二輪 Should-Fix：calibration 只施於 thin，critical 走既有 branch）。"""
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="某子議題", section_outline="o",
        relevant_findings="", analyst_citations=[],
        citation_format="numeric",
        evidence_sufficiency="critical",
    )
    # critical 章不放 calibration 保守 block（calibration 只施於 thin）
    assert "本章證據充分度校準" not in prompt
    assert "本章證據有限" not in prompt
