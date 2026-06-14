"""
Analyst Agent - Research and draft generation for the Actor-Critic system.
"""

from typing import Any, Callable, Dict, List, Optional, Set
from reasoning.agents.base import BaseReasoningAgent
from reasoning.schemas import AnalystResearchOutput, CriticReviewOutput
from reasoning.prompts.analyst import AnalystPromptBuilder


class AnalystAgent(BaseReasoningAgent):
    """
    Analyst Agent responsible for research and draft generation.

    The Analyst reads source materials, analyzes them, and produces
    initial drafts or revised drafts based on critic feedback.
    """

    def __init__(self, handler, timeout: int = 60):
        """
        Initialize Analyst Agent.

        Args:
            handler: Request handler with LLM configuration
            timeout: Timeout in seconds for LLM calls
        """
        super().__init__(
            handler=handler,
            agent_name="analyst",
            timeout=timeout,
            max_retries=3
        )
        self.prompt_builder = AnalystPromptBuilder()

    async def research(
        self,
        query: str,
        formatted_context: str,
        mode: str,
        temporal_context: Optional[Dict[str, Any]] = None,
        enable_kg: bool = False,
        enable_web_search: bool = False,
        previous_draft: Optional[str] = None,  # SEC-6 Phase 1
        enable_live_research: bool = False,  # Task 4: Live Research B context injection
        context_map_summary: Optional[str] = None  # Task 4: ContextMap summary for injection
    ) -> AnalystResearchOutput:
        """
        Enhanced research with optional argument graph generation and knowledge graph.

        Args:
            query: User's research question
            formatted_context: Pre-formatted context string with [1], [2] IDs
            mode: Research mode (strict, discovery, monitor)
            temporal_context: Optional temporal information (time range, etc.)
            enable_kg: Enable knowledge graph generation (per-request override)
            enable_web_search: Enable web search for dynamic data (Stage 5)

        Returns:
            AnalystResearchOutput (or Enhanced version if feature enabled)
        """
        # Import CONFIG here to avoid circular dependency
        from core.config import CONFIG

        # Check feature flags (CONFIG as default, parameter overrides for KG)
        enable_graphs = CONFIG.reasoning_params.get("features", {}).get("argument_graphs", False)
        # enable_kg is now a parameter (per-request control)

        # Check Stage 5 feature flag
        enable_gap_enrichment = CONFIG.reasoning_params.get("features", {}).get("gap_knowledge_enrichment", False)

        self.logger.info(
            f"Analyst.research() - enable_kg={enable_kg}, enable_graphs={enable_graphs}, "
            f"enable_web_search={enable_web_search}, enable_gap_enrichment={enable_gap_enrichment}, "
            f"enable_live_research={enable_live_research}"
        )

        # Build the system prompt from PDF (pages 7-10)
        system_prompt = self.prompt_builder.build_research_prompt(
            query=query,
            formatted_context=formatted_context,
            mode=mode,
            temporal_context=temporal_context,
            enable_argument_graph=enable_graphs,  # Phase 2
            enable_knowledge_graph=enable_kg,  # Phase KG
            enable_gap_enrichment=enable_gap_enrichment,  # Stage 5
            enable_web_search=enable_web_search,  # Stage 5
            previous_draft=previous_draft,  # SEC-6 Phase 1
            enable_live_research=enable_live_research,  # Task 4: Live Research
            context_map_summary=context_map_summary  # Task 4: B context injection
        )

        # Choose schema based on feature flags (dynamic schema selection)
        # Priority: live_research > kg > argument_graphs > base
        if enable_live_research:
            from reasoning.schemas_live import AnalystResearchOutputLive
            response_schema = AnalystResearchOutputLive
            self.logger.info("Using AnalystResearchOutputLive schema (Live Research)")
        elif enable_kg:
            from reasoning.schemas_enhanced import AnalystResearchOutputEnhancedKG
            response_schema = AnalystResearchOutputEnhancedKG
            self.logger.info("Using AnalystResearchOutputEnhancedKG schema (with KG)")
        elif enable_graphs:
            from reasoning.schemas_enhanced import AnalystResearchOutputEnhanced
            response_schema = AnalystResearchOutputEnhanced
            self.logger.info("Using AnalystResearchOutputEnhanced schema (no KG)")
        else:
            response_schema = AnalystResearchOutput
            self.logger.info("Using basic AnalystResearchOutput schema")

        # Call LLM with validation
        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=system_prompt,
            response_schema=response_schema,
            level="high"
        )

        # Log TypeAgent metrics for analytics
        self.logger.debug(f"TypeAgent metrics: retries={retry_count}, fallback={fallback_used}")

        # Validate argument graph if present
        if hasattr(result, 'argument_graph') and result.argument_graph:
            self._validate_argument_graph(result.argument_graph, result.citations_used)

        # Validate knowledge graph if present (Phase KG)
        if hasattr(result, 'knowledge_graph') and result.knowledge_graph:
            self._validate_knowledge_graph(result.knowledge_graph, result.citations_used)

        return result

    async def revise(
        self,
        original_draft: str,
        review: CriticReviewOutput,
        formatted_context: str,
        query: str = None
    ) -> AnalystResearchOutput:
        """
        Revise draft based on critic's feedback.

        Args:
            original_draft: Previous draft content
            review: Critic's review with validated schema
            formatted_context: Pre-formatted context string with [1], [2] IDs
            query: Original user query (Stage 5: prevent topic drift)

        Returns:
            AnalystResearchOutput with validated schema
        """
        # Build the revision prompt from PDF (pages 14-15)
        revision_prompt = self.prompt_builder.build_revision_prompt(
            original_draft=original_draft,
            review=review,
            formatted_context=formatted_context,
            original_query=query
        )

        # Call LLM with validation
        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=revision_prompt,
            response_schema=AnalystResearchOutput,
            level="high"
        )

        # Log TypeAgent metrics for analytics
        self.logger.debug(f"TypeAgent metrics (revise): retries={retry_count}, fallback={fallback_used}")

        return result

    def _validate_evidence_references(
        self,
        items: List[Any],
        valid_citation_ids: Set[int],
        name_getter: Callable[[Any], str],
    ) -> None:
        """
        Generic validation for evidence ID references.

        Validates that all evidence_ids in items reference valid citations,
        logs warnings for invalid references, and removes them.

        Args:
            items: List of items to validate (nodes, entities, relationships)
            valid_citation_ids: Set of valid citation IDs
            name_getter: Function to extract item name for logging
        """
        for item in items:
            evidence_ids = getattr(item, 'evidence_ids', [])
            if not evidence_ids:
                continue

            invalid_ids = [eid for eid in evidence_ids if eid not in valid_citation_ids]
            if invalid_ids:
                self.logger.warning(f"{name_getter(item)} has invalid evidence_ids: {invalid_ids}")
                item.evidence_ids = [eid for eid in evidence_ids if eid in valid_citation_ids]

    def _validate_argument_graph(self, graph: List, valid_citations: List[int]) -> None:
        """
        Ensure argument graph cites only available sources (Phase 2).

        Args:
            graph: List of ArgumentNode objects
            valid_citations: List of valid citation IDs from analyst
        """
        self._validate_evidence_references(
            items=graph,
            valid_citation_ids=set(valid_citations),
            name_getter=lambda node: f"Node {node.node_id[:8]}",
        )

    def _validate_knowledge_graph(self, kg: 'KnowledgeGraph', valid_citations: List[int]) -> None:  # noqa: F821
        """
        Ensure knowledge graph cites only available sources (Phase KG).

        Args:
            kg: KnowledgeGraph object with entities and relationships
            valid_citations: List of valid citation IDs from analyst
        """
        valid_citation_set = set(valid_citations)

        self._validate_evidence_references(
            items=kg.entities,
            valid_citation_ids=valid_citation_set,
            name_getter=lambda e: f"Entity '{e.name}'",
        )

        self._validate_evidence_references(
            items=kg.relationships,
            valid_citation_ids=valid_citation_set,
            name_getter=lambda r: f"Relationship {r.relationship_id[:8]}",
        )
