"""
Deep Research Orchestrator - Coordinates the Actor-Critic reasoning loop.
"""

import asyncio
import json
import sentry_sdk
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from urllib.parse import quote
from misc.logger.logging_config_helper import get_configured_logger
from core.retriever import search as retriever_search
from core.config import CONFIG
from reasoning.agents.analyst import AnalystAgent
from reasoning.agents.critic import CriticAgent
from reasoning.agents.writer import WriterAgent
from reasoning.filters.source_tier import SourceTierFilter, NoValidSourcesError
from reasoning.schemas import WriterComposeOutput
from reasoning.research_state import ResearchState
from reasoning.orchestrator_base import OrchestratorBase, ResearchCancelledError, ProgressConfig  # noqa: F401


logger = get_configured_logger("reasoning.orchestrator")


# === KG Editing: ResearchState cache for selective re-run ===
_research_state_cache: Dict[str, dict] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour
_MAX_CACHE_SIZE = 50


def _cache_research_state(query_id: str, state: 'ResearchState'):
    """Cache data fields from ResearchState after phase 1 for potential selective re-run.

    Only caches serializable data fields. Session-specific objects
    (iteration_logger, tracer) are NOT cached — they are recreated per session.
    """
    _research_state_cache[query_id] = {
        'formatted_context': state.formatted_context,
        'source_map': state.source_map,
        'current_context': state.current_context,
        'query': state.query,
        'mode': state.mode,
        'temporal_context': state.temporal_context,
        'enable_kg': state.enable_kg,
        'enable_web_search': state.enable_web_search,
        'items': state.items,
        'cached_at': time.time(),
    }
    # Cleanup expired entries
    now = time.time()
    expired = [k for k, v in _research_state_cache.items() if now - v['cached_at'] > _CACHE_TTL_SECONDS]
    for k in expired:
        del _research_state_cache[k]
    # Evict oldest entries if cache exceeds max size
    if len(_research_state_cache) >= _MAX_CACHE_SIZE:
        sorted_keys = sorted(_research_state_cache.keys(), key=lambda k: _research_state_cache[k]['cached_at'])
        evict_count = len(_research_state_cache) - _MAX_CACHE_SIZE + 1  # bring below limit
        for k in sorted_keys[:evict_count]:
            del _research_state_cache[k]
        logger.info(f"[RERUN CACHE] Evicted {evict_count} oldest entries (cache size={len(_research_state_cache)})")
    logger.info(f"[RERUN CACHE] Cached state for query_id={query_id} (cache size={len(_research_state_cache)})")


def get_cached_research_state(query_id: str) -> Optional[dict]:
    """Retrieve cached ResearchState data for selective re-run.

    Returns None if not found or expired.
    """
    cached = _research_state_cache.get(query_id)
    if cached is None:
        return None
    if time.time() - cached['cached_at'] > _CACHE_TTL_SECONDS:
        del _research_state_cache[query_id]
        return None
    return cached


