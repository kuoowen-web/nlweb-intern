"""
AssociatorAgent prompt builder tests.

TDD: These tests define expected behavior. Run first to verify FAIL, then implement.
"""
import pytest
from reasoning.prompts.associator import AssociatorPromptBuilder
from reasoning.schemas_live import ContextMap, ContextMapTopic, ContextMapSearchSeed


class TestBuildContextMapPrompt:
    def setup_method(self):
        self.builder = AssociatorPromptBuilder()

    def test_basic_prompt_contains_query(self):
        prompt = self.builder.build_context_map_prompt(
            query="台灣綠能衝突的國外案例",
            initial_context=None,
            user_prior_knowledge=None
        )
        assert "台灣綠能衝突的國外案例" in prompt
        assert "研究結構設計師" in prompt

    def test_with_initial_context_has_boundary(self):
        prompt = self.builder.build_context_map_prompt(
            query="test",
            initial_context="[1] Some initial data...",
            user_prior_knowledge=None
        )
        assert "_START]" in prompt  # boundary token marker
        assert "[1] Some initial data" in prompt

    def test_without_initial_context_no_boundary(self):
        prompt = self.builder.build_context_map_prompt(
            query="test",
            initial_context=None,
            user_prior_knowledge=None
        )
        # Without initial_context, no boundary injection should happen
        # (the context isolation block should be absent)
        assert "_START]" not in prompt

    def test_with_user_prior_knowledge(self):
        prompt = self.builder.build_context_map_prompt(
            query="test",
            initial_context=None,
            user_prior_knowledge="我已經整理了台灣的衝突案例"
        )
        assert "已經整理了台灣" in prompt

    def test_without_user_prior_knowledge_block_absent(self):
        prompt = self.builder.build_context_map_prompt(
            query="test",
            initial_context=None,
            user_prior_knowledge=None
        )
        # user prior knowledge block should not appear if not provided
        assert "先備知識" not in prompt

    def test_narration_instruction_present(self):
        prompt = self.builder.build_context_map_prompt(
            query="test", initial_context=None, user_prior_knowledge=None
        )
        assert "narration" in prompt.lower() or "敘述" in prompt

    def test_cross_domain_thinking_guidance_present(self):
        # The prompt should guide cross-domain associative thinking
        prompt = self.builder.build_context_map_prompt(
            query="test", initial_context=None, user_prior_knowledge=None
        )
        assert "因果" in prompt or "類比" in prompt or "上下游" in prompt

    def test_output_schema_reference(self):
        # Prompt must reference AssociatorBuildOutput schema fields
        prompt = self.builder.build_context_map_prompt(
            query="test", initial_context=None, user_prior_knowledge=None
        )
        assert "AssociatorBuildOutput" in prompt or "context_map" in prompt

    def test_returns_string(self):
        result = self.builder.build_context_map_prompt(
            query="test", initial_context=None, user_prior_knowledge=None
        )
        assert isinstance(result, str)
        assert len(result) > 100


class TestDeriveSearchPlanPrompt:
    def setup_method(self):
        self.builder = AssociatorPromptBuilder()

    def test_basic_prompt_contains_context_map(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="## 研究結構 (v0)\n核心議題: A, B",
            executed_searches=[]
        )
        assert "研究結構" in prompt
        assert "搜尋策略師" in prompt

    def test_context_map_wrapped_with_boundary(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="## 研究結構 (v0)\n核心議題: A, B",
            executed_searches=[]
        )
        # context_map_summary should be boundary-isolated
        assert "_START]" in prompt

    def test_with_executed_searches_shown(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="test",
            executed_searches=["德國 Energiewende 2019", "日本 再生能源 社區"]
        )
        assert "德國 Energiewende" in prompt
        assert "日本 再生能源" in prompt

    def test_without_executed_searches_block_absent(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="test",
            executed_searches=[]
        )
        # With empty executed_searches, the dynamic "## 已執行的搜尋" section header
        # should not appear (the conditional block is not injected)
        assert "## 已執行的搜尋" not in prompt

    def test_search_strategy_guidance_present(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="test",
            executed_searches=[]
        )
        assert "rationale" in prompt or "為什麼" in prompt or "搜尋策略" in prompt

    def test_propose_verify_reminder_present(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="test",
            executed_searches=[]
        )
        assert "falsifiable" in prompt or "假說" in prompt or "驗證" in prompt

    def test_output_schema_reference(self):
        prompt = self.builder.derive_search_plan_prompt(
            context_map_summary="test",
            executed_searches=[]
        )
        assert "AssociatorDeriveOutput" in prompt or "search_seeds" in prompt

    def test_returns_string(self):
        result = self.builder.derive_search_plan_prompt(
            context_map_summary="test",
            executed_searches=[]
        )
        assert isinstance(result, str)
        assert len(result) > 100


class TestRefineContextMapPrompt:
    def setup_method(self):
        self.builder = AssociatorPromptBuilder()

    def test_basic_prompt_has_all_sections(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="## Current B",
            retrieval_results="[1] New data found...",
            initial_context_map_summary="## Initial B"
        )
        assert "Current B" in prompt
        assert "New data found" in prompt
        assert "Initial B" in prompt
        assert "is_stable" in prompt

    def test_boundary_isolation_on_retrieval(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="[1] Data",
            initial_context_map_summary="initial"
        )
        assert "_START]" in prompt  # boundary token on retrieval results

    def test_current_context_map_injected(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="UNIQUE_CURRENT_B_CONTENT",
            retrieval_results="data",
            initial_context_map_summary="initial"
        )
        assert "UNIQUE_CURRENT_B_CONTENT" in prompt

    def test_initial_context_map_injected(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="data",
            initial_context_map_summary="UNIQUE_INITIAL_B_CONTENT"
        )
        assert "UNIQUE_INITIAL_B_CONTENT" in prompt

    def test_stability_guidance_present(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="data",
            initial_context_map_summary="initial"
        )
        assert "穩定" in prompt or "stable" in prompt.lower()

    def test_refinement_guidance_present(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="data",
            initial_context_map_summary="initial"
        )
        # Should guide on what to update: confidence, topics, relations
        assert "confidence" in prompt or "topic" in prompt

    def test_narration_instruction_present(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="data",
            initial_context_map_summary="initial"
        )
        assert "narration" in prompt.lower() or "敘述" in prompt

    def test_output_schema_reference(self):
        prompt = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="data",
            initial_context_map_summary="initial"
        )
        assert "AssociatorRefineOutput" in prompt or "updated_context_map" in prompt

    def test_returns_string(self):
        result = self.builder.refine_context_map_prompt(
            current_context_map_summary="current",
            retrieval_results="data",
            initial_context_map_summary="initial"
        )
        assert isinstance(result, str)
        assert len(result) > 100
