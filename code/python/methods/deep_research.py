# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Deep Research Handler - Full Handler for Reasoning Module

This handler extends NLWebHandler to reuse all infrastructure (retrieval, temporal detection, etc.)
while adding multi-agent reasoning capabilities.

Future implementation will include:
- DeepResearchOrchestrator
- Analyst, Critic, Writer Agents
- Actor-Critic Loop
- Multi-tier source filtering
"""

import asyncio
import json
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from core.baseHandler import NLWebHandler
from misc.logger.logging_config_helper import get_configured_logger
from reasoning.prompts.clarification import build_clarification_prompt

logger = get_configured_logger("deep_research_handler")


class DeepResearchHandler(NLWebHandler):
    """
    Full handler for deep research mode.

    Inherits retrieval/ranking infrastructure from NLWebHandler.
    Adds multi-agent reasoning with mode detection (strict/discovery/monitor).
    """

    def __init__(self, query_params, http_handler):
        """
        Initialize Deep Research Handler.

        Args:
            query_params: Query parameters from API request
            http_handler: HTTP streaming wrapper
        """
        # Call parent constructor - sets up all infrastructure
        super().__init__(query_params, http_handler)

        self.research_mode = None  # Will be set in prepare()

        # Phase KG: Extract enable_kg parameter from query_params, fallback to config
        from core.config import CONFIG
        config_enable_kg = CONFIG.reasoning_params.get("features", {}).get("knowledge_graph_generation", False)
        enable_kg_param = query_params.get('enable_kg', None)
        if enable_kg_param is not None:
            self.enable_kg = enable_kg_param in [True, 'true', 'True', '1']
        else:
            self.enable_kg = config_enable_kg

        # Stage 5: Extract enable_web_search parameter (default: False)
        enable_web_search_param = query_params.get('enable_web_search', 'false')
        self.enable_web_search = enable_web_search_param in [True, 'true', 'True', '1']

        # Task 6: Non-blocking research support
        self._research_task: Optional[asyncio.Task] = None
        self._soft_interrupt_event = asyncio.Event()

        logger.info("DeepResearchHandler initialized")
        logger.info(f"  Query: {self.query}")
        logger.info(f"  Site: {self.site}")
        logger.info(f"  Enable KG: {self.enable_kg}")
        logger.info(f"  Enable Web Search: {self.enable_web_search}")

        # Future: Initialize Orchestrator
        # self.orchestrator = DeepResearchOrchestrator(...)

    async def runQuery(self):
        """
        Main entry point for query execution.
        Follows standard handler pattern.
        """
        logger.info(f"[DEEP RESEARCH] Starting query execution for: {self.query}")

        try:
            # Call parent prepare() - gets retrieval, temporal detection, etc.
            await self.prepare()

            if self.query_done:
                logger.info("[DEEP RESEARCH] Query done prematurely")
                return self.return_value

            # Execute deep research
            await self.execute_deep_research()

            self.return_value["conversation_id"] = self.conversation_id
            logger.info(f"[DEEP RESEARCH] Query execution completed")

            return self.return_value

        except Exception as e:
            logger.error(f"[DEEP RESEARCH] Error in runQuery: {e}", exc_info=True)
            raise

    async def prepare(self):
        """
        Run pre-checks and retrieval.
        Extends parent prepare() to add mode detection and clarification check.
        """
        # Call parent prepare() - handles:
        # - Decontextualization
        # - Query rewrite
        # - Tool selection (will be skipped due to generate_mode)
        # - Memory retrieval
        # - Temporal detection
        # - Vector search with date filtering
        await super().prepare()

        # Phase 4: Check if clarification is needed
        # This happens after temporal detection, so we can check the results
        needs_clarification = await self._check_clarification_needed()
        if needs_clarification:
            # Clarification request sent, early return
            # Set return_value to indicate clarification is pending
            self.return_value.update({
                'answer': '',  # Empty answer - clarification needed
                'status': 'clarification_pending',
                'message': 'Waiting for user clarification'
            })
            logger.info("[DEEP RESEARCH] Clarification required, waiting for user input")
            return

        # Additional: Detect research mode
        self.research_mode = await self._detect_research_mode()
        logger.info(f"[DEEP RESEARCH] Mode detected: {self.research_mode.upper()}")

    async def _detect_research_mode(self) -> str:
        """
        Get research mode from frontend request.

        Returns:
            'strict' | 'discovery' | 'monitor'
        """
        # Use frontend-specified mode
        if 'research_mode' in self.query_params:
            user_mode = self.query_params['research_mode']
            if user_mode in ['strict', 'discovery', 'monitor']:
                logger.info(f"[DEEP RESEARCH] Using frontend mode: {user_mode}")
                return user_mode

        # Default if not specified
        logger.info("[DEEP RESEARCH] No mode specified, using default: discovery")
        return 'discovery'

    async def execute_deep_research(self):
        """
        Execute deep research using reasoning orchestrator.
        If reasoning module disabled, falls back to mock implementation.

        When nonblocking_research=true AND composable_pipeline=true, the research
        runs as a named asyncio.Task that can be cancelled via _research_task.cancel().
        The HTTP connection stays open (we still await the task) but the task can be
        interrupted by soft_interrupt_event or client disconnect.
        """
        from core.config import CONFIG

        # Access pre-filtered items from parent's prepare()
        items = self.final_retrieved_items

        logger.info(f"[DEEP RESEARCH] Executing {self.research_mode} mode")
        logger.info(f"[DEEP RESEARCH] Retrieved items: {len(items)}")

        # Get temporal context from parent
        temporal_context = self._get_temporal_context()
        logger.info(f"[DEEP RESEARCH] Temporal context: {temporal_context}")

        # Feature flag check
        if not CONFIG.reasoning_params.get("enabled", False):
            logger.info("[DEEP RESEARCH] Reasoning module disabled, using mock implementation")
            results = self._generate_mock_results(items, temporal_context)
        else:
            # Import and run orchestrator
            logger.info("[DEEP RESEARCH] Reasoning module enabled, using orchestrator")
            from reasoning.orchestrator import DeepResearchOrchestrator

            orchestrator = DeepResearchOrchestrator(handler=self)

            # Check if non-blocking mode is enabled
            # Requires BOTH composable_pipeline=true AND nonblocking_research=true
            enable_composable = CONFIG.reasoning_params.get("features", {}).get(
                "composable_pipeline", False
            )
            enable_nonblocking = CONFIG.reasoning_params.get("features", {}).get(
                "nonblocking_research", False
            )

            if enable_composable and enable_nonblocking:
                # Non-blocking: wrap in named task for cancellation support.
                # The HTTP connection stays open (we await the task), but
                # the task can be .cancel()'d from on_disconnect or soft interrupt.
                logger.info("[DEEP RESEARCH] Non-blocking mode: creating research task")
                self._research_task = asyncio.create_task(
                    orchestrator.run_research(
                        query=self.query,
                        mode=self.research_mode,
                        items=items,
                        temporal_context=temporal_context,
                        enable_kg=self.enable_kg,
                        enable_web_search=self.enable_web_search,
                    ),
                    name=f"research_{self.conversation_id}"
                )
                # W2 fix: add done callback to catch exceptions that would
                # otherwise be silently swallowed if await is interrupted
                self._research_task.add_done_callback(self._on_research_complete)

                try:
                    results = await self._research_task
                except asyncio.CancelledError:
                    logger.info("[DEEP RESEARCH] Research task cancelled")
                    results = []
                finally:
                    self._research_task = None
            else:
                # Legacy blocking path (default)
                results = await orchestrator.run_research(
                    query=self.query,
                    mode=self.research_mode,
                    items=items,
                    temporal_context=temporal_context,
                    enable_kg=self.enable_kg,
                    enable_web_search=self.enable_web_search,
                )

        # Send results using parent's message sender
        from core.schemas import create_assistant_result
        create_assistant_result(results, handler=self, send=True)

        logger.info(f"[DEEP RESEARCH] Sent {len(results)} results to frontend")

        # Generate final report for api.py
        final_report = self._generate_final_report(results, temporal_context)

        # Extract source URLs from schema_object (set by orchestrator)
        source_urls = []
        for item in results:
            schema_obj = item.get('schema_object', {})
            if 'sources_used' in schema_obj:
                source_urls.extend(schema_obj['sources_used'])

        # Update return_value with structured response
        self.return_value.update({
            'answer': final_report,
            'confidence_level': self._calculate_confidence(results),
            'methodology_note': f'Deep Research ({self.research_mode} mode)',
            'sources_used': source_urls,  # Use actual source URLs, not report URL
            'items': results  # Include full results with schema_object for Phase 4
        })

        logger.info(f"[DEEP RESEARCH] Updated return_value with final report")

    def _on_research_complete(self, task: asyncio.Task):
        """
        Callback when background research task completes or fails.

        W2 fix: Without this callback, if the handler itself raises before
        `await task`, the task's exception would be silently swallowed by asyncio
        ("Task exception was never retrieved"). This ensures exceptions are always
        logged and error messages are pushed to the user via SSE.
        """
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"[DEEP RESEARCH] Background research task failed: {exc}",
                    exc_info=exc
                )
                # Push error to user via SSE (best-effort, non-blocking)
                asyncio.create_task(self._send_research_error(exc))
        except asyncio.CancelledError:
            # Normal cancellation (client disconnect or soft interrupt) -- not an error
            logger.info(f"[DEEP RESEARCH] Research task cancelled: {task.get_name()}")
        except asyncio.InvalidStateError:
            # Task not done yet -- shouldn't happen in done callback, but be safe
            pass

    async def _send_research_error(self, exc: Exception):
        """Push a research error message to the user via SSE (best-effort)."""
        try:
            if hasattr(self, 'message_sender') and self.message_sender:
                error_message = {
                    "message_type": "research_error",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
                await self.message_sender.send_message(error_message)
                logger.info("[DEEP RESEARCH] Error message sent to frontend via SSE")
        except Exception as send_err:
            # SSE send itself failed -- connection probably dead, just log
            logger.warning(
                f"[DEEP RESEARCH] Failed to send error to frontend: {send_err}"
            )

    def _get_temporal_context(self) -> Dict[str, Any]:
        """
        Package temporal metadata for reasoning module.
        Reuses parent's temporal detection.

        Returns:
            Dictionary with temporal information including user_selected flag
            for BINDING constraint in Analyst prompt.
        """
        # Check if temporal parsing was done
        temporal_range = getattr(self, 'temporal_range', None)

        context = {
            'is_temporal_query': temporal_range.get('is_temporal', False) if temporal_range else False,
            'method': temporal_range.get('method') if temporal_range else 'none',
            'start_date': temporal_range.get('start_date') if temporal_range else None,
            'end_date': temporal_range.get('end_date') if temporal_range else None,
            'start': temporal_range.get('start_date') if temporal_range else None,  # Alias for orchestrator
            'end': temporal_range.get('end_date') if temporal_range else None,  # Alias for orchestrator
            'relative_days': temporal_range.get('relative_days') if temporal_range else None,
            'current_date': datetime.now().strftime("%Y-%m-%d"),
            # NEW: User-selected time range from clarification (BINDING constraint)
            'user_selected': temporal_range.get('user_selected', False) if temporal_range else False,
            'user_choice_label': temporal_range.get('user_choice_label', '') if temporal_range else ''
        }

        if context['user_selected']:
            logger.info(f"[DEEP RESEARCH] User-selected time constraint: {context['user_choice_label']} ({context['start_date']} to {context['end_date']})")

        return context

    def _generate_mock_results(self, items: list, temporal_context: Dict) -> list:
        """
        Generate mock results for testing.
        Will be replaced by Orchestrator output.

        Args:
            items: Retrieved and filtered items from parent handler
            temporal_context: Temporal metadata

        Returns:
            List of result items in standard format
        """
        mode_descriptions = {
            'strict': 'High-accuracy fact-checking with Tier 1/2 sources only',
            'discovery': 'Comprehensive exploration across multiple sources and perspectives',
            'monitor': 'Gap detection between official statements and public sentiment'
        }

        return [{
            "@type": "Item",
            "url": f"https://deep-research.internal/{self.research_mode}",
            "name": f"[MOCK] Deep Research Result - {self.research_mode.upper()} Mode",
            "site": "Deep Research Module",
            "siteUrl": "internal",
            "score": 95,
            "description": (
                f"This is a placeholder result from deep_research handler.\n\n"
                f"**Query:** {self.query}\n\n"
                f"**Mode:** {self.research_mode} - {mode_descriptions.get(self.research_mode, 'Unknown')}\n\n"
                f"**Items Retrieved:** {len(items)} articles\n\n"
                f"**Temporal Context:**\n"
                f"- Is Temporal: {temporal_context['is_temporal_query']}\n"
                f"- Method: {temporal_context['method']}\n"
                f"- Date Range: {temporal_context.get('start_date', 'N/A')} to {temporal_context.get('end_date', 'N/A')}\n\n"
                f"**Future:** This will be replaced by DeepResearchOrchestrator output with:\n"
                f"- Analyst Agent findings\n"
                f"- Critic Agent validation\n"
                f"- Writer Agent synthesis\n"
                f"- Actor-Critic loop iterations"
            ),
            "schema_object": {
                "@type": "Article",
                "headline": f"Deep Research: {self.research_mode} Mode",
                "description": "Mock implementation - infrastructure testing",
                "mode": self.research_mode,
                "temporal_detected": temporal_context['is_temporal_query'],
                "num_items_retrieved": len(items)
            }
        }]

    def _generate_final_report(self, results: list, temporal_context: Dict) -> str:
        """
        Generate a final markdown report from research results.

        Args:
            results: List of NLWeb Item dicts from research
            temporal_context: Temporal metadata

        Returns:
            Markdown-formatted final report
        """
        # Extract descriptions from results (which contain the actual content)
        descriptions = [item.get('description', '') for item in results]

        # Build final report
        report_parts = [
            f"# 深度研究報告：{self.query}",
            f"\n**分析來源數：** {len(results)}",
        ]

        # Add temporal context if applicable
        if temporal_context.get('is_temporal_query'):
            date_range = f"{temporal_context.get('start_date', 'N/A')} 至 {temporal_context.get('end_date', 'N/A')}"
            report_parts.append(f"\n**時間範圍：** {date_range}")

        report_parts.append("\n---\n")

        # Add research findings
        for desc in descriptions:
            report_parts.append(desc)
            report_parts.append("\n")

        return "\n".join(report_parts)

    def _calculate_confidence(self, results: list) -> str:
        """
        Calculate confidence level based on research results.

        Args:
            results: List of research result items

        Returns:
            Confidence level: 'High', 'Medium', or 'Low'
        """
        num_results = len(results)

        # Simple heuristic based on number of results
        if num_results >= 5:
            return 'High'
        elif num_results >= 2:
            return 'Medium'
        else:
            return 'Low'

    async def _check_clarification_needed(self) -> bool:
        """
        Check if query needs clarification before proceeding with research.

        Single LLM call to detect all ambiguities (time, scope, entity).
        Returns conversational clarification questions.

        Returns:
            True if clarification was sent (early return needed)
            False if no clarification needed (proceed normally)
        """
        # Check if clarification should be skipped (user already selected an option)
        if self.query_params.get('skip_clarification') == 'true':
            logger.info("[DEEP RESEARCH] Skipping clarification check (user already clarified)")
            return False

        # Single LLM call to detect all ambiguities
        questions = await self._detect_all_ambiguities()

        if questions:
            # Format as multi-dimensional parallel clarification
            clarification_data = {
                "query": self.query,
                "questions": questions,
                "instruction": "為了精準搜尋，請選擇以下條件",
                "submit_label": "開始搜尋"
            }

            # Send to frontend (render in conversation)
            await self._send_clarification_request(clarification_data)

            self.query_done = True
            return True

        return False

    async def _detect_all_ambiguities(self) -> list:
        """
        Single LLM call to detect all ambiguities (time, scope, entity).

        Uses extracted prompt builder for cleaner separation of concerns.

        Returns:
            List of question dicts, each with options. Empty list if no ambiguities.
        """
        from core.llm import ask_llm, LLMError

        # Get temporal context for rule-based time ambiguity check
        temporal_range = getattr(self, 'temporal_range', None)
        has_time_ambiguity = self._check_time_ambiguity_rules(temporal_range)

        # Build prompt using extracted prompt builder (P1.3)
        prompt = build_clarification_prompt(
            query=self.query,
            temporal_range=temporal_range,
            has_time_ambiguity=has_time_ambiguity,
        )

        response_structure = {
            "questions": [
                {
                    "clarification_type": "string - time | scope | entity",
                    "question": "string - 對話式問題",
                    "required": "boolean - 必須為 true",
                    "options": [
                        {
                            "label": "string - 選項文字",
                            "intent": "string - 系統內部標籤",
                            "query_modifier": "string - 用於組合查詢的修飾詞（空字串表示全面性選項）",
                            "is_comprehensive": "boolean - 可選，標記為全面性選項"
                        }
                    ]
                }
            ]
        }

        try:
            response = await ask_llm(
                prompt,
                response_structure,
                level="low",
                query_params=self.query_params,
                max_length=1536  # Increased for multiple questions
            )

            # LLMError sentinel（falsy dict）→ response.get 會回 [] 偽裝成「無歧義」，
            # 把 LLM 故障 silent 吞掉。明確分型：故障降級（仍 proceed without
            # clarification）但留 error 級訊息，不偽裝成正常結果。
            if isinstance(response, LLMError):
                logger.error(
                    f"[AMBIGUITY] Detection degraded ({response.error_kind}): "
                    f"LLM call failed, proceeding without clarification questions"
                )
                return []

            questions = response.get('questions', [])

            if questions:
                # Add question_id and option IDs
                for i, q in enumerate(questions, 1):
                    q['question_id'] = f"q{i}"
                    # Add option IDs (1a, 1b, 1c...)
                    for j, opt in enumerate(q.get('options', []), 1):
                        opt['id'] = f"{i}{chr(96+j)}"  # 1a, 1b, 1c...

                logger.info(f"[AMBIGUITY] Detected {len(questions)} ambiguities")
                return questions
            else:
                logger.info("[AMBIGUITY] No ambiguities detected")
                return []

        except Exception as e:
            logger.error(f"[AMBIGUITY] Detection failed: {e}", exc_info=True)
            return []

    async def execute_rerun(self, original_query_id: str, kg_edits_json: str):
        """Execute selective re-run with KG edits.

        Builds a modified query by appending KG edit instructions to the original query,
        then runs phases 2-4 of the composable pipeline using cached context from phase 1.

        Args:
            original_query_id: query_id from the original deep research run
            kg_edits_json: serialized JSON string of KG edits from frontend

        Raises:
            ValueError: if no cached state for original_query_id
            RuntimeError: if composable_pipeline feature flag is disabled
        """
        from core.config import CONFIG

        # Feature flag gate
        enable_composable = CONFIG.reasoning_params.get("features", {}).get(
            "composable_pipeline", False
        )
        if not enable_composable:
            raise RuntimeError("Selective re-run requires composable_pipeline=true")

        # Parse edit_summary for the query template
        try:
            kg_edits = json.loads(kg_edits_json) if isinstance(kg_edits_json, str) else kg_edits_json
            edit_summary = kg_edits.get('edit_summary', {})
        except (json.JSONDecodeError, AttributeError):
            edit_summary = {}

        # Build modified query using the template from kg-editing-spec.md
        summary_lines = []
        if edit_summary.get('nodes_added', 0):
            summary_lines.append(f"- 新增節點：{edit_summary['nodes_added']} 個")
        if edit_summary.get('nodes_deleted', 0):
            summary_lines.append(f"- 刪除節點：{edit_summary['nodes_deleted']} 個")
        if edit_summary.get('nodes_modified', 0):
            summary_lines.append(f"- 修改節點：{edit_summary['nodes_modified']} 個")
        if edit_summary.get('edges_added', 0):
            summary_lines.append(f"- 新增關係：{edit_summary['edges_added']} 個")
        if edit_summary.get('edges_deleted', 0):
            summary_lines.append(f"- 刪除關係：{edit_summary['edges_deleted']} 個")
        if edit_summary.get('edges_modified', 0):
            summary_lines.append(f"- 修改關係：{edit_summary['edges_modified']} 個")

        summary_text = "\n".join(summary_lines) if summary_lines else "- （變更摘要不可用）"

        modified_query = (
            f"{self.query}\n\n"
            f"【使用者知識圖譜修改】\n"
            f"使用者根據知識圖譜進行了以下修改，請以此為前提重新分析：\n"
            f"{summary_text}\n\n"
            f"【修改後的知識圖譜（JSON）】\n"
            f"{kg_edits_json}\n\n"
            f"請假設使用者的修改為正確前提，據此重新分析。"
            f"如果你的分析結果與使用者修改有衝突，"
            f"仍以使用者的修改為前提分析，但標註 ⚠️ 說明 evidence 與使用者判斷的差異。"
        )

        logger.info(f"[DEEP RESEARCH RERUN] Starting rerun for query_id={original_query_id}")

        from reasoning.orchestrator import DeepResearchOrchestrator
        orchestrator = DeepResearchOrchestrator(handler=self)

        results = await orchestrator.run_research_rerun(
            original_query_id=original_query_id,
            modified_query=modified_query,
        )

        # Send results via existing SSE mechanism (same path as execute_deep_research)
        from core.schemas import create_assistant_result
        create_assistant_result(results, handler=self, send=True)

        logger.info(f"[DEEP RESEARCH RERUN] Sent {len(results)} results to frontend")

        # Update return_value with structured response
        final_report = self._generate_final_report(results, self._get_temporal_context())
        source_urls = []
        for item in results:
            schema_obj = item.get('schema_object', {})
            if 'sources_used' in schema_obj:
                source_urls.extend(schema_obj['sources_used'])

        self.return_value.update({
            'answer': final_report,
            'confidence_level': self._calculate_confidence(results),
            'methodology_note': f'Deep Research Rerun ({self.research_mode or "discovery"} mode)',
            'sources_used': source_urls,
            'items': results,
            'is_rerun': True,
        })

    def _check_time_ambiguity_rules(self, temporal_range) -> bool:
        """
        Rule-based time ambiguity check (preserving existing logic).
        Returns True if time ambiguity detected.

        Args:
            temporal_range: Temporal parsing result from parent handler

        Returns:
            True if time clarification needed, False otherwise
        """
        # Check 1: Explicit time parsing issues
        if temporal_range is None:
            logger.info("[TIME RULES] Time parsing failed, needs clarification")
            return True
        elif temporal_range.get('confidence', 1.0) < 0.7:
            logger.info(f"[TIME RULES] Low confidence parsing ({temporal_range.get('confidence')}), needs clarification")
            return True

        # Check 2: Semantic temporal ambiguity
        elif not temporal_range.get('is_temporal', False):
            query_lower = self.query.lower()
            temporal_ambiguity_indicators = [
                # Political figures and their policies
                ('蔡英文', ['政策', '兩岸', '外交', '立場', '主張']),
                ('賴清德', ['政策', '兩岸', '外交', '立場', '主張']),
                ('馬英九', ['政策', '兩岸', '外交', '立場', '主張']),
                # Events that span time or evolve
                ('發展', None),
                ('趨勢', None),
                ('演變', None),
                ('變化', None),
            ]

            for entity, context_words in temporal_ambiguity_indicators:
                if entity in query_lower:
                    if context_words:
                        if any(word in query_lower for word in context_words):
                            logger.info(f"[TIME RULES] Semantic ambiguity: '{entity}' with context")
                            return True
                    else:
                        logger.info(f"[TIME RULES] Semantic ambiguity: '{entity}'")
                        return True

        return False

    async def _send_clarification_request(self, clarification_data: dict):
        """
        Send clarification request to frontend via SSE.

        Args:
            clarification_data: Clarification options from ClarificationAgent
        """
        try:
            # Use inherited message_sender from NLWebHandler
            if not hasattr(self, 'message_sender'):
                logger.error("[DEEP RESEARCH] message_sender not available")
                return

            message_data = {
                "message_type": "clarification_required",
                "clarification": clarification_data,
                "query": self.query
            }

            await self.message_sender.send_message(message_data)

            logger.info("[DEEP RESEARCH] Clarification request sent to frontend")

        except Exception as e:
            logger.error(f"[DEEP RESEARCH] Failed to send clarification request: {e}", exc_info=True)