class DeepResearchOrchestrator(OrchestratorBase):
    """
    Orchestrator for the Actor-Critic reasoning system.

    Coordinates the iterative loop between Analyst (Actor) and Critic,
    then uses Writer to format the final report.
    """

    def __init__(self, handler: Any):
        """
        Initialize orchestrator with reasoning agents.

        Args:
            handler: Request handler with LLM configuration
        """
        super().__init__(handler)
        self.logger = get_configured_logger("reasoning.orchestrator")

        # Initialize agents
        analyst_timeout = CONFIG.reasoning_params.get("analyst_timeout", 60)
        critic_timeout = CONFIG.reasoning_params.get("critic_timeout", 30)
        writer_timeout = CONFIG.reasoning_params.get("writer_timeout", 45)

        self.analyst = AnalystAgent(handler, timeout=analyst_timeout)
        self.critic = CriticAgent(handler, timeout=critic_timeout)
        self.writer = WriterAgent(handler, timeout=writer_timeout)

        # Initialize source tier filter
        self.source_filter = SourceTierFilter(CONFIG.reasoning_source_tiers)

        # Unified context storage (Single Source of Truth)
        self.formatted_context = ""
        self.source_map = {}

    def _format_context_shared(self, items: List[Dict[str, Any]], start_id: int = 1) -> tuple[str, Dict[int, Dict]]:
        """
        Format context with citation markers - SINGLE SOURCE OF TRUTH.

        This ensures all agents (Analyst, Critic, Writer) use the same
        citation numbering system, preventing citation mismatch issues.

        Args:
            items: List of source items (already filtered and enriched by SourceTierFilter)

        Returns:
            Tuple of (formatted_string, source_map)
                - formatted_string: Context with [1], [2], [3] markers (token-budgeted for AI)
                - source_map: Dict mapping citation ID to source item (complete, for frontend)

        Design:
            - source_map: Contains ALL items (no limit) - managed by code, not LLM
            - formatted_context: Token-budgeted for LLM consumption
            - Citation numbers are consistent between both
        """
        MAX_TOTAL_CHARS = 20000  # ~10k tokens budget for formatted_context
        MAX_SNIPPET_LENGTH = 500
        OVERHEAD_PER_ITEM = 100  # Citation marker + source + title + newlines

        source_map = {}
        formatted_parts = []

        # ===== Step 1: Build COMPLETE source_map (no limit) =====
        # This is the ground truth for citation -> item mapping
        # Frontend will use this to display all citation references
        for idx, item in enumerate(items, start_id):
            source_map[idx] = item

        # ===== Step 2: Calculate how many items fit in token budget =====
        # Dynamically determine items_in_budget based on actual content size
        cumulative_chars = 0
        items_in_budget = 0

        for item in items:
            if isinstance(item, dict):
                desc_len = len(item.get("description") or item.get("articleBody", ""))
            else:
                desc_len = 0
            item_chars = min(desc_len, MAX_SNIPPET_LENGTH) + OVERHEAD_PER_ITEM

            if cumulative_chars + item_chars > MAX_TOTAL_CHARS:
                break
            cumulative_chars += item_chars
            items_in_budget += 1

        # Ensure at least some items are included
        items_in_budget = max(items_in_budget, min(10, len(items)))

        # ===== Step 3: Calculate snippet length for budgeted items =====
        total_estimated = sum(
            min(len((item.get("description") or item.get("articleBody", "")) if isinstance(item, dict) else ""), MAX_SNIPPET_LENGTH) + OVERHEAD_PER_ITEM
            for item in items[:items_in_budget]
        )

        if total_estimated > MAX_TOTAL_CHARS:
            reduction_ratio = MAX_TOTAL_CHARS / total_estimated
            snippet_length = max(int(MAX_SNIPPET_LENGTH * reduction_ratio), 100)
            self.logger.warning(
                f"Context too large ({total_estimated} chars for {items_in_budget} items), "
                f"reducing snippet length to {snippet_length} chars (ratio: {reduction_ratio:.2f})"
            )
        else:
            snippet_length = MAX_SNIPPET_LENGTH

        # ===== Step 4: Format context for AI (only budgeted items) =====
        for idx, item in enumerate(items[:items_in_budget], start_id):
            # Handle both dict and tuple/list formats
            if isinstance(item, dict):
                title = item.get("title") or item.get("name", "No title")
                description = item.get("description") or item.get("articleBody", "")
                source = item.get("site", "Unknown")
                date_published = item.get("datePublished", "")
            elif isinstance(item, (list, tuple)):
                title = item[2] if len(item) > 2 else "No title"
                try:
                    schema_json = item[1] if len(item) > 1 else "{}"
                    schema_obj = json.loads(schema_json) if isinstance(schema_json, str) else schema_json
                    description = schema_obj.get("description") or schema_obj.get("articleBody", "")
                    date_published = schema_obj.get("datePublished", "")
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    logger.debug(f"Failed to parse schema_json: {e}")
                    description = ""
                    date_published = ""
                source = item[3] if len(item) > 3 else "Unknown"
            else:
                title = "No title"
                description = ""
                source = "Unknown"
                date_published = ""

            # Format date for display (extract YYYY-MM-DD from ISO format)
            date_str = ""
            if date_published:
                date_str = str(date_published).split("T")[0]

            snippet = description[:snippet_length] + (
                "..." if len(description) > snippet_length else ""
            )
            header = f"[{idx}] {source} - {title}"
            if date_str:
                header += f" ({date_str})"
            formatted_parts.append(f"{header}\n{snippet}\n")

        formatted_string = "\n".join(formatted_parts)

        # Add current datetime header for temporal query accuracy
        current_time_header = self._get_current_time_header()
        if current_time_header:
            formatted_string = current_time_header + formatted_string

        self.logger.info(
            f"Formatted context: {items_in_budget}/{len(items)} sources in AI context, "
            f"{len(source_map)} total in source_map, {len(formatted_string)} chars"
        )

        # Check if context is empty
        if not formatted_string or len(source_map) == 0:
            self.logger.warning(
                f"Empty context generated! items count: {len(items)}, "
                f"formatted_parts count: {len(formatted_parts)}"
            )

        return formatted_string, source_map

    def _build_critic_reference_sheet(self, citations_used: List[int]) -> str:
        """
        SEC-6: Build a compact reference sheet for Critic containing only cited sources.

        Instead of passing full formatted_context to Critic, extract only the
        sources actually cited by Analyst, reducing token usage significantly.

        Args:
            citations_used: List of citation IDs from Analyst's response

        Returns:
            Formatted reference sheet string
        """
        snippet_length = CONFIG.reasoning_params.get("agent_isolation", {}).get(
            "reference_sheet_snippet_length", 500
        )
        parts = []
        for cid in sorted(set(citations_used)):
            item = self.source_map.get(cid)
            if not item:
                self.logger.warning(f"SEC-6: citation [{cid}] not found in source_map")
                continue

            if isinstance(item, dict):
                title = item.get("title") or item.get("name", "No title")
                source = item.get("site", "Unknown")
                description = item.get("description") or item.get("articleBody", "")
            elif isinstance(item, (list, tuple)):
                title = item[2] if len(item) > 2 else "No title"
                source = item[3] if len(item) > 3 else "Unknown"
                try:
                    schema_json = item[1] if len(item) > 1 else "{}"
                    schema_obj = json.loads(schema_json) if isinstance(schema_json, str) else schema_json
                    description = schema_obj.get("description") or schema_obj.get("articleBody", "")
                except Exception as e:
                    self.logger.warning(f"SEC-6: Failed to parse description for citation [{cid}]: {e}")
                    description = ""
            else:
                title = "No title"
                source = "Unknown"
                description = ""

            snippet = description[:snippet_length] + ("..." if len(description) > snippet_length else "")
            parts.append(f"[{cid}] {source} - {title}\n{snippet}\n")

        return "\n".join(parts)

    def _create_no_results_response(self, query: str) -> List[Dict[str, Any]]:
        """
        Create a response indicating no relevant documents were found.

        Used when the formatted context is empty, preventing the Analyst
        from hallucinating content without any source material.

        Args:
            query: The user's original query

        Returns:
            List with single NLWeb Item dict with no-results message
        """
        return [{
            "@type": "Item",
            "url": "internal://no-results",
            "name": f"查無相關資料：{query}",
            "site": "系統訊息",
            "siteUrl": "internal",
            "score": 0,
            "description": (
                f"# 查無相關資料\n\n"
                f"針對「{query}」的搜尋未找到任何相關文件。\n\n"
                f"**建議**：\n"
                f"1. 嘗試使用不同的關鍵詞\n"
                f"2. 在搜尋介面調整來源篩選，包含更多新聞來源\n"
                f"3. 確認資料庫中有相關內容"
            )
        }]

    def _get_current_time_header(self) -> str:
        """
        Generate current datetime header for temporal query accuracy.

        Returns:
            Formatted datetime header string or empty string if disabled.
        """
        try:
            # Get timezone from config (default: Asia/Taipei)
            timezone_str = CONFIG.reasoning_params.get("timezone", "Asia/Taipei")

            try:
                import pytz
                tz = pytz.timezone(timezone_str)
                current_time = datetime.now(tz)
            except ImportError:
                # Fallback if pytz not available
                current_time = datetime.now()
                self.logger.debug("pytz not available, using local time")

            # Format: 2026-01-13 14:30:00 星期一 (台北時間)
            weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekday_names[current_time.weekday()]

            header = f"""## 當前時間
{current_time.strftime('%Y-%m-%d %H:%M:%S')} {weekday} ({timezone_str})

當用戶詢問「今天」、「最近」、「現在」等時間相關詞彙時，請參考上述當前時間。

## 可用資料來源
"""
            return header

        except Exception as e:
            self.logger.warning(f"Failed to generate current time header: {e}")
            return ""

    async def _phase_filter_and_prepare(self, state: 'ResearchState') -> 'ResearchState':
        """
        Phase 1: Filter sources by tier + format context with citations.

        Reads: state.items, state.mode, state.tracer
        Writes: state.current_context, state.formatted_context, state.source_map
        May set: state.early_return (if no sources)

        Raises:
            NoValidSourcesError: If no sources are available after enrichment
        """
        await self._emit_phase_event("filter_and_prepare", "started")

        # Phase 1a: Filter and prepare sources
        try:
            state.current_context = await self._filter_and_prepare_sources(
                items=state.items,
                mode=state.mode,
                tracer=state.tracer,
            )
        except ValueError:
            state.early_return = self._create_no_sources_error_response(
                items_count=len(state.items),
                filtered_count=0,
                mode=state.mode,
            )
            return state

        # Phase 1b: Format context with citations
        state.formatted_context, state.source_map = await self._format_research_context(
            items=state.current_context,
            tracer=state.tracer,
        )

        # Sync to instance attributes (backward compat until all phases migrated)
        # SYNC: self.source_map and state.source_map must be the SAME dict reference,
        # because _process_gap_resolutions() and _build_critic_reference_sheet()
        # read/mutate self.source_map directly. Remove when all helpers read from state.
        self.formatted_context = state.formatted_context
        self.source_map = state.source_map

        # RSN-11: Early return if source_map empty
        if not state.source_map:
            self.logger.warning(
                "RSN-11: source_map is empty -- no real sources retrieved. "
                "Returning no-results response to prevent hallucination."
            )
            state.early_return = self._create_no_results_response(state.query)
            await self._emit_phase_event("filter_and_prepare", "completed")
            return state

        await self._emit_phase_event("filter_and_prepare", "completed")
        return state

    async def _phase_actor_critic_loop(self, state: 'ResearchState') -> 'ResearchState':
        """
        Phase 2: Actor-Critic iterative loop.

        Contains: Analyst research/revise, Gap Detection, Gap Resolution (Tier 6),
                  Critic review, convergence check.

        Reads: state.formatted_context, state.source_map, state.current_context,
               state.query, state.mode, state.temporal_context, state.enable_kg,
               state.enable_web_search, state.query_id, state.tracer,
               state.iteration_logger, state.enable_isolation, state.max_iterations
        Writes: state.draft, state.review, state.response, state.iteration,
                state.reject_count, state.seen_citation_ids, state.analyst_citations,
                state.formatted_context, state.source_map, state.current_context
        May set: state.early_return (if max iterations exhausted with no data)

        Raises:
            ResearchCancelledError: If client disconnects (7 checkpoints)
        """
        await self._emit_phase_event("actor_critic_loop", "started")

        max_iterations = state.max_iterations
        iteration = 0
        draft = None
        review = None
        response = None  # RSN-7: Initialize to avoid unbound variable if first analyst call fails
        reject_count = 0

        # SEC-6: Agent isolation state
        enable_isolation = state.enable_isolation
        if enable_isolation:
            self.logger.info("SEC-6: Agent isolation ENABLED")
        seen_citation_ids: set = set()

        # Aliases for readability (state is the source of truth)
        query = state.query
        mode = state.mode
        temporal_context = state.temporal_context
        enable_kg = state.enable_kg
        enable_web_search = state.enable_web_search
        query_id = state.query_id
        tracer = state.tracer
        iteration_logger = state.iteration_logger

        while iteration < max_iterations:
            self._check_connection()  # Checkpoint 1: loop start
            self.logger.info(f"Starting iteration {iteration + 1}/{max_iterations}")

            # Tracing: Iteration start
            if tracer:
                tracer.start_iteration(iteration + 1, max_iterations)

            # Send progress: Analyst analyzing
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "analyst_analyzing",
                "iteration": iteration + 1,
                "total_iterations": max_iterations
            })

            # Analyst: Research or revise
            if review and review.status == "REJECT":
                # Revise based on critique
                reject_count += 1
                self.logger.info("Analyst revising draft based on critique")
                analyst_input = {
                    "original_draft": draft,
                    "review": review,
                    "formatted_context": state.formatted_context
                }

                self._check_connection()  # Checkpoint 2: before analyst.revise()
                if tracer:
                    with tracer.agent_span("analyst", "revise", analyst_input) as span:
                        response = await self.analyst.revise(
                            original_draft=draft,
                            review=review,
                            formatted_context=state.formatted_context,
                            query=query,
                            enable_kg=enable_kg,  # B9: match research() schema selection
                        )
                        span.set_result(response)
                else:
                    response = await self.analyst.revise(
                        original_draft=draft,
                        review=review,
                        formatted_context=state.formatted_context,
                        query=query,
                        enable_kg=enable_kg,  # B9: match research() schema selection
                    )

                iteration_logger.log_agent_output(
                    iteration=iteration + 1,
                    agent_name="analyst_revise",
                    input_prompt=f"Draft: {draft[:100]}...\nReview: {review}",
                    output_response=response
                )
            else:
                # Initial research
                self.logger.info("Analyst conducting research")
                analyst_input = {
                    "query": query,
                    "formatted_context": state.formatted_context,
                    "mode": mode,
                    "temporal_context": temporal_context
                }

                self._check_connection()  # Checkpoint 3: before analyst.research()
                if tracer:
                    with tracer.agent_span("analyst", "research", analyst_input) as span:
                        response = await self.analyst.research(
                            query=query,
                            formatted_context=state.formatted_context,
                            mode=mode,
                            temporal_context=temporal_context,
                            enable_kg=enable_kg,  # Phase KG: Pass per-request flag
                            enable_web_search=enable_web_search  # Stage 5: Pass web search flag
                        )
                        span.set_result(response)
                else:
                    response = await self.analyst.research(
                        query=query,
                        formatted_context=state.formatted_context,
                        mode=mode,
                        temporal_context=temporal_context,
                        enable_kg=enable_kg,  # Phase KG: Pass per-request flag
                        enable_web_search=enable_web_search  # Stage 5: Pass web search flag
                    )

                iteration_logger.log_agent_output(
                    iteration=iteration + 1,
                    agent_name="analyst_research",
                    input_prompt=f"Query: {query}\nMode: {mode}",
                    output_response=response
                )

            # Gap detection: Handle SEARCH_REQUIRED
            if response.status == "SEARCH_REQUIRED":
                self.logger.warning(
                    f"Analyst requested additional search (iteration {iteration + 1}): "
                    f"{response.new_queries}"
                )

                # Tracing: Gap detection
                if tracer:
                    tracer.condition_branch(
                        "GAP_DETECTION",
                        "SEARCH_REQUIRED",
                        {
                            "missing_information": response.missing_information,
                            "new_queries": response.new_queries
                        }
                    )

                # Send progress message to frontend
                await self._send_progress({
                    "message_type": "intermediate_result",
                    "stage": "gap_search_started",
                    "gap_reason": ", ".join(response.missing_information) if response.missing_information else "資料缺口",
                    "new_queries": response.new_queries,
                    "iteration": iteration + 1
                })

                # Execute secondary search for each new query
                secondary_results = []

                self._check_connection()  # Checkpoint 4: before secondary searches
                for new_query in response.new_queries:
                    try:
                        # Call retriever with same parameters as original search
                        results = await retriever_search(
                            query=new_query,
                            site=self.handler.site,
                            num_results=20,  # Smaller batch for gap search
                            query_params=self.handler.query_params,
                            handler=self.handler
                        )
                        secondary_results.extend(results)
                        self.logger.info(f"Gap search for '{new_query}': {len(results)} results")
                    except Exception as e:
                        self.logger.error(f"Secondary search failed for '{new_query}': {e}")

                # Handle search results
                if secondary_results:
                    # Filter and enrich new results
                    new_context = self.source_filter.filter_and_enrich(secondary_results, mode)

                    # Merge with existing context
                    state.current_context.extend(new_context)
                    self.logger.info(f"Added {len(new_context)} sources from secondary search (total: {len(state.current_context)})")

                    # Tracing: Secondary search context update
                    if tracer:
                        tracer.context_update(
                            "SECONDARY_SEARCH",
                            {
                                "queries_executed": response.new_queries,
                                "results_found": len(secondary_results),
                                "new_sources_added": len(new_context)
                            }
                        )

                    if enable_isolation:
                        # SEC-6: Only format NEW documents for LLM context
                        new_start_id = max(state.source_map.keys(), default=0) + 1
                        new_formatted, new_source_map = self._format_context_shared(
                            new_context, start_id=new_start_id
                        )
                        # SEC-6: Invariant check - new IDs must not collide with existing
                        overlap = set(new_source_map.keys()) & set(state.source_map.keys())
                        if overlap:
                            self.logger.error(f"SEC-6: source_map ID collision detected: {overlap}")
                        state.source_map.update(new_source_map)
                        state.formatted_context = new_formatted  # Only new docs for Analyst
                        # SYNC: Keep self.* in sync for helper methods that read self.source_map
                        self.formatted_context = state.formatted_context
                        self.source_map = state.source_map
                        self.logger.info(
                            f"SEC-6: gap_search new_docs={len(new_context)}, "
                            f"new_start_id={new_start_id}, total_source_map={len(state.source_map)}"
                        )
                    else:
                        # Re-format unified context with updated citations
                        state.formatted_context, state.source_map = self._format_context_shared(state.current_context)
                        # SYNC: Keep self.* in sync for helper methods that read self.source_map
                        self.formatted_context = state.formatted_context
                        self.source_map = state.source_map

                    # Continue to next iteration (Analyst will retry with expanded context)
                    iteration += 1
                    continue
                else:
                    # No results found - force Analyst to work with existing data
                    self.logger.warning("Secondary search returned no results")

                    # RSN-5: Rebuild formatted_context fresh instead of accumulating hints
                    # Re-format from source of truth to avoid stale hints from previous iterations
                    state.formatted_context, state.source_map = self._format_context_shared(state.current_context)
                    system_hint = "\n\n[系統提示] 針對缺口的補充搜尋未發現有效結果，請基於現有資訊推論。"
                    state.formatted_context += system_hint
                    # SYNC: Keep self.* in sync for helper methods
                    self.formatted_context = state.formatted_context
                    self.source_map = state.source_map

                    # Increment iteration and let it proceed to Critic evaluation
                    iteration += 1
                    if iteration >= max_iterations:
                        self.logger.error("Max iterations reached after failed gap search")
                        # Return error result
                        state.early_return = [{
                            "@type": "Item",
                            "url": "internal://error",
                            "name": "Deep Research 資料不足",
                            "site": "系統訊息",
                            "siteUrl": "internal",
                            "score": 0,
                            "description": (
                                f"**無法完成研究**\n\n"
                                f"原因：經過 {max_iterations} 次迭代後，仍然缺少關鍵資訊。\n\n"
                                f"**缺失的資訊**：\n" +
                                "\n".join(f"- {info}" for info in response.missing_information) +
                                f"\n\n**建議的補充搜尋**：\n" +
                                "\n".join(f"- {q}" for q in response.new_queries) +
                                "\n\n補充搜尋已執行但未找到相關結果。"
                            )
                        }]
                        await self._emit_phase_event("actor_critic_loop", "completed")
                        return state
                    # Do NOT continue - let it fall through to force Analyst to produce something
                    # (will be caught in next iteration with system hint)

            draft = response.draft

            # SEC-6: Track and validate citations
            if enable_isolation and response and hasattr(response, 'citations_used'):
                seen_citation_ids.update(response.citations_used)
                invalid_citations = [c for c in response.citations_used if c not in state.source_map]
                if invalid_citations:
                    self.logger.error(f"SEC-6: citations not in source_map: {invalid_citations}")

            # RSN-1: Guard against empty draft before proceeding to Critic
            if not draft or not draft.strip():
                self.logger.warning("Empty draft detected after gap resolution, cannot proceed to review")
                # If we still have iterations left, increment and retry
                if iteration + 1 < max_iterations:
                    self.logger.info("Retrying with next iteration due to empty draft")
                    iteration += 1
                    continue
                else:
                    # Max iterations exhausted with empty draft
                    self.logger.error("Max iterations reached with empty draft")
                    state.early_return = self._format_error_result(
                        query,
                        "分析階段無法產生有效內容，請嘗試調整搜尋條件或使用不同的查詢。"
                    )
                    await self._emit_phase_event("actor_critic_loop", "completed")
                    return state

            # Stage 5: Process gap_resolutions for web search
            gap_resolution_added_data = False
            if hasattr(response, 'gap_resolutions') and response.gap_resolutions:
                self.logger.info("="*80)
                self.logger.info(f"[STAGE 5] GAP DETECTION TRIGGERED - Found {len(response.gap_resolutions)} gap resolutions")
                for i, gap in enumerate(response.gap_resolutions, 1):
                    self.logger.info(f"  Gap {i}: type={gap.gap_type}, resolution={gap.resolution}, reason={gap.reason}")
                self.logger.info("="*80)

                self._check_connection()  # Checkpoint 5: before gap resolutions
                context_before = len(state.current_context)
                await self._process_gap_resolutions(
                    response=response,
                    mode=mode,
                    current_context=state.current_context,
                    enable_web_search=enable_web_search,
                    tracer=tracer,
                    query_id=query_id
                )
                context_after = len(state.current_context)
                gap_resolution_added_data = context_after > context_before
            else:
                self.logger.warning("[STAGE 5] No gap_resolutions found (gap_resolutions is empty or missing)")

            # If new data was added, re-run Analyst to integrate it
            if gap_resolution_added_data:
                self.logger.info(f"Gap resolution added {context_after - context_before} items. Re-running Analyst to integrate new data.")

                await self._send_progress({
                    "message_type": "intermediate_result",
                    "stage": "analyst_integrating_new_data"
                })

                # Re-run Analyst with enriched context
                # Stage 5: Simplified tracer input (avoid logging full context)
                analyst_input = {
                    "query": query,
                    "context_count": len(state.current_context),
                    "mode": mode,
                    "enable_web_search": False  # Don't trigger another round of web search
                }

                self._check_connection()  # Checkpoint 6: before analyst re-run with enriched data

                if enable_isolation:
                    # SEC-6: Format only NEW context items for Analyst
                    new_items = state.current_context[context_before:]
                    new_start_id = max(state.source_map.keys(), default=0) + 1
                    formatted_context_enriched, new_source_map = self._format_context_shared(
                        new_items, start_id=new_start_id
                    )
                    # SEC-6: Invariant check before merge
                    overlap = set(new_source_map.keys()) & set(state.source_map.keys())
                    if overlap:
                        self.logger.error(f"SEC-6: enriched source_map ID collision: {overlap}")
                    state.source_map.update(new_source_map)
                    # SYNC: Keep self.* in sync for helper methods
                    self.source_map = state.source_map
                    previous_draft_for_analyst = draft  # Pass previous draft so Analyst knows prior analysis
                    self.logger.info(
                        f"SEC-6: enriched re-run new_docs={len(new_items)}, "
                        f"previous_draft_len={len(draft) if draft else 0}, "
                        f"total_source_map={len(state.source_map)}"
                    )
                else:
                    # Format context for re-analysis with enriched data
                    formatted_context_enriched = "\n".join([
                        f"[{i+1}] {doc.get('title', 'Unknown')} ({doc.get('site', 'Unknown')})"
                        for i, doc in enumerate(state.current_context)
                    ])
                    previous_draft_for_analyst = None

                if tracer:
                    with tracer.agent_span("analyst", "research_with_enriched_data", analyst_input) as span:
                        response = await self.analyst.research(
                            query=query,
                            formatted_context=formatted_context_enriched,
                            mode=mode,
                            temporal_context=temporal_context,
                            enable_kg=enable_kg,
                            enable_web_search=False,  # Disable for re-analysis (already got data)
                            previous_draft=previous_draft_for_analyst  # SEC-6
                        )
                        span.set_result(response)
                else:
                    response = await self.analyst.research(
                        query=query,
                        formatted_context=formatted_context_enriched,
                        mode=mode,
                        temporal_context=temporal_context,
                        enable_kg=enable_kg,
                        enable_web_search=False,  # Disable for re-analysis (already got data)
                        previous_draft=previous_draft_for_analyst  # SEC-6
                    )

                draft = response.draft

                iteration_logger.log_agent_output(
                    iteration=iteration + 1,
                    agent_name="analyst_enriched",
                    input_prompt=f"Query: {query} (with {len(state.current_context)} enriched sources)",
                    output_response=response
                )

            # Send progress: Analyst complete
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "analyst_complete",
                "citations_count": len(response.citations_used)
            })

            # Critic: Review draft
            # Send progress: Critic reviewing
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "critic_reviewing"
            })

            self._check_connection()  # Checkpoint 7: before critic.review()
            self.logger.info("Critic reviewing draft")
            critic_input = {
                "draft": draft,
                "query": query,
                "mode": mode
            }

            # SEC-6: Build critic context (reference sheet or full context)
            if enable_isolation and hasattr(response, 'citations_used') and response.citations_used:
                ref_sheet = self._build_critic_reference_sheet(response.citations_used)
                iso_config = CONFIG.reasoning_params.get("agent_isolation", {})
                min_chars = iso_config.get("critic_reference_sheet_min_chars", 1000)
                min_citations = iso_config.get("critic_reference_sheet_min_citations", 2)
                if (len(ref_sheet) < min_chars
                        or len(response.citations_used) < min_citations):
                    # Rebuild full context from source of truth (self.formatted_context
                    # may only contain latest batch in isolation mode)
                    full_context, _ = self._format_context_shared(state.current_context)
                    self.logger.info(
                        f"SEC-6: Reference sheet too small "
                        f"(chars={len(ref_sheet)}<{min_chars} or "
                        f"citations={len(response.citations_used)}<{min_citations}), "
                        f"falling back to full context ({len(full_context)} chars)"
                    )
                    critic_context = full_context
                else:
                    full_len = len(state.formatted_context)
                    reduction = 1.0 - (len(ref_sheet) / full_len) if full_len > 0 else 0
                    self.logger.info(
                        f"SEC-6: critic ref_sheet={len(ref_sheet)} chars "
                        f"(full={full_len}, reduction={reduction:.0%})"
                    )
                    critic_context = ref_sheet
            else:
                critic_context = state.formatted_context

            if tracer:
                with tracer.agent_span("critic", "review", critic_input) as span:
                    review = await self.critic.review(
                        draft, query, mode,
                        analyst_output=response,
                        formatted_context=critic_context  # Phase 2 CoV / SEC-6
                    )
                    span.set_result(review)
            else:
                review = await self.critic.review(
                    draft, query, mode,
                    analyst_output=response,
                    formatted_context=critic_context  # Phase 2 CoV / SEC-6
                )

            iteration_logger.log_agent_output(
                iteration=iteration + 1,
                agent_name="critic",
                input_prompt=f"Draft: {draft[:100]}...",
                output_response=review
            )

            # Send progress: Critic complete
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "critic_complete",
                "status": review.status
            })

            # Check convergence
            # Tracing: Convergence check
            if tracer:
                tracer.condition_branch(
                    "CONVERGENCE",
                    review.status,
                    {
                        "critique": review.critique[:200] + "..." if len(review.critique) > 200 else review.critique,
                        "suggestions": review.suggestions,
                        "mode_compliance": review.mode_compliance
                    }
                )

            if review.status in ["PASS", "WARN"]:
                self.logger.info(f"Convergence achieved: {review.status}")
                # Tracing: Iteration end
                if tracer:
                    tracer.end_iteration()
                break

            iteration += 1

        # === Post-loop: Write results back to state ===
        state.draft = draft
        state.review = review
        state.response = response
        state.iteration = iteration
        state.reject_count = reject_count
        state.seen_citation_ids = seen_citation_ids

        # Check if we have a valid draft
        if not draft:
            self.logger.error("No draft generated after iterations")
            # Check if this was due to continuous SEARCH_REQUIRED without results
            if response and response.status == "SEARCH_REQUIRED":
                state.early_return = self._format_friendly_no_data_result(
                    query=query,
                    mode=mode,
                    missing_info=response.missing_information,
                    attempted_queries=response.new_queries,
                    reasoning_chain=response.reasoning_chain
                )
                await self._emit_phase_event("actor_critic_loop", "completed")
                return state
            # Otherwise, generic error (include reasoning if available)
            error_details = ""
            if response and hasattr(response, 'reasoning_chain') and response.reasoning_chain:
                error_details = f"\n\n**分析過程：**\n{response.reasoning_chain}"
            state.early_return = self._format_error_result(
                query,
                f"Failed to generate draft{error_details}"
            )
            await self._emit_phase_event("actor_critic_loop", "completed")
            return state

        # Graceful degradation check
        if reject_count >= max_iterations and review.status == "REJECT":
            self.logger.warning(
                f"Max iterations with continuous REJECTs ({reject_count}). "
                f"Degrading gracefully."
            )
            # Add warning to critique (Pydantic models are immutable by default)
            # We'll pass original review to Writer, which will handle REJECT status

        # SEC-6: Writer draft length monitoring
        if draft and enable_isolation:
            threshold = CONFIG.reasoning_params.get("agent_isolation", {}).get(
                "draft_length_warning_threshold", 20000
            )
            if len(draft) > threshold:
                self.logger.warning(
                    f"SEC-6: draft length {len(draft)} exceeds threshold {threshold}"
                )

        # SEC-5: Validate analyst citations against source_map
        raw_citations = response.citations_used
        valid_citations = [c for c in raw_citations if c in state.source_map]
        if len(valid_citations) < len(raw_citations):
            removed = set(raw_citations) - set(valid_citations)
            logger.warning(f"Removed phantom citations not in source_map: {removed}")
        state.analyst_citations = valid_citations

        await self._emit_phase_event("actor_critic_loop", "completed")
        return state

    async def _phase_writer(self, state: 'ResearchState') -> 'ResearchState':
        """
        Phase 3: Writer compose + Hallucination Guard.

        Contains: plan_and_write feature flag check, writer.compose(),
                  hallucination guard (set operation), progress messages.

        Reads: state.draft, state.review, state.response, state.analyst_citations,
               state.source_map, state.query, state.mode, state.iteration,
               state.max_iterations, state.tracer, state.iteration_logger
        Writes: state.final_report, state.plan

        Raises:
            ResearchCancelledError: If client disconnects (checkpoint 8)
        """
        await self._emit_phase_event("writer", "started")

        # Check if plan-and-write is enabled
        enable_plan_and_write = CONFIG.reasoning_params.get("features", {}).get(
            "plan_and_write", False
        )

        self._check_connection()  # Checkpoint 8: before writer phase

        plan = None
        if enable_plan_and_write:
            # Step 1: Plan
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "writer_planning",
                "iteration": state.iteration + 1,
                "total_iterations": state.max_iterations
            })

            self.logger.info("Writer planning report structure")
            plan = await self.writer.plan(
                analyst_draft=state.draft,
                critic_review=state.review,
                user_query=state.query,
                target_length=2000
            )

            # Step 2: Compose
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "writer_composing",
                "iteration": state.iteration + 1,
                "total_iterations": state.max_iterations
            })

            self.logger.info("Writer composing long-form report based on plan")
        else:
            # Standard single-step compose
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "writer_composing"
            })

            self.logger.info("Writer composing final report")

        # Build citation details for logging (show what citations Writer can use)
        citation_details = {}
        for cid in state.analyst_citations:
            if cid in state.source_map:
                item = state.source_map[cid]
                if isinstance(item, dict):
                    title = item.get("title") or item.get("name", "No title")
                    url = item.get("url") or item.get("link", "")
                elif isinstance(item, (list, tuple)) and len(item) > 0:
                    title = item[2] if len(item) > 2 else "No title"
                    url = item[0] if len(item) > 0 else ""
                else:
                    title = "Unknown"
                    url = ""
                citation_details[cid] = f"{title[:60]}... ({url[:40]}...)" if url else title[:60]

        writer_input = {
            "analyst_draft": state.draft[:200] + "...",  # Show preview
            "critic_review": state.review,
            "analyst_citations": state.analyst_citations,
            "citation_details": citation_details,  # Show actual source info
            "mode": state.mode,
            "user_query": state.query
        }

        if state.tracer:
            with state.tracer.agent_span("writer", "compose", writer_input) as span:
                final_report = await self.writer.compose(
                    analyst_draft=state.draft,
                    critic_review=state.review,
                    analyst_citations=state.analyst_citations,
                    mode=state.mode,
                    user_query=state.query,
                    plan=plan  # Pass plan (None if not enabled)
                )
                span.set_result(final_report)
        else:
            final_report = await self.writer.compose(
                analyst_draft=state.draft,
                critic_review=state.review,
                analyst_citations=state.analyst_citations,
                mode=state.mode,
                user_query=state.query,
                plan=plan  # Pass plan (None if not enabled)
            )

        state.iteration_logger.log_agent_output(
            iteration=state.iteration + 1,
            agent_name="writer",
            input_prompt=f"Draft: {state.draft[:100]}...",
            output_response=final_report
        )

        # Send progress: Writer complete
        await self._send_progress({
            "message_type": "intermediate_result",
            "stage": "writer_complete"
        })

        # Hallucination Guard: Verify Writer sources subset of Analyst citations
        invalid_sources = []
        needs_correction = False
        if not set(final_report.sources_used).issubset(set(state.analyst_citations)):
            self.logger.error(
                f"Writer hallucination detected: {final_report.sources_used} "
                f"not subset of {state.analyst_citations}"
            )
            # Auto-correct: Only keep intersection (Pydantic models are immutable)
            corrected_sources = list(set(final_report.sources_used) & set(state.analyst_citations))
            invalid_sources = list(set(final_report.sources_used) - set(state.analyst_citations))
            needs_correction = True
            self.logger.warning(f"Corrected sources from {final_report.sources_used} to: {corrected_sources}")

            # Create corrected version (rebuild model with corrected data)
            final_report = WriterComposeOutput(
                final_report=final_report.final_report,
                sources_used=corrected_sources,
                confidence_level="Low",
                methodology_note=final_report.methodology_note + " [自動修正：移除未驗證來源]"
            )

        # Tracing: Hallucination guard
        if state.tracer:
            state.tracer.condition_branch(
                "HALLUCINATION_GUARD",
                "PASSED" if not needs_correction else "CORRECTED",
                {
                    "writer_sources": final_report.sources_used,
                    "analyst_sources": list(state.source_map.keys()),
                    "invalid_sources": invalid_sources if needs_correction else []
                }
            )

        state.final_report = final_report
        state.plan = plan

        await self._emit_phase_event("writer", "completed")
        return state

    async def _phase_format_result(self, state: 'ResearchState') -> 'ResearchState':
        """
        Phase 4: Session logging + Chain Analysis + Format NLWeb result.

        Contains: iteration_logger.log_summary(), reasoning chain analysis
                  (if argument_graph exists), RSN-4 verification_status transfer,
                  _format_result() call, tracing end.

        Reads: state.response, state.review, state.final_report, state.iteration,
               state.current_context, state.query, state.mode, state.tracer,
               state.iteration_logger, state.items
        Writes: state.chain_analysis, state.result
        """
        await self._emit_phase_event("format_result", "started")

        # Log session summary
        state.iteration_logger.log_summary(
            total_iterations=state.iteration + 1,
            final_status=state.review.status,
            mode=state.mode,
            metadata={
                "sources_analyzed": len(state.current_context),
                "sources_filtered": len(state.items) - len(state.current_context)
            }
        )

        # Phase 3.5: Analyze reasoning chain if argument_graph exists
        if hasattr(state.response, 'argument_graph') and state.response.argument_graph:
            from reasoning.utils.chain_analyzer import ReasoningChainAnalyzer

            self.logger.info("Analyzing reasoning chain for impact and critical nodes")

            # Get weaknesses from critic
            weaknesses = getattr(state.review, 'structured_weaknesses', None)

            # Analyze chain
            try:
                analyzer = ReasoningChainAnalyzer(state.response.argument_graph, weaknesses)
                chain_analysis = analyzer.analyze()

                # B9/C3: preserve runtime type (incl. a future Live response)
                # instead of narrowing to a fixed Enhanced/KG type. model_copy
                # keeps the subclass + all fields; only reasoning_chain_analysis
                # is updated. (Does NOT re-run validation — value is deterministic.)
                state.response = state.response.model_copy(
                    update={"reasoning_chain_analysis": chain_analysis}
                )

                state.chain_analysis = chain_analysis

                self.logger.info(
                    f"Chain analysis: {len(chain_analysis.critical_nodes)} critical nodes, "
                    f"max_depth={chain_analysis.max_depth}, "
                    f"logic_inconsistencies={chain_analysis.logic_inconsistencies}"
                )

                # Display in console tracer (Developer Mode in Terminal)
                if state.tracer:
                    state.tracer.reasoning_chain_analysis(state.response.argument_graph, chain_analysis)

            except Exception as e:
                self.logger.error(f"Failed to analyze reasoning chain: {e}", exc_info=True)

        # RSN-4: Transfer verification_status from critic review to final_report
        # critic.py sets these fields dynamically on review.__dict__ when CoV fails.
        # _format_result reads them from final_report.__dict__ to include in schema_obj.
        if state.review and state.review.__dict__.get("verification_status"):
            state.final_report.__dict__["verification_status"] = state.review.__dict__["verification_status"]
            state.final_report.__dict__["verification_message"] = state.review.__dict__.get(
                "verification_message", "本報告未經完整事實驗證"
            )
            self.logger.info(
                f"RSN-4: Transferred verification_status='{state.review.__dict__['verification_status']}' "
                "from critic review to final_report"
            )

        # Phase 4: Format as NLWeb result (pass context for source extraction)
        state.result = self._format_result(
            state.query, state.mode, state.final_report,
            state.iteration + 1, state.current_context,
            analyst_output=state.response
        )
        self.logger.info(f"Research completed: {state.iteration + 1} iterations")

        # Tracing: Research end
        # RSN-10: Safe access to tracer.start_time
        if state.tracer:
            start_time = getattr(state.tracer, 'start_time', None)
            if start_time is None:
                start_time = time.time()  # fallback to now
            total_time = time.time() - start_time
        else:
            total_time = 0
        if state.tracer:
            state.tracer.end_research(
                final_status=state.review.status,
                iterations=state.iteration + 1,
                total_time=total_time
            )

        await self._emit_phase_event("format_result", "completed")
        return state

    async def _filter_and_prepare_sources(
        self,
        items: List[Dict[str, Any]],
        mode: str,
        tracer,
    ) -> List[Dict[str, Any]]:
        """
        Apply source tier filtering based on research mode.

        Returns:
            Filtered items list

        Raises:
            ValueError: If no sources remain after filtering
        """
        # Phase 1: Filter and enrich context by source tier
        current_context = self.source_filter.filter_and_enrich(items, mode)
        self.logger.info(f"Filtered context: {len(current_context)} sources (from {len(items)})")

        # Tracing: Source filtering
        if tracer:
            tracer.source_filtering(
                original_items=items,
                filtered_items=current_context,
                mode=mode
            )

        # Check if we have any sources to work with
        if not current_context or len(current_context) == 0:
            self.logger.error(f"No sources available for research! Original items: {len(items)}")
            raise ValueError(
                f"No sources available after filtering. "
                f"Original: {len(items)}, Filtered: 0, Mode: {mode}"
            )

        return current_context

    async def _format_research_context(
        self,
        items: List[Dict[str, Any]],
        tracer,
    ) -> tuple[str, Dict[int, Dict[str, Any]]]:
        """
        Format items into citation context.

        Returns:
            Tuple of (formatted_context_string, source_id_map)
        """
        # Unified context formatting (Single Source of Truth)
        formatted_context, source_map = self._format_context_shared(items)

        # Tracing: Context formatted
        if tracer:
            tracer.context_formatted(
                source_map=source_map,
                formatted_context=formatted_context
            )

        return formatted_context, source_map

    def _create_no_sources_error_response(
        self,
        items_count: int,
        filtered_count: int,
        mode: str,
    ) -> List[Dict[str, Any]]:
        """Create error response for no sources case."""
        return [{
            "@type": "Item",
            "url": "internal://error",
            "name": "Deep Research 無法執行",
            "site": "系統訊息",
            "siteUrl": "internal",
            "score": 0,
            "description": (
                f"**錯誤：無法執行 Deep Research**\n\n"
                f"原因：檢索階段未找到任何相關資料來源。\n\n"
                f"- 檢索到的項目數：{items_count}\n"
                f"- 經過濾後的項目數：{filtered_count}\n\n"
                f"請嘗試：\n"
                f"1. 使用不同的關鍵詞重新搜尋\n"
                f"2. 在搜尋介面調整來源篩選，包含更多新聞來源\n"
                f"3. 確認資料庫中有相關內容"
            )
        }]


    async def run_research(
        self,
        query: str,
        mode: str,
        items: List[Dict[str, Any]],
        temporal_context: Optional[Dict[str, Any]] = None,
        enable_kg: bool = False,
        enable_web_search: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Execute deep research. Dispatches to composable or legacy pipeline based on feature flag.

        Args:
            query: User's research question
            mode: Research mode (strict, discovery, monitor)
            items: Retrieved items from search (pre-filtered by temporal range)
            temporal_context: Optional temporal information
            enable_kg: Enable knowledge graph generation (Phase KG, per-request override)
            enable_web_search: Enable web search for dynamic data (Stage 5)

        Returns:
            List of NLWeb Item dicts compatible with create_assistant_result().
        """
        # composable_pipeline flag 曾用於 gate Tasks 0-4 refactor；legacy 與 composable
        # 已 zero-behavior-change 收斂為同一實作（見原 _run_research_legacy docstring）。
        # config 顯式設 composable_pipeline: true → legacy 在 prod 不會被走到（dead-in-prod）。
        # 直呼 composable，flag 異常時發 warning 觀測 log（不再分支）。
        # I-1：下面 default 從現行 .get(..., False) 翻成 True 是 intentional 且等價 ——
        # config 已有此 key 並設 true，default 翻 True 只是「config 萬一遺失時也走 composable」，
        # 不改變現行（config=true）行為。
        # F1（不可 silent fail）：log 條件必須涵蓋「key 完全缺失」與「明確 false」兩種異常。
        # 若只寫 `if not ...get(..., True)`，key 缺失時 get 回 True → not True → False → 靜默，
        # 反而比舊 code（default=False、缺 key 走 legacy 發 warning）更安靜 → 違反不可 silent fail。
        # 故顯式檢查 key 是否在 features 中，缺失或 false 都發 warning。
        features = CONFIG.reasoning_params.get("features", {})
        if "composable_pipeline" not in features or not features.get("composable_pipeline", True):
            logger.warning(
                "run_research: composable_pipeline 未明確啟用（key 缺失或 false）— "
                "legacy 與 composable 已合一，直呼 composable"
            )
        return await self._run_research_composable(
            query, mode, items, temporal_context, enable_kg, enable_web_search
        )

    async def _run_research_composable(
        self,
        query: str,
        mode: str,
        items: List[Dict[str, Any]],
        temporal_context: Optional[Dict[str, Any]] = None,
        enable_kg: bool = False,
        enable_web_search: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Composable phase pipeline for deep research.

        Each phase reads/writes ResearchState, enabling:
        - Non-blocking execution (asyncio.create_task, Task 6)
        - Phase-level cache / timeout / monitoring
        - Selective re-run
        - Cancel at phase boundary

        Args:
            query: User's research question
            mode: Research mode (strict, discovery, monitor)
            items: Retrieved items from search (pre-filtered by temporal range)
            temporal_context: Optional temporal information
            enable_kg: Enable knowledge graph generation (Phase KG, per-request override)
            enable_web_search: Enable web search for dynamic data (Stage 5)

        Returns:
            List of NLWeb Item dicts compatible with create_assistant_result().
            Each dict contains: @type, url, name, site, siteUrl, score, description

        Raises:
            NoValidSourcesError: If no sources are available
        """
        # Setup: Initialize logging and tracing
        query_id = getattr(self.handler, 'query_id', f'reasoning_{hash(query)}')

        # Note: log_query_start() is now called in api.py before handler.runQuery()
        # to avoid FK violations. No need to call it again here.

        iteration_logger, tracer = self._setup_research_session(
            query_id=query_id,
            query=query,
            mode=mode,
            items=items,
            enable_web_search=enable_web_search,
        )

        # Initialize explicit research state
        state = ResearchState(
            query=query,
            mode=mode,
            items=items,
            temporal_context=temporal_context,
            enable_kg=enable_kg,
            enable_web_search=enable_web_search,
            query_id=query_id,
            iteration_logger=iteration_logger,
            tracer=tracer,
            max_iterations=CONFIG.reasoning_params.get("max_iterations", 3),
            enable_isolation=CONFIG.reasoning_params.get("features", {}).get("agent_isolation", False),
        )

        logger.info(f"[Orchestrator] ResearchState initialized: enable_kg={enable_kg}, enable_web_search={enable_web_search}, query_id={query_id}")

        try:
            # Phase 1: Filter and prepare sources
            state = await self._phase_filter_and_prepare(state)
            if state.early_return is not None:
                return state.early_return

            # Cache state for potential KG editing selective re-run
            _cache_research_state(state.query_id, state)

            # Phase 2: Actor-Critic Loop
            state = await self._phase_actor_critic_loop(state)
            if state.early_return is not None:
                return state.early_return

            # Phase 3: Writer + Hallucination Guard
            state = await self._phase_writer(state)

            # Phase 4: Session logging + Chain Analysis + Format Result
            state = await self._phase_format_result(state)
            return state.result

        except ResearchCancelledError:
            self.logger.info("Deep Research cancelled: client disconnected")
            print("[CANCEL] Deep Research cancelled - client disconnected, no further LLM calls")
            return []

        except NoValidSourcesError as e:
            self.logger.error(f"No valid sources after filtering: {e}")
            if tracer:
                tracer.error(f"No valid sources after filtering: {e}")
            return self._format_error_result(
                query,
                f"No valid sources available in {mode} mode. Try using 'discovery' mode for broader source coverage."
            )

        except (asyncio.TimeoutError, TimeoutError) as e:
            self.logger.error(f"Timeout error in orchestrator: {e}")
            if tracer:
                tracer.error(f"Research timeout: {str(e)}")
            return self._format_error_result(
                query,
                "研究請求超時，請稍後再試或縮小搜尋範圍。"
            )

        except (ConnectionError, OSError) as e:
            self.logger.error(f"Network error in orchestrator: {e}")
            if tracer:
                tracer.error(f"Network error: {str(e)}")
            return self._format_error_result(
                query,
                "網路連線發生問題，請檢查連線後再試。"
            )

        except Exception as e:
            self.logger.critical(f"Unexpected error in orchestrator: {e}", exc_info=True)
            if tracer:
                tracer.error(f"Research failed: {str(e)}", exception=e)
            # Re-raise in development/testing mode
            if CONFIG.should_raise_exceptions():
                raise
            sentry_sdk.capture_exception(e)
            return self._format_error_result(query, f"系統發生未預期錯誤: {str(e)}")

    async def run_research_rerun(
        self,
        original_query_id: str,
        modified_query: str,
    ) -> List[Dict[str, Any]]:
        """
        Selective re-run: skip search phase, reuse cached formatted_context.
        Used when user edits KG and wants re-analysis with same articles.

        Args:
            original_query_id: query_id of the original research whose cached state to reuse
            modified_query: the modified query with KG edit instructions appended

        Returns:
            List of NLWeb Item dicts compatible with create_assistant_result().

        Raises:
            ValueError: if no cached state exists for the given query_id
        """
        cached = get_cached_research_state(original_query_id)
        if not cached:
            raise ValueError(f"No cached research state for query_id={original_query_id}")

        # Create a new query_id for the rerun
        new_query_id = f"rerun_{original_query_id}_{abs(hash(modified_query)) % 10**8}"

        # Ensure new_query_id exists in queries table for FK references (analytics, tier_6, etc.)
        try:
            from core.query_logger import get_query_logger
            ql = get_query_logger()
            if ql:
                ql.log_query_start(
                    query_id=new_query_id,
                    user_id=getattr(self.handler, 'user_id', '') or '',
                    query_text=modified_query,
                    site=getattr(self.handler, 'site', 'all'),
                    mode='deep_research_rerun',
                )
        except Exception as e:
            self.logger.warning(f"Failed to log rerun query start (non-fatal): {e}")

        # Setup logging/tracing for the new session
        iteration_logger, tracer = self._setup_research_session(
            query_id=new_query_id,
            query=modified_query,
            mode=cached['mode'],
            items=cached['items'],
            enable_web_search=cached['enable_web_search'],
        )

        # Build new state with modified query, reusing cached context
        state = ResearchState(
            query=modified_query,
            mode=cached['mode'],
            items=cached['items'],
            temporal_context=cached['temporal_context'],
            enable_kg=cached['enable_kg'],
            enable_web_search=cached['enable_web_search'],
            query_id=new_query_id,
            iteration_logger=iteration_logger,
            tracer=tracer,
            max_iterations=CONFIG.reasoning_params.get("max_iterations", 3),
            enable_isolation=CONFIG.reasoning_params.get("features", {}).get("agent_isolation", False),
            is_rerun=True,
        )

        # Restore cached context from phase 1 (skip search)
        state.formatted_context = cached['formatted_context']
        state.source_map = cached['source_map']
        state.current_context = cached['current_context']

        self.logger.info(
            f"[RERUN] Starting selective re-run: original_query_id={original_query_id}, "
            f"new_query_id={new_query_id}, sources={len(state.source_map)}"
        )

        try:
            # Emit phase event for rerun start
            await self._emit_phase_event("rerun", "started")

            # Run phases 2-4 only (skip phase 1 search)
            state = await self._phase_actor_critic_loop(state)
            if state.early_return is not None:
                return state.early_return

            state = await self._phase_writer(state)

            state = await self._phase_format_result(state)
            return state.result

        except ResearchCancelledError:
            self.logger.info("Selective re-run cancelled: client disconnected")
            return []

        except (asyncio.TimeoutError, TimeoutError) as e:
            self.logger.error(f"Timeout error in rerun: {e}")
            if tracer:
                tracer.error(f"Rerun timeout: {str(e)}")
            return self._format_error_result(
                modified_query,
                "重新分析請求超時，請稍後再試。"
            )

        except (ConnectionError, OSError) as e:
            self.logger.error(f"Network error in rerun: {e}")
            if tracer:
                tracer.error(f"Network error: {str(e)}")
            return self._format_error_result(
                modified_query,
                "網路連線發生問題，請檢查連線後再試。"
            )

        except Exception as e:
            self.logger.critical(f"Unexpected error in rerun: {e}", exc_info=True)
            if tracer:
                tracer.error(f"Rerun failed: {str(e)}", exception=e)
            if CONFIG.should_raise_exceptions():
                raise
            sentry_sdk.capture_exception(e)
            return self._format_error_result(modified_query, f"重新分析發生錯誤: {str(e)}")

    def _format_result(
        self,
        query: str,
        mode: str,
        final_report: Dict[str, Any],
        iterations: int,
        context: List[Any],
        analyst_output=None
    ) -> List[Dict[str, Any]]:
        """
        Format final report as NLWeb Item.

        Args:
            query: User's query
            mode: Research mode
            final_report: Final report from writer
            iterations: Number of iterations completed
            context: Source items used
            analyst_output: Optional analyst output with knowledge graph (Phase KG)

        Returns:
            List with single NLWeb Item dict

        ⚠️ CRITICAL: Must match schema expected by create_assistant_result()
        """
        # Convert source_map to URL array for frontend citation linking
        # Frontend expects: sources[0] = URL for [1], sources[1] = URL for [2], etc.
        # We build a complete array from citation ID 1 to max ID used
        source_urls = []
        writer_citations = final_report.sources_used  # List of citation IDs like [1, 4, 10, 18...]
        # Bug #25 Plan A: Extend max_cid to cover Writer's actual citations (even if out of source_map range)
        max_cid = max(
            max(self.source_map.keys(), default=0),
            max(writer_citations, default=0)
        )

        self.logger.info(f"Writer cited {len(writer_citations)} sources: {writer_citations}")
        self.logger.info(f"Building complete source URL array from 1 to {max_cid}")

        for cid in range(1, max_cid + 1):
            if cid in self.source_map:
                item = self.source_map[cid]
                # Handle both dict and tuple formats
                if isinstance(item, dict):
                    url = item.get("url") or item.get("link", "")
                elif isinstance(item, (list, tuple)) and len(item) > 0:
                    url = item[0]  # First element is URL in tuple format
                else:
                    url = ""
                    self.logger.warning(f"Citation ID {cid} has invalid format: {type(item)}")

                # Add Chrome Text Fragment for paragraph-level deep linking
                if url and not url.startswith(("urn:", "private://")):
                    description = ""
                    if isinstance(item, dict):
                        description = item.get("description") or item.get("articleBody", "")
                    if description:
                        snippet = description.strip()[:80].strip()
                        if snippet:
                            url = f"{url}#:~:text={quote(snippet)}"

                source_urls.append(url if url else "")  # Keep empty string to maintain index alignment
            else:
                # Missing citation ID - maintain index alignment with empty string
                source_urls.append("")
                self.logger.warning(f"Citation ID {cid} missing in source_map")

        self.logger.info(f"Converted source_map ({len(self.source_map)} items) to {len(source_urls)} URLs for frontend")

        # Serialize knowledge graph if present (Phase KG)
        kg_json = None
        if analyst_output and hasattr(analyst_output, 'knowledge_graph') and analyst_output.knowledge_graph:
            kg = analyst_output.knowledge_graph
            kg_json = {
                "entities": [e.model_dump() for e in kg.entities],
                "relationships": [r.model_dump() for r in kg.relationships],
                "metadata": {
                    "generated_at": datetime.now().isoformat(),
                    "entity_count": len(kg.entities),
                    "relationship_count": len(kg.relationships)
                }
            }
            self.logger.info(f"Serialized knowledge graph: {len(kg.entities)} entities, {len(kg.relationships)} relationships")

        # Build schema_object
        schema_obj = {
            "@type": "ResearchReport",
            "mode": mode,
            "iterations": iterations,
            "sources_used": source_urls,  # Now contains actual URLs instead of citation IDs
            "confidence": final_report.confidence_level,
            "methodology": final_report.methodology_note,
            "total_sources_analyzed": len(context)
        }

        # Add knowledge graph if available (Phase KG)
        if kg_json:
            schema_obj["knowledge_graph"] = kg_json

        # Add reasoning chain if available (Phase 4)
        if analyst_output and hasattr(analyst_output, 'argument_graph') and analyst_output.argument_graph:
            schema_obj["argument_graph"] = [node.model_dump() for node in analyst_output.argument_graph]

            if hasattr(analyst_output, 'reasoning_chain_analysis') and analyst_output.reasoning_chain_analysis:
                schema_obj["reasoning_chain_analysis"] = analyst_output.reasoning_chain_analysis.model_dump()

        # RSN-4: Add verification status if CoV failed (set dynamically on final_report by orchestrator)
        verification_status = final_report.__dict__.get("verification_status")
        if verification_status:
            schema_obj["verification_status"] = verification_status
            verification_message = final_report.__dict__.get("verification_message")
            if verification_message:
                schema_obj["verification_message"] = verification_message

        return [{
            "@type": "Item",
            "url": f"https://deep-research.internal/{mode}/{query[:50]}",
            "name": f"深度研究報告：{query}",
            "site": "Deep Research Module",
            "siteUrl": "https://deep-research.internal",
            "score": 95,
            "description": final_report.final_report,
            "schema_object": schema_obj
        }]

    def _format_friendly_no_data_result(
        self,
        query: str,
        mode: str,
        missing_info: List[str],
        attempted_queries: List[str],
        reasoning_chain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Format a user-friendly response when no relevant data is found.

        Args:
            query: User's query
            mode: Research mode
            missing_info: List of missing information identified by Analyst
            attempted_queries: List of supplementary queries that were attempted
            reasoning_chain: Optional detailed reasoning from Analyst

        Returns:
            List with single NLWeb Item dict with friendly no-data message
        """
        # Build friendly description
        description_parts = [
            f"# 抱歉，目前找不到關於「{query}」的相關資料\n",
            f"## 搜尋說明\n",
            f"我們已經在 **{mode.upper()} 模式**下進行了深度搜尋，但資料庫中沒有找到符合條件的新聞或資料。\n"
        ]

        if missing_info:
            description_parts.append("\n### 缺少的關鍵資訊：\n")
            for info in missing_info:
                description_parts.append(f"- {info}\n")

        if attempted_queries:
            description_parts.append("\n### 已嘗試的補充搜尋：\n")
            for q in attempted_queries:
                description_parts.append(f"- `{q}`\n")

        # Add detailed reasoning if available (optional, for transparency)
        if reasoning_chain:
            description_parts.append("\n---\n")
            description_parts.append("\n<details>\n<summary>📊 詳細分析過程（點擊展開）</summary>\n\n")
            description_parts.append(reasoning_chain)
            description_parts.append("\n</details>\n")

        description_parts.extend([
            "\n---\n",
            "\n## 建議您可以：\n",
            "1. **調整關鍵字**：嘗試使用不同的詞彙或更廣泛的搜尋詞\n",
            "2. **擴大時間範圍**：如果您指定了特定日期，可以嘗試更寬的時間範圍\n",
            "3. **調整來源範圍**：在搜尋介面開啟更多新聞來源（或包含全部來源）\n",
            "4. **確認資料可用性**：有些資訊可能尚未被收錄到資料庫中\n",
            "\n有其他想了解的內容嗎？"
        ])

        return [{
            "@type": "Item",
            "url": "https://deep-research.internal/no-data",
            "name": f"找不到相關資料：{query}",
            "site": "Deep Research Module",
            "siteUrl": "https://deep-research.internal",
            "score": 0,
            "description": "".join(description_parts),
            "schema_object": {
                "@type": "NoDataReport",
                "query": query,
                "mode": mode,
                "missing_information": missing_info,
                "attempted_queries": attempted_queries
            }
        }]

    def _format_error_result(
        self,
        query: str,
        error_message: str
    ) -> List[Dict[str, Any]]:
        """
        Format error as NLWeb Item.

        Args:
            query: User's query
            error_message: Error description

        Returns:
            List with single NLWeb Item dict containing error message
        """
        return [{
            "@type": "Item",
            "url": "https://deep-research.internal/error",
            "name": f"研究錯誤：{query}",
            "site": "深度研究模組",
            "siteUrl": "https://deep-research.internal",
            "score": 0,
            "description": f"## 錯誤\n\n{error_message}",
            "schema_object": {
                "@type": "ErrorReport",
                "error": error_message
            }
        }]

    async def _process_gap_resolutions(
        self,
        response: Any,
        mode: str,
        current_context: List[Dict[str, Any]],
        enable_web_search: bool,
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """
        Process gap_resolutions from Analyst output (Stage 5).

        Handles multiple types of gap resolution:
        1. LLM Knowledge: Creates virtual documents with URN
        2. Web Search: Executes Google search if enabled
        3. Internal Search: Uses existing vector DB (handled by main loop)
        4. Stock APIs: STOCK_TW (TWSE/TPEX), STOCK_GLOBAL (yfinance)
        5. Wikipedia: Direct Wikipedia API call

        Args:
            response: Analyst output with gap_resolutions
            mode: Research mode
            current_context: Current context list (modified in place)
            enable_web_search: Whether web search is enabled
            tracer: Optional console tracer
            query_id: Query ID for analytics logging
        """
        from reasoning.schemas_enhanced import GapResolutionType

        web_search_gaps = []
        llm_knowledge_items = []
        stock_tw_gaps = []
        stock_global_gaps = []
        wikipedia_gaps = []
        weather_tw_gaps = []
        weather_global_gaps = []
        company_tw_gaps = []
        company_global_gaps = []

        for gap in response.gap_resolutions:
            if gap.resolution == GapResolutionType.LLM_KNOWLEDGE:
                # Create virtual document for LLM knowledge
                topic = gap.topic or gap.gap_type.replace(" ", "_")
                urn = f"urn:llm:knowledge:{topic}"

                virtual_doc = {
                    "url": urn,
                    "title": f"AI 背景知識：{gap.gap_type}",
                    "site": "LLM Knowledge",
                    "description": f"[Tier 6 | llm_knowledge] {gap.llm_answer or ''}",
                    "_reasoning_metadata": {
                        "tier": 6,
                        "type": "llm_knowledge",
                        "original_source": "LLM Knowledge",
                        "gap_type": gap.gap_type,
                        "confidence": gap.confidence
                    }
                }
                llm_knowledge_items.append(virtual_doc)
                self.logger.info(f"Created LLM knowledge document: {urn}")

            elif gap.resolution == GapResolutionType.WEB_SEARCH:
                if enable_web_search and gap.search_query:
                    web_search_gaps.append(gap)
                elif gap.requires_web_search:
                    # Mark as needing web search but not enabled
                    self.logger.info(f"Web search required but not enabled for: {gap.search_query}")

            elif gap.resolution == GapResolutionType.STOCK_TW:
                stock_tw_gaps.append(gap)

            elif gap.resolution == GapResolutionType.STOCK_GLOBAL:
                stock_global_gaps.append(gap)

            elif gap.resolution == GapResolutionType.WIKIPEDIA:
                wikipedia_gaps.append(gap)

            elif gap.resolution == GapResolutionType.WEATHER_TW:
                weather_tw_gaps.append(gap)

            elif gap.resolution == GapResolutionType.WEATHER_GLOBAL:
                weather_global_gaps.append(gap)

            elif gap.resolution == GapResolutionType.COMPANY_TW:
                company_tw_gaps.append(gap)

            elif gap.resolution == GapResolutionType.COMPANY_GLOBAL:
                company_global_gaps.append(gap)

        # Add LLM knowledge items to context
        if llm_knowledge_items:
            current_context.extend(llm_knowledge_items)
            # Update source_map with new items
            start_idx = max(self.source_map.keys(), default=0) + 1
            for i, item in enumerate(llm_knowledge_items):
                self.source_map[start_idx + i] = item
            self.logger.info(f"Added {len(llm_knowledge_items)} LLM knowledge items to context")

        # Execute stock API calls
        if stock_tw_gaps:
            await self._execute_stock_tw_searches(stock_tw_gaps, current_context, tracer, query_id)

        if stock_global_gaps:
            await self._execute_stock_global_searches(stock_global_gaps, current_context, tracer, query_id)

        # Execute weather API calls
        if weather_tw_gaps:
            await self._execute_weather_tw_searches(weather_tw_gaps, current_context, tracer, query_id)

        if weather_global_gaps:
            await self._execute_weather_global_searches(weather_global_gaps, current_context, tracer, query_id)

        # Execute company API calls
        if company_tw_gaps:
            await self._execute_company_tw_searches(company_tw_gaps, current_context, tracer, query_id)

        if company_global_gaps:
            await self._execute_company_global_searches(company_global_gaps, current_context, tracer, query_id)

        # Execute Wikipedia searches
        if wikipedia_gaps:
            await self._execute_wikipedia_searches(wikipedia_gaps, current_context, tracer, query_id)

        # Execute web searches in parallel if enabled
        if web_search_gaps and enable_web_search:
            await self._execute_web_searches(web_search_gaps, mode, current_context, tracer, query_id)

    async def _execute_web_searches(
        self,
        gaps: List[Any],
        mode: str,
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """
        Execute web searches for gap resolutions using multiple Tier 6 sources.

        Args:
            gaps: List of GapResolution objects requiring web search
            mode: Research mode
            current_context: Current context list (modified in place)
            tracer: Optional console tracer
            query_id: Query ID for analytics logging
        """
        import asyncio

        # Get configuration
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        web_config = tier_6_config.get("web_search", {})
        wiki_config = tier_6_config.get("wikipedia", {})
        max_results = web_config.get("max_results", 5)
        enrichment_strategy = tier_6_config.get("enrichment_strategy", "parallel")

        self.logger.info(f"Executing {len(gaps)} web searches (strategy={enrichment_strategy})")

        # Send progress
        await self._send_progress({
            "message_type": "intermediate_result",
            "stage": "web_search_started",
            "queries": [g.search_query for g in gaps]
        })

        try:
            # Initialize Google Search client
            from retrieval_providers.google_search_client import GoogleSearchClient
            google_client = GoogleSearchClient()

            # Initialize Wikipedia client if enabled
            wiki_client = None
            if wiki_config.get("enabled", False):
                try:
                    from retrieval_providers.wikipedia_client import WikipediaClient
                    wiki_client = WikipediaClient()
                    if not wiki_client.is_available():
                        wiki_client = None
                        self.logger.debug("Wikipedia client disabled or library not installed")
                except ImportError:
                    self.logger.debug("Wikipedia library not installed")

            # Build search tasks
            search_tasks = []
            for gap in gaps:
                if gap.search_query:
                    # Google Search task
                    google_task = google_client.search_all_sites(
                        query=gap.search_query,
                        num_results=max_results,
                        query_id=query_id
                    )
                    search_tasks.append(("google", gap, google_task))

                    # Wikipedia task (parallel strategy)
                    if wiki_client and enrichment_strategy == "parallel":
                        wiki_task = wiki_client.search(
                            query=gap.search_query,
                            query_id=query_id
                        )
                        search_tasks.append(("wikipedia", gap, wiki_task))

            # Gather results
            all_results = []
            google_count = 0
            wiki_count = 0

            for source_type, gap, task in search_tasks:
                try:
                    results = await task

                    if source_type == "google":
                        # Process Google results (tuple format)
                        for result in results:
                            if isinstance(result, (list, tuple)) and len(result) >= 4:
                                schema_json = result[1] if len(result) > 1 else "{}"
                                try:
                                    schema_obj = json.loads(schema_json) if isinstance(schema_json, str) else schema_json
                                except json.JSONDecodeError:
                                    schema_obj = {}

                                web_doc = {
                                    "url": result[0],
                                    "title": result[2] if len(result) > 2 else "Web Result",
                                    "site": result[3] if len(result) > 3 else "Web",
                                    "description": f"[Tier 6 | web_reference] {schema_obj.get('description', '')}",
                                    "_reasoning_metadata": {
                                        "tier": 6,
                                        "type": "web_reference",
                                        "original_source": result[3] if len(result) > 3 else "Web",
                                        "gap_query": gap.search_query
                                    }
                                }
                                all_results.append(web_doc)
                                google_count += 1

                    elif source_type == "wikipedia":
                        # Process Wikipedia results (dict format)
                        for result in results:
                            if isinstance(result, dict):
                                wiki_doc = {
                                    "url": result.get("link", ""),
                                    "title": result.get("title", "Wikipedia"),
                                    "site": "Wikipedia",
                                    "description": f"[Tier 6 | encyclopedia] {result.get('snippet', '')}",
                                    "_reasoning_metadata": {
                                        "tier": 6,
                                        "type": "encyclopedia",
                                        "original_source": "Wikipedia",
                                        "gap_query": gap.search_query
                                    }
                                }
                                all_results.append(wiki_doc)
                                wiki_count += 1

                    self.logger.info(f"{source_type} search for '{gap.search_query}': {len(results)} results")

                except Exception as e:
                    self.logger.error(f"{source_type} search failed for '{gap.search_query}': {e}")

            # Sequential fallback: Try Wikipedia if Google returned few results
            if wiki_client and enrichment_strategy == "sequential" and google_count < 3:
                self.logger.info("Sequential fallback: trying Wikipedia for additional context")
                for gap in gaps:
                    if gap.search_query:
                        try:
                            wiki_results = await wiki_client.search(
                                query=gap.search_query,
                                query_id=query_id
                            )
                            for result in wiki_results:
                                if isinstance(result, dict):
                                    wiki_doc = {
                                        "url": result.get("link", ""),
                                        "title": result.get("title", "Wikipedia"),
                                        "site": "Wikipedia",
                                        "description": f"[Tier 6 | encyclopedia] {result.get('snippet', '')}",
                                        "_reasoning_metadata": {
                                            "tier": 6,
                                            "type": "encyclopedia",
                                            "original_source": "Wikipedia",
                                            "gap_query": gap.search_query
                                        }
                                    }
                                    all_results.append(wiki_doc)
                                    wiki_count += 1
                        except Exception as e:
                            self.logger.error(f"Wikipedia fallback failed: {e}")

            # Add to context
            if all_results:
                current_context.extend(all_results)
                # Update source_map
                start_idx = max(self.source_map.keys(), default=0) + 1
                for i, item in enumerate(all_results):
                    self.source_map[start_idx + i] = item
                self.logger.info(f"Added {len(all_results)} Tier 6 results (Google: {google_count}, Wikipedia: {wiki_count})")

                # Tracing
                if tracer:
                    tracer.context_update(
                        "WEB_SEARCH",
                        {
                            "queries_executed": [g.search_query for g in gaps],
                            "results_found": len(all_results),
                            "google_count": google_count,
                            "wikipedia_count": wiki_count
                        }
                    )

        except ImportError:
            self.logger.warning("Google Search client not available, skipping web search")
        except Exception as e:
            self.logger.error(f"Web search execution failed: {e}")

    async def _execute_api_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        client_module: str,
        client_class: str,
        tracer_label: str,
        log_name: str,
        extract_param,
        search_fn,
        tracer_detail_fn,
        tracer: Any = None,
        query_id: str = None,
        transform_fn=None,
    ) -> None:
        """
        Generic API search executor for gap resolutions.

        Shared logic for all Tier 6 API searches: import client, check availability,
        loop gaps to collect results, update source_map and context, trace.

        Args:
            gaps: List of GapResolution objects
            current_context: Current context list (modified in place)
            client_module: Module path for dynamic import (e.g., "retrieval_providers.twse_client")
            client_class: Class name to instantiate (e.g., "TwseClient")
            tracer_label: Label for tracer.context_update (e.g., "STOCK_TW")
            log_name: Human-readable name for log messages (e.g., "TWSE")
            extract_param: Callable(gap) -> param value or None
            search_fn: Callable(client, param, query_id) -> awaitable results
            tracer_detail_fn: Callable(gaps) -> dict of tracer detail fields
            tracer: Optional console tracer
            query_id: Query ID for analytics logging
            transform_fn: Optional callable(results, gap) -> transformed results list.
                          If None, results are used as-is.
        """
        try:
            import importlib
            mod = importlib.import_module(client_module)
            cls = getattr(mod, client_class)
            client = cls()

            if not client.is_available():
                self.logger.debug(f"{log_name} client not enabled or not available")
                return

            all_results = []
            for gap in gaps:
                param = extract_param(gap)
                if param:
                    try:
                        results = await search_fn(client, param, query_id)
                        if transform_fn:
                            all_results.extend(transform_fn(results, gap))
                        else:
                            all_results.extend(results)
                        self.logger.info(f"{log_name} search for '{param}': {len(results)} results")
                    except Exception as e:
                        self.logger.error(f"{log_name} search failed for '{param}': {e}")

            # Add to context and update source_map
            if all_results:
                current_context.extend(all_results)
                start_idx = max(self.source_map.keys(), default=0) + 1
                for i, item in enumerate(all_results):
                    self.source_map[start_idx + i] = item
                self.logger.info(f"Added {len(all_results)} {log_name} results")

                if tracer:
                    details = tracer_detail_fn(gaps)
                    details["results_found"] = len(all_results)
                    tracer.context_update(tracer_label, details)

        except ImportError:
            self.logger.debug(f"{log_name} client not available")
        except Exception as e:
            self.logger.error(f"{log_name} search failed: {e}")

    async def _execute_stock_tw_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute Taiwan stock API calls for gap resolutions."""
        import re

        def extract_param(gap):
            symbol = gap.api_params.get("symbol") if gap.api_params else None
            if not symbol and gap.search_query:
                match = re.search(r'\b(\d{4,5})\b', gap.search_query)
                if match:
                    symbol = match.group(1)
            return symbol

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.twse_client",
            client_class="TwseClient",
            tracer_label="STOCK_TW",
            log_name="TWSE",
            extract_param=extract_param,
            search_fn=lambda client, param, qid: client.search(param, query_id=qid),
            tracer_detail_fn=lambda gs: {"symbols_queried": [g.api_params.get("symbol") if g.api_params else None for g in gs]},
            tracer=tracer,
            query_id=query_id,
        )

    async def _execute_stock_global_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute global stock API calls via yfinance for gap resolutions."""
        import re

        def extract_param(gap):
            symbol = gap.api_params.get("symbol") if gap.api_params else None
            if not symbol and gap.search_query:
                match = re.search(r'\b([A-Z]{1,5})\b', gap.search_query.upper())
                if match:
                    symbol = match.group(1)
            return symbol

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.yfinance_client",
            client_class="YfinanceClient",
            tracer_label="STOCK_GLOBAL",
            log_name="yFinance",
            extract_param=extract_param,
            search_fn=lambda client, param, qid: client.search(param, query_id=qid),
            tracer_detail_fn=lambda gs: {"symbols_queried": [g.api_params.get("symbol") if g.api_params else None for g in gs]},
            tracer=tracer,
            query_id=query_id,
        )

    async def _execute_wikipedia_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute Wikipedia API calls for gap resolutions."""

        def extract_param(gap):
            return gap.search_query or (gap.api_params.get("query") if gap.api_params else None)

        def transform_fn(results, gap):
            """Convert Wikipedia results to standard Tier 6 document format."""
            docs = []
            for result in results:
                if isinstance(result, dict):
                    docs.append({
                        "url": result.get("link", ""),
                        "title": result.get("title", "Wikipedia"),
                        "site": "Wikipedia",
                        "description": f"[Tier 6 | encyclopedia] {result.get('snippet', '')}",
                        "_reasoning_metadata": {
                            "tier": 6,
                            "type": "encyclopedia",
                            "original_source": "Wikipedia",
                            "gap_query": extract_param(gap)
                        }
                    })
            return docs

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.wikipedia_client",
            client_class="WikipediaClient",
            tracer_label="WIKIPEDIA",
            log_name="Wikipedia",
            extract_param=extract_param,
            search_fn=lambda client, param, qid: client.search(param, query_id=qid),
            tracer_detail_fn=lambda gs: {"queries_executed": [g.search_query for g in gs if g.search_query]},
            tracer=tracer,
            query_id=query_id,
            transform_fn=transform_fn,
        )

    async def _execute_weather_tw_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute Taiwan weather API calls for gap resolutions."""

        def extract_param(gap):
            location = gap.api_params.get("location") if gap.api_params else None
            if not location and gap.search_query:
                location = gap.search_query
            return location

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.cwb_weather_client",
            client_class="CwbWeatherClient",
            tracer_label="WEATHER_TW",
            log_name="CWB Weather",
            extract_param=extract_param,
            search_fn=lambda client, param, qid: client.search(param, query_id=qid),
            tracer_detail_fn=lambda gs: {"locations_queried": [g.api_params.get("location") if g.api_params else g.search_query for g in gs]},
            tracer=tracer,
            query_id=query_id,
        )

    async def _execute_weather_global_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute global weather API calls via OpenWeatherMap for gap resolutions."""

        def extract_param(gap):
            city = gap.api_params.get("city") if gap.api_params else None
            if not city and gap.search_query:
                city = gap.search_query
            return city

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.global_weather_client",
            client_class="GlobalWeatherClient",
            tracer_label="WEATHER_GLOBAL",
            log_name="Global Weather",
            extract_param=extract_param,
            search_fn=lambda client, param, qid: client.search(param, query_id=qid),
            tracer_detail_fn=lambda gs: {"cities_queried": [g.api_params.get("city") if g.api_params else g.search_query for g in gs]},
            tracer=tracer,
            query_id=query_id,
        )

    async def _execute_company_tw_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute Taiwan company registration API calls for gap resolutions."""

        def extract_param(gap):
            query = None
            if gap.api_params:
                query = gap.api_params.get("name") or gap.api_params.get("ubn")
            if not query and gap.search_query:
                query = gap.search_query
            return query

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.tw_company_client",
            client_class="TwCompanyClient",
            tracer_label="COMPANY_TW",
            log_name="TW Company",
            extract_param=extract_param,
            search_fn=lambda client, param, qid: client.search(param, query_id=qid),
            tracer_detail_fn=lambda gs: {"queries_executed": [g.api_params.get("name") if g.api_params else g.search_query for g in gs]},
            tracer=tracer,
            query_id=query_id,
        )

    async def _execute_company_global_searches(
        self,
        gaps: List[Any],
        current_context: List[Dict[str, Any]],
        tracer: Any = None,
        query_id: str = None
    ) -> None:
        """Execute global company/entity API calls via Wikidata for gap resolutions."""

        # entity_type varies per gap (gap.api_params.get("type", "company")), so we
        # carry it alongside the name inside the extracted param tuple. _execute_api_searches
        # calls extract_param then search_fn once per gap in order, so each gap's entity_type
        # travels with its own name — no cross-gap state, no name-keyed collision. Returns
        # None when there is no name so the executor's `if param` skip is preserved.
        def extract_param(gap):
            name = gap.api_params.get("name") if gap.api_params else None
            if not name and gap.search_query:
                name = gap.search_query
            if not name:
                return None
            entity_type = gap.api_params.get("type", "company") if gap.api_params else "company"
            return (name, entity_type)

        async def search_with_entity_type(client, param, qid):
            name, entity_type = param
            return await client.search(name, entity_type=entity_type, query_id=qid)

        await self._execute_api_searches(
            gaps=gaps,
            current_context=current_context,
            client_module="retrieval_providers.wikidata_client",
            client_class="WikidataClient",
            tracer_label="COMPANY_GLOBAL",
            log_name="Wikidata",
            extract_param=extract_param,
            search_fn=search_with_entity_type,
            tracer_detail_fn=lambda gs: {"queries_executed": [g.api_params.get("name") if g.api_params else g.search_query for g in gs]},
            tracer=tracer,
            query_id=query_id,
        )
