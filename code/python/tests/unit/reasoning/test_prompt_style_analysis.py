"""Style Analysis 提示詞建構器測試。"""
import pytest
from reasoning.prompts.style_analysis import StyleAnalysisPromptBuilder


class TestStyleAnalysisPrompt:
    def setup_method(self):
        self.builder = StyleAnalysisPromptBuilder()

    def test_basic_prompt_contains_sample(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="這是一篇範本文章。語氣嚴謹但不枯燥。"
        )
        assert "範本文章" in prompt
        assert "文筆分析" in prompt

    def test_boundary_isolation_on_sample(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="Sample text"
        )
        assert "_START]" in prompt  # boundary token

    def test_has_analysis_dimensions(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        assert "句式結構" in prompt
        assert "用詞層次" in prompt
        assert "段落節奏" in prompt

    def test_has_actionable_instruction_guidance(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        assert "具體的寫作指令" in prompt or "可操作" in prompt

    def test_sample_content_present_in_output(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="獨特識別文字XYZ"
        )
        assert "獨特識別文字XYZ" in prompt

    def test_role_definition_present(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        assert "文筆分析專家" in prompt

    def test_output_schema_mentioned(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        # Should mention StyleAnalysisOutput schema fields
        assert "StyleAnalysisOutput" in prompt or "features" in prompt or "overall_tone" in prompt

    def test_all_seven_dimensions_covered(self):
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        assert "論證風格" in prompt
        assert "語氣和立場" in prompt
        assert "引用習慣" in prompt
        assert "結構偏好" in prompt

    def test_instruction_example_present(self):
        """Prompt should include example of observation -> instruction transformation."""
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        # The plan specifies showing observation vs instruction example
        assert "觀察" in prompt
        assert "指令" in prompt

    def test_citation_format_enum_classification_instructions(self):
        """Prompt should instruct LLM to classify citation format into enum."""
        prompt = self.builder.build_style_analysis_prompt(
            writing_sample="test"
        )
        # Must explicitly enumerate the four allowed enum values
        assert "author_year" in prompt
        assert "numeric" in prompt
        assert "footnote" in prompt
        assert "none" in prompt
        # Must mention the field name so LLM knows where to emit it
        assert "citation_format" in prompt

    def test_has_input_type_guard(self):
        """Prompt 必須含 input-type 逃生口：若輸入是指令/閒聊而非範本，回報非範本。"""
        prompt = self.builder.build_style_analysis_prompt(writing_sample="語氣再生動一點")
        # 守門條款關鍵字：輸入可能不是範本
        assert "不一定真的是" in prompt  # 守門段獨有句，現行 prompt 不存在
        # 必須指示 LLM 用 input_is_writing_sample 欄位回報
        assert "input_is_writing_sample" in prompt

    def test_guard_mentions_instruction_and_chitchat_cases(self):
        """守門段必須具體列舉「調整指令」與「閒聊」兩類誤貼情境。
        注意：不可只斷言「指令」——現行 prompt 已含「寫作指令」「操作指令」，
        改動前就 PASS，無辨別力（review N1）。"""
        prompt = self.builder.build_style_analysis_prompt(writing_sample="test")
        assert "調整指令" in prompt
        assert "閒聊" in prompt
