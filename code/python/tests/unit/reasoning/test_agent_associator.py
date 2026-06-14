"""
AssociatorAgent unit tests.

TDD: These tests verify the agent class structure and its integration with
the prompt builder and schema layer. LLM calls are not tested here (integration
concern) — we verify class instantiation, method signatures, and prompt wiring.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from reasoning.agents.associator import AssociatorAgent
from reasoning.schemas_live import (
    AssociatorBuildOutput, AssociatorDeriveOutput, AssociatorRefineOutput,
    ContextMap, ContextMapTopic, ContextMapRelation, ContextMapSearchSeed,
    ContextMapDelta,
)


def make_mock_handler():
    """Create a minimal mock handler for agent initialization."""
    handler = MagicMock()
    handler.query_params = {}
    return handler


def make_minimal_context_map(research_question: str = "test research question") -> ContextMap:
    """Build a minimal valid ContextMap for testing."""
    return ContextMap(research_question=research_question)


def make_context_map_with_topics() -> ContextMap:
    """Build a ContextMap with topics and relations for testing."""
    t1 = ContextMapTopic(name="Topic A", domain="Domain A", relevance="core")
    t2 = ContextMapTopic(name="Topic B", domain="Domain B", relevance="supporting")
    rel = ContextMapRelation(
        source_topic_id=t1.topic_id,
        target_topic_id=t2.topic_id,
        relation_type="enables"
    )
    return ContextMap(
        research_question="test question",
        topics=[t1, t2],
        relations=[rel]
    )


class TestAssociatorAgentInit:
    def test_instantiation_with_handler(self):
        handler = make_mock_handler()
        agent = AssociatorAgent(handler=handler)
        assert agent is not None

    def test_agent_name_is_associator(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        assert agent.agent_name == "associator"

    def test_default_timeout(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        assert agent.timeout == 90

    def test_custom_timeout(self):
        agent = AssociatorAgent(handler=make_mock_handler(), timeout=120)
        assert agent.timeout == 120

    def test_has_prompt_builder(self):
        from reasoning.prompts.associator import AssociatorPromptBuilder
        agent = AssociatorAgent(handler=make_mock_handler())
        assert isinstance(agent.prompt_builder, AssociatorPromptBuilder)

    def test_inherits_base_reasoning_agent(self):
        from reasoning.agents.base import BaseReasoningAgent
        agent = AssociatorAgent(handler=make_mock_handler())
        assert isinstance(agent, BaseReasoningAgent)


class TestBuildContextMap:
    """Tests for AssociatorAgent.build_context_map()"""

    @pytest.mark.asyncio
    async def test_calls_llm_validated_with_build_output_schema(self):
        """build_context_map() must call call_llm_validated with AssociatorBuildOutput."""
        agent = AssociatorAgent(handler=make_mock_handler())

        # Build a minimal valid mock return value
        mock_cm = make_minimal_context_map("台灣綠能衝突")
        mock_output = AssociatorBuildOutput(
            context_map=mock_cm,
            narration="建立了初始知識地圖"
        )

        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        result = await agent.build_context_map(
            query="台灣綠能衝突的國外案例",
            initial_context=None,
            user_prior_knowledge=None
        )

        # Verify call_llm_validated was called
        agent.call_llm_validated.assert_called_once()
        call_kwargs = agent.call_llm_validated.call_args
        assert call_kwargs.kwargs.get("response_schema") == AssociatorBuildOutput

    @pytest.mark.asyncio
    async def test_returns_build_output_type(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_cm = make_minimal_context_map("test question")
        mock_output = AssociatorBuildOutput(
            context_map=mock_cm,
            narration="narration text"
        )
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        result = await agent.build_context_map(query="test")
        assert isinstance(result, AssociatorBuildOutput)

    @pytest.mark.asyncio
    async def test_prompt_contains_query(self):
        """Verify the prompt passed to call_llm_validated contains the query."""
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_cm = make_minimal_context_map("台灣再生能源")
        mock_output = AssociatorBuildOutput(
            context_map=mock_cm,
            narration="narration"
        )
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.build_context_map(query="台灣再生能源的社區共有模式")

        # Check the prompt argument
        call_args = agent.call_llm_validated.call_args
        prompt_passed = call_args.kwargs.get("prompt") or call_args.args[0]
        assert "台灣再生能源的社區共有模式" in prompt_passed

    @pytest.mark.asyncio
    async def test_passes_initial_context_to_prompt_builder(self):
        """When initial_context is given, prompt should contain it."""
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_cm = make_minimal_context_map("test")
        mock_output = AssociatorBuildOutput(context_map=mock_cm, narration="n")
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.build_context_map(
            query="test",
            initial_context="[1] Specific initial context data"
        )

        call_args = agent.call_llm_validated.call_args
        prompt_passed = call_args.kwargs.get("prompt") or call_args.args[0]
        assert "Specific initial context data" in prompt_passed

    @pytest.mark.asyncio
    async def test_optional_params_default_none(self):
        """build_context_map works with only query argument."""
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_cm = make_minimal_context_map("test")
        mock_output = AssociatorBuildOutput(context_map=mock_cm, narration="n")
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        # Should not raise
        result = await agent.build_context_map(query="test question only")
        assert result is mock_output


class TestDeriveSearchPlan:
    """Tests for AssociatorAgent.derive_search_plan()"""

    @pytest.mark.asyncio
    async def test_calls_llm_validated_with_derive_output_schema(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_output = AssociatorDeriveOutput(
            search_seeds=[],
            narration="no seeds needed"
        )
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        context_map = make_minimal_context_map("test")
        await agent.derive_search_plan(context_map=context_map)

        call_kwargs = agent.call_llm_validated.call_args
        assert call_kwargs.kwargs.get("response_schema") == AssociatorDeriveOutput

    @pytest.mark.asyncio
    async def test_returns_derive_output_type(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_output = AssociatorDeriveOutput(search_seeds=[], narration="n")
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        result = await agent.derive_search_plan(
            context_map=make_minimal_context_map("test")
        )
        assert isinstance(result, AssociatorDeriveOutput)

    @pytest.mark.asyncio
    async def test_prompt_contains_context_map_content(self):
        """The derived prompt should contain research question from context_map."""
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_output = AssociatorDeriveOutput(search_seeds=[], narration="n")
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        cm = make_minimal_context_map("UNIQUE_RESEARCH_QUESTION_FOR_TEST")
        await agent.derive_search_plan(context_map=cm)

        call_args = agent.call_llm_validated.call_args
        prompt_passed = call_args.kwargs.get("prompt") or call_args.args[0]
        assert "UNIQUE_RESEARCH_QUESTION_FOR_TEST" in prompt_passed

    @pytest.mark.asyncio
    async def test_executed_searches_passed_to_prompt(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_output = AssociatorDeriveOutput(search_seeds=[], narration="n")
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.derive_search_plan(
            context_map=make_minimal_context_map("test"),
            executed_searches=["UNIQUE_EXECUTED_SEARCH_QUERY_XYZ"]
        )

        call_args = agent.call_llm_validated.call_args
        prompt_passed = call_args.kwargs.get("prompt") or call_args.args[0]
        assert "UNIQUE_EXECUTED_SEARCH_QUERY_XYZ" in prompt_passed

    @pytest.mark.asyncio
    async def test_optional_params_default(self):
        """derive_search_plan works with only context_map argument."""
        agent = AssociatorAgent(handler=make_mock_handler())
        mock_output = AssociatorDeriveOutput(search_seeds=[], narration="n")
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        result = await agent.derive_search_plan(
            context_map=make_minimal_context_map("test")
        )
        assert result is mock_output


class TestRefineContextMap:
    """Tests for AssociatorAgent.refine_context_map()"""

    def _make_refine_output(self, context_map: ContextMap) -> AssociatorRefineOutput:
        """Build a minimal valid AssociatorRefineOutput."""
        delta = ContextMapDelta(
            from_version=0,
            to_version=1,
            reason="test refinement"
        )
        return AssociatorRefineOutput(
            updated_context_map=context_map,
            delta=delta,
            is_stable=False,
            narration="updated the map"
        )

    @pytest.mark.asyncio
    async def test_calls_llm_validated_with_refine_output_schema(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        cm = make_minimal_context_map("test")
        mock_output = self._make_refine_output(cm)
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.refine_context_map(
            current_context_map=cm,
            initial_context_map=cm,
            retrieval_results="[1] Some results"
        )

        call_kwargs = agent.call_llm_validated.call_args
        assert call_kwargs.kwargs.get("response_schema") == AssociatorRefineOutput

    @pytest.mark.asyncio
    async def test_returns_refine_output_type(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        cm = make_minimal_context_map("test")
        mock_output = self._make_refine_output(cm)
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        result = await agent.refine_context_map(
            current_context_map=cm,
            initial_context_map=cm,
            retrieval_results="[1] Some results"
        )
        assert isinstance(result, AssociatorRefineOutput)

    @pytest.mark.asyncio
    async def test_prompt_contains_retrieval_results(self):
        agent = AssociatorAgent(handler=make_mock_handler())
        cm = make_minimal_context_map("test")
        mock_output = self._make_refine_output(cm)
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.refine_context_map(
            current_context_map=cm,
            initial_context_map=cm,
            retrieval_results="[1] UNIQUE_RETRIEVAL_CONTENT_XYZ"
        )

        call_args = agent.call_llm_validated.call_args
        prompt_passed = call_args.kwargs.get("prompt") or call_args.args[0]
        assert "UNIQUE_RETRIEVAL_CONTENT_XYZ" in prompt_passed

    @pytest.mark.asyncio
    async def test_prompt_contains_current_and_initial_maps(self):
        """Both current and initial context maps must appear in prompt."""
        agent = AssociatorAgent(handler=make_mock_handler())
        current_cm = make_minimal_context_map("CURRENT_RESEARCH_QUESTION")
        initial_cm = make_minimal_context_map("INITIAL_RESEARCH_QUESTION")
        mock_output = self._make_refine_output(current_cm)
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.refine_context_map(
            current_context_map=current_cm,
            initial_context_map=initial_cm,
            retrieval_results="data"
        )

        call_args = agent.call_llm_validated.call_args
        prompt_passed = call_args.kwargs.get("prompt") or call_args.args[0]
        assert "CURRENT_RESEARCH_QUESTION" in prompt_passed
        assert "INITIAL_RESEARCH_QUESTION" in prompt_passed

    @pytest.mark.asyncio
    async def test_level_is_high(self):
        """refine_context_map should use 'high' LLM level."""
        agent = AssociatorAgent(handler=make_mock_handler())
        cm = make_minimal_context_map("test")
        mock_output = self._make_refine_output(cm)
        agent.call_llm_validated = AsyncMock(return_value=(mock_output, 0, False))

        await agent.refine_context_map(
            current_context_map=cm,
            initial_context_map=cm,
            retrieval_results="data"
        )

        call_kwargs = agent.call_llm_validated.call_args
        level = call_kwargs.kwargs.get("level")
        assert level == "high"
