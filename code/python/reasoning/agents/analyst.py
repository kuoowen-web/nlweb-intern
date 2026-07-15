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

    def __init__(self, handler, timeout: int = 120):  # 60 -> 120: 對齊 base.py:168，消除 subclass < base 矛盾（真實值靠 config analyst_timeout=300）
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
        context_map_summary: Optional[str] = None,  # Task 4: ContextMap summary for injection
        retrieval_evidence: Optional[str] = None,  # 防線二：gap 分類證據注入
        final_pass: bool = False  # 防線四：最後一輪強制 best-effort 寫稿
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
            context_map_summary=context_map_summary,  # Task 4: B context injection
            retrieval_evidence=retrieval_evidence,  # 防線二
            final_pass=final_pass  # 防線四
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
        query: str = None,
        enable_kg: bool = False,  # B9: match research() schema selection
        enable_live_research: bool = False,  # B9: match research() schema selection
        final_pass: bool = False,  # 防線四（修訂 3）：最後一輪 revise 也強制寫稿
    ) -> AnalystResearchOutput:
        """
        Revise draft based on critic's feedback.

        Schema selection mirrors research() (priority live_research > kg >
        argument_graphs > base) so the revised result keeps argument_graph /
        gap_resolutions / knowledge_graph instead of silently downgrading to
        the base schema (B9 forensic loss).

        Args:
            original_draft: Previous draft content
            review: Critic's review with validated schema
            formatted_context: Pre-formatted context string with [1], [2] IDs
            query: Original user query (Stage 5: prevent topic drift)
            enable_kg: Enable knowledge graph schema (per-request, matches research())
            enable_live_research: Enable Live Research schema (matches research())

        Returns:
            AnalystResearchOutput (or Enhanced/EnhancedKG/Live subclass if a
            feature flag is active)
        """
        # Import CONFIG here to avoid circular dependency
        from core.config import CONFIG

        enable_graphs = CONFIG.reasoning_params.get("features", {}).get("argument_graphs", False)

        # Build the revision prompt from PDF (pages 14-15)
        revision_prompt = self.prompt_builder.build_revision_prompt(
            original_draft=original_draft,
            review=review,
            formatted_context=formatted_context,
            original_query=query,
            final_pass=final_pass  # 防線四（修訂 3）
        )

        # Choose schema based on feature flags (dynamic schema selection)
        # Priority: live_research > kg > argument_graphs > base — must stay in
        # lockstep with research() (analyst.py research() schema selection).
        if enable_live_research:
            from reasoning.schemas_live import AnalystResearchOutputLive
            response_schema = AnalystResearchOutputLive
            self.logger.info("Using AnalystResearchOutputLive schema (revise, Live Research)")
        elif enable_kg:
            from reasoning.schemas_enhanced import AnalystResearchOutputEnhancedKG
            response_schema = AnalystResearchOutputEnhancedKG
            self.logger.info("Using AnalystResearchOutputEnhancedKG schema (revise, with KG)")
        elif enable_graphs:
            from reasoning.schemas_enhanced import AnalystResearchOutputEnhanced
            response_schema = AnalystResearchOutputEnhanced
            self.logger.info("Using AnalystResearchOutputEnhanced schema (revise, no KG)")
        else:
            response_schema = AnalystResearchOutput
            self.logger.info("Using basic AnalystResearchOutput schema (revise)")

        # Call LLM with validation
        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=revision_prompt,
            response_schema=response_schema,
            level="high"
        )

        # Log TypeAgent metrics for analytics
        self.logger.debug(f"TypeAgent metrics (revise): retries={retry_count}, fallback={fallback_used}")

        # Validate graphs if present (mirror research() post-validation)
        if hasattr(result, 'argument_graph') and result.argument_graph:
            self._validate_argument_graph(result.argument_graph, result.citations_used)
        if hasattr(result, 'knowledge_graph') and result.knowledge_graph:
            self._validate_knowledge_graph(result.knowledge_graph, result.citations_used)

        # C5 (observability only — Zoe ruling: do NOT mutate the prompt to force
        # the LLM to emit a graph; B9's goal is to stop the *type* from forcing the
        # field away, not to coerce the LLM into filling it). When a graph-capable
        # schema was selected but the LLM omitted argument_graph, log so on-call can
        # distinguish "LLM legitimately produced no graph" from "a type bug ate it".
        if response_schema is not AnalystResearchOutput and not getattr(result, 'argument_graph', None):
            self.logger.info(
                f"Revise selected {response_schema.__name__} but argument_graph is "
                f"empty — LLM omitted it (not a type bug; revision prompt does not "
                f"mandate graph emission)"
            )

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
