"""
AssociatorAgent - Builds, derives, and refines the research Context Map (Master B).

Used in Live Research mode's B->A->B' loop:
- Phase 0 (build): Create initial Master B from research question
- Phase 1 (derive): Derive search plan A from current B
- Phase 4 (refine): Update B to B' after retrieval results arrive
"""

from typing import List, Optional
from reasoning.agents.base import BaseReasoningAgent
from reasoning.prompts.associator import AssociatorPromptBuilder
from reasoning.schemas_live import (
    AssociatorBuildOutput,
    AssociatorDeriveOutput,
    AssociatorRefineOutput,
    ContextMap,
    context_map_to_summary,
    context_map_extract_for_section,
)


class AssociatorAgent(BaseReasoningAgent):
    """
    AssociatorAgent builds and refines the research Context Map (Master B).

    Three operations:
    1. build_context_map: Create initial B from research question
    2. derive_search_plan: Derive search plan A from current B
    3. refine_context_map: Update B to B' after retrieval
    """

    def __init__(self, handler, timeout: int = 90):
        """
        Initialize AssociatorAgent.

        Args:
            handler: Request handler with LLM configuration
            timeout: Timeout in seconds for LLM calls (default: 90s,
                     higher than standard 60s because context map generation
                     involves complex structural output)
        """
        super().__init__(
            handler=handler,
            agent_name="associator",
            timeout=timeout,
            max_retries=3
        )
        self.prompt_builder = AssociatorPromptBuilder()

    async def build_context_map(
        self,
        query: str,
        initial_context: Optional[str] = None,
        user_prior_knowledge: Optional[str] = None
    ) -> AssociatorBuildOutput:
        """
        Phase 0: Build initial Context Map (Master B) from research question.

        Args:
            query: User's research question
            initial_context: Optional initial retrieval results formatted with
                             [ID] citations (from async pre-fetch)
            user_prior_knowledge: Optional prior knowledge provided by user
                                  in dialogue (from guided questions)

        Returns:
            AssociatorBuildOutput containing the initial ContextMap and narration
        """
        prompt = self.prompt_builder.build_context_map_prompt(
            query=query,
            initial_context=initial_context,
            user_prior_knowledge=user_prior_knowledge
        )
        result, _, _ = await self.call_llm_validated(
            prompt=prompt,
            response_schema=AssociatorBuildOutput,
            level="high"
        )
        return result

    async def derive_search_plan(
        self,
        context_map: ContextMap,
        executed_searches: Optional[List[str]] = None,
        focus_topic_ids: Optional[List[str]] = None
    ) -> AssociatorDeriveOutput:
        """
        Phase 1: Derive search plan (A) from current Context Map (B).

        Args:
            context_map: Current ContextMap (Master B)
            executed_searches: List of already-executed search queries
                               to prevent duplication
            focus_topic_ids: Optional list of topic_ids to focus on.
                             If provided, uses context_map_extract_for_section
                             for a filtered view. Otherwise uses full summary.

        Returns:
            AssociatorDeriveOutput containing search_seeds and narration
        """
        # Use section-filtered view if focus topics provided, else full summary
        if focus_topic_ids:
            summary = context_map_extract_for_section(context_map, focus_topic_ids)
        else:
            summary = context_map_to_summary(context_map)

        prompt = self.prompt_builder.derive_search_plan_prompt(
            context_map_summary=summary,
            executed_searches=executed_searches or []
        )
        result, _, _ = await self.call_llm_validated(
            prompt=prompt,
            response_schema=AssociatorDeriveOutput,
            level="low"  # derive 是從既有 ContextMap 提取 search query，機械性高，不需 high
        )
        return result

    async def refine_context_map(
        self,
        current_context_map: ContextMap,
        initial_context_map: ContextMap,
        retrieval_results: str,
        focus_topic_ids: Optional[List[str]] = None
    ) -> AssociatorRefineOutput:
        """
        Phase 4: Refine Context Map from B to B' after retrieval.

        Args:
            current_context_map: Current version of Master B (to be updated)
            initial_context_map: Version 0 of Master B (for drift awareness)
            retrieval_results: Formatted retrieval results with [ID] citations
            focus_topic_ids: Optional topic_ids to scope current map summary.
                             If provided, uses context_map_extract_for_section.
                             Initial map always uses full context_map_to_summary.

        Returns:
            AssociatorRefineOutput containing updated_context_map, delta,
            is_stable flag, and narration
        """
        # Current map: use section-filtered view if focus topics provided
        if focus_topic_ids:
            current_summary = context_map_extract_for_section(
                current_context_map, focus_topic_ids
            )
        else:
            current_summary = context_map_to_summary(current_context_map)

        # Initial map: always use full summary for drift detection
        initial_summary = context_map_to_summary(initial_context_map)

        prompt = self.prompt_builder.refine_context_map_prompt(
            current_context_map_summary=current_summary,
            retrieval_results=retrieval_results,
            initial_context_map_summary=initial_summary
        )
        result, _, _ = await self.call_llm_validated(
            prompt=prompt,
            response_schema=AssociatorRefineOutput,
            level="high"
        )
        return result
