# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
This file contains the base class for all handlers.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

from core.retriever import search, search_with_expansion
import asyncio
import importlib
import os
import time
import uuid
from typing import Any, Dict, List
from core.schemas import Message
from datetime import datetime, timezone, timedelta
import json
import sentry_sdk
import core.query_analysis.decontextualize as decontextualize
import core.query_analysis.analyze_query as analyze_query
import core.query_analysis.memory as memory
import core.query_analysis.query_understanding as query_understanding
# Replaced by query_understanding: query_rewrite, time_range_extractor, author_intent_detector
import core.ranking as ranking
import core.query_analysis.required_info as required_info
import traceback
import core.query_analysis.relevance_detection as relevance_detection
import core.query_analysis.prompt_guardrails as prompt_guardrails
import core.post_ranking as post_ranking
import core.router as router
from core.state import NLWebHandlerState
from core.utils.utils import get_param, siteToItemType, log
from core.utils.message_senders import MessageSender
from misc.logger.logger import get_logger, LogLevel
from misc.logger.logging_config_helper import get_configured_logger
from core.config import CONFIG
from core.query_analysis.query_sanitizer import QuerySanitizer
logger = get_configured_logger("nlweb_handler")

# Analytics logging
from core.query_logger import get_query_logger

# Sites that don't support standard vector retrieval
# (moved here from the removed dead retrieval-shortcut module, 2026-07)
NO_STANDARD_RETRIEVAL_SITES = ["datacommons", "all", "conv_history", "CricketLens", "cricketlens", "cricketlens.com"]

def site_supports_standard_retrieval(site):
    """Check if a site supports standard vector database retrieval"""
    # If site is "all" and aggregation is disabled, treat it as supporting standard retrieval
    if site == "all" and not CONFIG.is_aggregation_enabled():
        logger.debug("Site is 'all' with aggregation disabled - treating as standard retrieval")
        return True
    return site not in NO_STANDARD_RETRIEVAL_SITES

API_VERSION = "0.1"


def _resolve_trusted_identity(query_params, http_handler):
    """Resolve (user_id, org_id) preferring the server-injected JWT identity.

    L2 single-source（拍板 1）：identity 一律優先採 middleware 放進
    request['user'] 的可信值，client 傳的 query_params['user_id'] 只在
    「沒有可信 request 身分」時 fallback（非 streaming / from_message 路徑）。

    優先序：
      1. http_handler.request['user'] 且 authenticated → 用其 id / org_id
         （覆蓋任何 client 偽造的 user_id / org_id）
      2. 否則 → get_param(query_params, ...)（維持既有行為）

    Returns (user_id, org_id)。org_id 允許為 None（合法無 org user）。
    """
    request = getattr(http_handler, "request", None)
    if request is not None:
        try:
            user = request.get("user")
        except AttributeError:
            user = None
        if user and user.get("authenticated"):
            # server 端可信身分：直接採用，覆蓋 client 值
            return user.get("id"), user.get("org_id")

    # Fallback：沒有可信 request 身分（wrapper=None / 無 request / 未認證）
    return (
        get_param(query_params, "user_id", str, None),
        get_param(query_params, "org_id", str, None),
    )


class NLWebHandler:

    def __init__(self, query_params: Dict[str, Any], http_handler):
        """
        Initialize NLWebHandler with query parameters.

        Args:
            query_params: Dictionary of query parameters from request
            http_handler: HTTP handler for sending responses
        """
        self.http_handler = http_handler
        self.query_params = query_params

        # Delegate initialization to focused methods
        self._init_core_params()
        self._init_query_context()
        self._init_conversation()
        self._init_state()
        self._init_synchronization()
        self._init_messaging()

    def _init_core_params(self) -> None:
        """Initialize core query parameters."""
        self.init_time = time.time()
        self.first_result_sent = False

        self.site = get_param(self.query_params, "site", str, "all")
        if self.site and isinstance(self.site, str) and "," in self.site:
            self.site = [s.strip() for s in self.site.split(",") if s.strip()]

        self.query = get_param(self.query_params, "query", str, "")

        # P1-2: Sanitize query (strip template vars and control chars).
        # Length rejection is handled upstream in api.py (HTTP 400 before SSE).
        # Here we only sanitize — not reject — and store changes for async logging.
        _sanitize_result = QuerySanitizer.sanitize(self.query)
        if _sanitize_result['sanitized']:
            self.query = _sanitize_result['cleaned_query']
        self._sanitize_changes: list = _sanitize_result['changes']  # logged in runQuery()

        self.prev_queries = get_param(self.query_params, "prev", list, [])
        self.last_answers = get_param(self.query_params, "last_ans", list, [])
        self.model = get_param(self.query_params, "model", str, "gpt-4.1-mini")

    def _init_query_context(self) -> None:
        """Initialize query-specific context."""
        self.decontextualized_query = get_param(self.query_params, "decontextualized_query", str, "")
        self.context_url = get_param(self.query_params, "context_url", str, "")
        self.context_description = get_param(self.query_params, "context_description", str, "")

        streaming = get_param(self.query_params, "streaming", str, "True")
        self.streaming = streaming not in ["False", "false", "0"]

        debug = get_param(self.query_params, "debug", str, "False")
        self.debug_mode = debug not in ["False", "false", "0", None]

        self.generate_mode = get_param(self.query_params, "generate_mode", str, "none")

        free_conversation = get_param(self.query_params, "free_conversation", str, "false")
        self.free_conversation = free_conversation not in ["False", "false", "0", None]

        include_private_sources = get_param(self.query_params, "include_private_sources", str, "false")
        self.include_private_sources = include_private_sources not in ["False", "false", "0", None]

        # L2 single-source（拍板 1）：identity 優先採 server 注入的可信身分
        # （request['user']），client 傳的 query_params['user_id'] 只在無可信
        # 身分時 fallback。堵住偽造 user_id/org_id 撈別人私有文件（P0）。
        self.user_id, self.org_id = _resolve_trusted_identity(
            self.query_params, self.http_handler
        )

        self.item_type = siteToItemType(self.site)
        self.required_item_type = get_param(self.query_params, "required_item_type", str, None)

    def _init_conversation(self) -> None:
        """Initialize conversation tracking."""
        self.conversation_id = get_param(self.query_params, "conversation_id", str, "")
        # Auto-generate conversation_id if not provided
        if not self.conversation_id:
            self.conversation_id = f"conv_{uuid.uuid4().hex[:12]}"
        self.session_id = get_param(self.query_params, "session_id", str, "")
        self.thread_id = get_param(self.query_params, "thread_id", str, "")
        self.parent_query_id = get_param(self.query_params, "parent_query_id", str, None)

    def _init_state(self) -> None:
        """Initialize state variables."""
        self.retrieved_items = []
        self.final_retrieved_items = []
        self.final_ranked_answers = []

        self.query_done = False
        self.query_is_irrelevant = False
        self.requires_decontextualization = False
        self.injection_verdict = None  # Set by PromptGuardrails.do()

        self.tool_routing_results = []
        self.state = NLWebHandlerState(self)

        self.sites_in_embeddings_sent = False

        self.return_value = {}
        self.versionNumberSent = False
        self.headersSent = False

    def _init_synchronization(self) -> None:
        """Initialize async synchronization primitives."""
        self.pre_checks_done_event = asyncio.Event()
        self.retrieval_done_event = asyncio.Event()
        self.connection_alive_event = asyncio.Event()
        self.connection_alive_event.set()  # Initially alive
        self._state_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()

    def _init_messaging(self) -> None:
        """Initialize messaging and response tracking."""
        self.messages: List['Message'] = []
        self.handler_message_id = f"msg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:9]}"
        self.message_counter = 0
        self.message_sender = MessageSender(self)

        initial_user_message = self.message_sender.create_initial_user_message()
        self.messages.append(initial_user_message)
    
    @classmethod
    def from_message(cls, message, http_handler):
        """
        Create NLWebHandler from a Message object.
        Extracts all necessary parameters from the message structure.
        
        Args:
            message: Message object with UserQuery content
            http_handler: HTTP handler for streaming responses
        
        Returns:
            NLWebHandler instance configured from the message
        """
        import json
        
        # Initialize query_params dict
        query_params = {}
        
        # Extract from message content (UserQuery object or dict)
        content = message.content
        if hasattr(content, 'query'):
            # UserQuery object
            query_params["query"] = [content.query]
            query_params["site"] = [content.site] if content.site else ["all"]
            query_params["generate_mode"] = [content.mode] if content.mode else ["list"]
            if content.prev_queries:
                query_params["prev"] = [json.dumps(content.prev_queries)]
        elif isinstance(content, dict):
            # Dict with query structure
            query_params["query"] = [content.get('query', '')]
            query_params["site"] = [content.get('site', 'all')]
            query_params["generate_mode"] = [content.get('mode', 'list')]
            if content.get('prev_queries'):
                query_params["prev"] = [json.dumps(content['prev_queries'])]
        else:
            # Plain string content (fallback)
            query_params["query"] = [str(content)]
            query_params["site"] = ["all"]
            query_params["generate_mode"] = ["list"]
        
        # Extract from message metadata
        if message.sender_info:
            query_params["user_id"] = [message.sender_info.get('id', '')]
        
        # Add conversation tracking
        if message.conversation_id:
            query_params["conversation_id"] = [message.conversation_id]
        
        # Add streaming flag (always true for WebSocket/chat)
        query_params["streaming"] = ["true"]
        
        # Extract any additional parameters from message metadata
        if hasattr(message, 'metadata') and message.metadata:
            # Pass through search_all_users if present
            if 'search_all_users' in message.metadata:
                query_params["search_all_users"] = [str(message.metadata['search_all_users']).lower()]
        
        # Create and return NLWebHandler instance
        return cls(query_params, http_handler)
        
    @property 
    def is_connection_alive(self):
        return self.connection_alive_event.is_set()
        
    @is_connection_alive.setter
    def is_connection_alive(self, value):
        if value:
            self.connection_alive_event.set()
        else:
            self.connection_alive_event.clear()

    async def send_message(self, message):
        """Send a message with appropriate metadata and routing."""
        await self.message_sender.send_message(message)


    async def runQuery(self):
        logger.info(f"Starting query execution for conversation_id: {self.conversation_id}")

        # Analytics: Generate unique query ID and log query start (preserve if pre-set by API layer)
        if not hasattr(self, 'query_id') or not self.query_id:
            self.query_id = f"query_{int(time.time() * 1000)}"
        query_logger = get_query_logger()
        query_start_time = time.time()

        try:
            _effective_user_id = self.user_id or "anonymous"
            if _effective_user_id == "anonymous":
                logger.warning("Anonymous user in B2B mode — should not happen")
            query_logger.log_query_start(
                query_id=self.query_id,
                user_id=_effective_user_id,
                query_text=self.query,
                site=str(self.site) if isinstance(self.site, list) else self.site,
                mode=self.generate_mode or "list",
                decontextualized_query=self.decontextualized_query,
                session_id=self.session_id,
                conversation_id=self.conversation_id,
                model=self.model,
                parent_query_id=self.parent_query_id,
                org_id=self.org_id,
                embedding_model=getattr(CONFIG, 'embedding_model', '') or 'qwen3-4b',
            )
            # Allow parent commit to propagate to avoid foreign key race conditions
            await asyncio.sleep(0.15)
        except Exception as e:
            logger.warning(f"Failed to log query start: {e}")

        # P1-2: Log sanitization changes to guardrail_events (async context)
        if getattr(self, '_sanitize_changes', None):
            try:
                from core.guardrail_logger import GuardrailLogger
                await GuardrailLogger.get_instance().log_event(
                    event_type='query_sanitized',
                    severity='info',
                    user_id=self.user_id,
                    client_ip=None,  # IP not available in handler; logged via api.py if needed
                    details={'changes': self._sanitize_changes},
                )
                logger.info(f"[Guardrail] Query sanitized: {self._sanitize_changes}")
            except Exception as _gl_err:
                logger.warning(f"GuardrailLogger failed in runQuery: {_gl_err}")

        try:
            # Send begin-nlweb-response message at the start
            await self.message_sender.send_begin_response()

            # Progress: Analyzing query
            await self.message_sender.send_progress("analyzing", "分析查詢中...", 5)

            await self.prepare()
            if (self.query_done):
                return self.return_value
            await self.route_query_based_on_tools()

            # Check if query is done
            if (self.query_done):
                return self.return_value

            # Cache results BEFORE PostRanking for generate mode reuse
            # Must cache before PostRanking because summarize mode exits inside PostRanking
            if self.generate_mode in ["none", "summarize"] and hasattr(self, 'final_ranked_answers') and self.final_ranked_answers:
                try:
                    from core.results_cache import get_results_cache
                    cache = get_results_cache()
                    # CORE-1: cache 按 trusted user_id 隔離。空 conversation_id 或
                    # 無 user_id 時 cache 層自動不 cache（弱 fallback key 不用可
                    # 碰撞的 query+site，避免跨 user 私有文件洩漏）。
                    cache.store(
                        self.conversation_id,
                        self.final_ranked_answers,
                        self.query,
                        user_id=self.user_id,
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache results: {e}")

            # Progress: Generating AI answer
            await self.message_sender.send_progress("generating", "生成 AI 回答中...", 70)

            await post_ranking.PostRanking(self).do()

            self.return_value["conversation_id"] = self.conversation_id
            self.return_value["query_id"] = self.query_id

            # Send end-nlweb-response message at the end
            if not getattr(self, 'skip_end_response', False):
                await self.message_sender.send_end_response()

            # Analytics: Log query completion (skip if api.py handles it, e.g. unified mode)
            if not getattr(self, 'skip_end_response', False):
                try:
                    query_end_time = time.time()
                    total_latency_ms = (query_end_time - query_start_time) * 1000

                    num_results = 0
                    if hasattr(self, 'final_ranked_answers') and self.final_ranked_answers:
                        num_results = len(self.final_ranked_answers)

                    query_logger.log_query_complete(
                        query_id=self.query_id,
                        latency_total_ms=total_latency_ms,
                        num_results_retrieved=getattr(self, 'num_retrieved', 0),
                        num_results_ranked=getattr(self, 'num_ranked', 0),
                        num_results_returned=num_results,
                        cost_usd=getattr(self, 'estimated_cost', 0),
                        error_occurred=False
                    )
                except Exception as e:
                    logger.warning(f"Failed to log query completion: {e}")

            # Return both return_value and messages (converted to dicts for backward compatibility)
            return self.return_value, [msg.to_dict() for msg in self.messages]
        except Exception as e:
            traceback.print_exc()

            # Analytics: Log query error (skip if api.py handles it, e.g. unified mode)
            if not getattr(self, 'skip_end_response', False):
                try:
                    query_end_time = time.time()
                    total_latency_ms = (query_end_time - query_start_time) * 1000
                    query_logger.log_query_complete(
                        query_id=self.query_id,
                        latency_total_ms=total_latency_ms,
                        error_occurred=True,
                        error_message=str(e)
                    )
                except Exception as log_err:
                    logger.warning(f"Failed to log query error: {log_err}")

            # Send end-nlweb-response even on error
            if not getattr(self, 'skip_end_response', False):
                await self.message_sender.send_end_response(error=True)

            raise
    
    async def prepare(self):
        tasks = []

        tasks.append(asyncio.create_task(self.decontextualizeQuery().do()))
        tasks.append(asyncio.create_task(query_understanding.QueryUnderstanding(self).do()))

        # Check if a specific tool is requested via the 'tool' parameter
        requested_tool = get_param(self.query_params, "tool", str, None)
        if requested_tool:
            # Skip tool selection and use the requested tool directly
            # Set tool_routing_results to use the specified tool
            self.tool_routing_results = [{
                "tool": type('Tool', (), {'name': requested_tool, 'handler_class': None})(),
                "score": 100,
                "result": {"score": 100, "justification": f"Tool {requested_tool} specified in request"}
            }]
        else:
            # Normal tool selection
            tasks.append(asyncio.create_task(router.ToolSelector(self).do()))

     #   tasks.append(asyncio.create_task(analyze_query.DetectItemType(self).do()))
     #   tasks.append(asyncio.create_task(analyze_query.DetectMultiItemTypeQuery(self).do()))
     #   tasks.append(asyncio.create_task(analyze_query.DetectQueryType(self).do()))
        tasks.append(asyncio.create_task(relevance_detection.RelevanceDetection(self).do()))
        tasks.append(asyncio.create_task(prompt_guardrails.PromptGuardrails(self).do()))
        tasks.append(asyncio.create_task(memory.Memory(self).do()))
     #   tasks.append(asyncio.create_task(required_info.RequiredInfo(self).do()))
        
        try:
            if CONFIG.should_raise_exceptions():
                # In testing/development mode, raise exceptions to fail tests properly
                await asyncio.gather(*tasks)
            else:
                # In production mode, catch exceptions to avoid crashing
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # Check for failed pre-retrieval tasks
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Pre-retrieval task failed: {result}")
                        sentry_sdk.capture_exception(result)
        except Exception as e:
            if CONFIG.should_raise_exceptions():
                raise  # Re-raise in testing/development mode
        finally:
            self.pre_checks_done_event.set()  # Signal completion regardless of errors
            self.state.set_pre_checks_done()

        # P2-1: Skip retrieval if query was blocked by guardrails
        if self.query_done:
            return

        # Wait for retrieval to be done
        if not self.retrieval_done_event.is_set():
            # Skip retrieval for sites without embeddings
            if not site_supports_standard_retrieval(self.site):
                self.final_retrieved_items = []
                self.retrieval_done_event.set()
            # Skip retrieval for free conversation mode - use conversation context only
            elif self.free_conversation:
                logger.info("[FREE_CONVERSATION] Skipping public vector search - using conversation context")
                logger.debug("[FREE_CONVERSATION] Skipping public vector search - using conversation context")

                # Note: Research report is now passed directly from frontend via query_params
                # in Free Conversation mode, handled by generate_answer.py

                # Check for private sources even in free conversation mode
                logger.info(f"[FREE_CONVERSATION] include_private_sources={self.include_private_sources}, user_id={self.user_id}")
                if self.include_private_sources and self.user_id:
                    try:
                        from core.user_data_retriever import search_user_documents, format_private_result_for_display

                        logger.info("[FREE_CONVERSATION] Searching user's private documents")
                        private_results = await search_user_documents(
                            query=self.decontextualized_query,
                            user_id=self.user_id,
                            top_k=10,
                            query_params=self.query_params,
                            org_id=self.org_id
                        )

                        # Format private results to match expected format
                        if private_results:
                            formatted_private = []
                            for result in private_results:
                                formatted = format_private_result_for_display(result)
                                import json
                                private_item = [
                                    formatted['url'],
                                    json.dumps({'text': formatted['text'], 'metadata': formatted.get('metadata', {})}),
                                    formatted['title'],
                                    formatted['site']
                                ]
                                formatted_private.append(private_item)

                            self.final_retrieved_items = formatted_private
                            logger.info(f"[FREE_CONVERSATION] Found {len(formatted_private)} private documents")
                        else:
                            self.final_retrieved_items = []
                    except Exception as e:
                        logger.exception(f"[FREE_CONVERSATION] Failed to retrieve private documents: {str(e)}")
                        self.final_retrieved_items = []
                else:
                    self.final_retrieved_items = []

                self.retrieval_done_event.set()
            else:
                # Progress: Searching database
                await self.message_sender.send_progress("searching", "搜尋資料庫中...", 15)

                # Get parsed time range (TimeRangeExtractor runs in parallel during prepare())
                temporal_range = getattr(self, 'temporal_range', None)

                if temporal_range and temporal_range.get('is_temporal'):
                    # Adjust retrieval volume based on time window
                    days = temporal_range.get('relative_days') or 365
                    if days <= 7:
                        num_to_retrieve = 100  # Recent queries need more candidates
                    elif days <= 30:
                        num_to_retrieve = 150
                    else:
                        num_to_retrieve = 200

                    logger.info(f"[TEMPORAL] Temporal query detected (method: {temporal_range.get('method')})")
                    logger.info(f"[TEMPORAL] Time range: {temporal_range.get('start_date')} to {temporal_range.get('end_date')} ({days} days)")
                    logger.info(f"[TEMPORAL] Retrieving {num_to_retrieve} items for date filtering")
                else:
                    logger.info(f"[TEMPORAL] Non-temporal query: '{self.query}' - retrieving 50 items")
                    num_to_retrieve = 50

                # Check if MMR is enabled and request vectors if needed
                include_vectors = CONFIG.mmr_params.get('enabled', True) and CONFIG.mmr_params.get('include_vectors', True)

                # Construct generic filters from temporal_range and author_search
                search_filters = []
                self.time_filter_relaxed = False
                self.author_search_no_results = False
                self.low_relevance_warning = False
                self.low_keyword_match_warning = False

                if temporal_range and temporal_range.get('is_temporal'):
                    start_date = temporal_range.get('start_date')
                    end_date = temporal_range.get('end_date')
                    if start_date:
                        search_filters.append(
                            {"field": "datePublished", "operator": "gte", "value": start_date}
                        )
                        if end_date:
                            search_filters.append(
                                {"field": "datePublished", "operator": "lte", "value": end_date}
                            )

                # Author filter from AuthorIntentDetector
                author_search = getattr(self, 'author_search', None)
                if author_search and author_search.get('is_author_search'):
                    author_name = author_search['author_name']
                    search_filters.append(
                        {"field": "author", "operator": "contains", "value": author_name}
                    )
                    logger.info(f"[AUTHOR] Added author filter: '{author_name}'")

                if search_filters:
                    logger.info(f"[FILTER] Constructed retriever filters: {search_filters}")
                else:
                    search_filters = None

                # Use expansion queries if QueryRewrite produced them
                expansion_queries = getattr(self, 'rewritten_queries', [])
                if expansion_queries:
                    logger.info(f"[EXPANSION] Using {len(expansion_queries)} expansion queries: {expansion_queries}")
                    items = await search_with_expansion(
                        self.decontextualized_query,
                        expansion_queries,
                        self.site,
                        num_results=num_to_retrieve,
                        num_per_expansion=20,
                        query_params=self.query_params,
                        handler=self,
                        include_vectors=include_vectors,
                        filters=search_filters
                    )
                else:
                    items = await search(
                        self.decontextualized_query,
                        self.site,
                        query_params=self.query_params,
                        handler=self,
                        num_results=num_to_retrieve,
                        include_vectors=include_vectors,
                        filters=search_filters
                    )

                # Query user's private files if requested
                if self.include_private_sources and self.user_id:
                    try:
                        from core.user_data_retriever import search_user_documents, format_private_result_for_display

                        # Search private documents
                        private_results = await search_user_documents(
                            query=self.decontextualized_query,
                            user_id=self.user_id,
                            top_k=10,  # Retrieve top 10 from private files
                            query_params=self.query_params,
                            org_id=self.org_id
                        )

                        # Format private results to match public results format
                        if private_results:
                            formatted_private = []
                            for result in private_results:
                                # Convert to tuple format [url, json_str, name, site]
                                formatted = format_private_result_for_display(result)
                                import json
                                private_item = [
                                    formatted['url'],
                                    json.dumps({'text': formatted['text'], 'metadata': formatted.get('metadata', {})}),
                                    formatted['title'],
                                    formatted['site']
                                ]
                                formatted_private.append(private_item)

                            # Prepend private results (higher priority)
                            items = formatted_private + items
                            logger.info(f"Added {len(formatted_private)} private document results to search")

                    except Exception as e:
                        logger.exception(f"Failed to retrieve private documents: {str(e)}")
                        # Continue with public results only

                # Date filtering is now done at the retriever/provider level via generic filters.
                # The provider sets self.time_filter_relaxed = True if no results matched the filter.
                if self.time_filter_relaxed:
                    logger.warning(f"[TEMPORAL] Time filter was relaxed — provider found no results matching the date range")
                    # Notify frontend to display a warning banner
                    try:
                        await self.message_sender.send_message({
                            "message_type": "time_filter_relaxed",
                            "content": "系統找不到完全符合日期需求的資料，已擴大搜尋範圍"
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send time_filter_relaxed message: {e}")

                # Low-relevance warning (Signal A): fallback safety net — provider judged
                # the overall result relevance low (highest vector_score below the warn band).
                # Does not block results; frontend renders a banner above the result list.
                if getattr(self, 'low_relevance_warning', False):
                    logger.warning("[WARN] low_relevance_warning — notifying frontend")
                    try:
                        await self.message_sender.send_message({
                            "message_type": "low_relevance_warning",
                            "content": "以下結果與您的搜尋可能關聯性較鬆，建議交叉參考其他來源"
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send low_relevance_warning message: {e}")

                # Low-keyword-match warning (Signal B): fewer than the minimum number of
                # pg_bigm keyword hits. Independent of Signal A — both can fire together.
                if getattr(self, 'low_keyword_match_warning', False):
                    logger.warning("[WARN] low_keyword_match_warning — notifying frontend")
                    try:
                        await self.message_sender.send_message({
                            "message_type": "low_keyword_match_warning",
                            "content": "以下結果與關鍵字的字面吻合度較低，建議留意是否切合您的需求"
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send low_keyword_match_warning message: {e}")

                # Author search returned no results — strict filter, don't show unrelated articles
                if getattr(self, 'author_search_no_results', False):
                    author_name = author_search.get('author_name', '') if author_search else ''
                    logger.warning(f"[AUTHOR] No articles found for author '{author_name}' in retrieved candidates")
                    try:
                        await self.message_sender.send_message({
                            "message_type": "author_search_no_results",
                            "content": f"在目前的資料庫中找不到作者「{author_name}」的文章。若確有此人，可能其文章尚未被收錄。"
                        })
                    except Exception as e:
                        logger.warning(f"Failed to send author_search_no_results message: {e}")

                # Empty-result honest notice (non-author): a silent empty page is the
                # worst UX — say explicitly that the corpus has nothing (CDE plan §E).
                await self._maybe_emit_empty_results_notice(items)

                self.final_retrieved_items = items

                # For author searches, sort results by date (most recent first)
                if author_search and author_search.get('is_author_search') and self.final_retrieved_items:
                    try:
                        def _extract_date(item):
                            """Extract datePublished for sorting. Returns '0000-00-00' if unparseable."""
                            try:
                                sj = item.get('schema_json', '{}') if isinstance(item, dict) else ''
                                schema = json.loads(sj) if sj else {}
                                d = schema.get('datePublished', '') or ''
                                return d.split('T')[0] if 'T' in d else d
                            except Exception:
                                return '0000-00-00'

                        self.final_retrieved_items.sort(key=_extract_date, reverse=True)
                        logger.info(f"[AUTHOR] Sorted {len(self.final_retrieved_items)} results by date (most recent first)")
                    except Exception as e:
                        logger.warning(f"[AUTHOR] Failed to sort by date: {e}")

                self.retrieval_done_event.set()

        logger.info("Preparation phase completed")

    async def _maybe_emit_empty_results_notice(self, items):
        """Honest empty-result notice: fires only when the final merged result list
        (public + private) is empty AND this is not an author search.

        Mutually exclusive with the other retrieval notices (CDE plan §E):
        Signals A/B never fire on an empty set (compute_* return False on []),
        and author-search empty results keep the more specific
        author_search_no_results copy emitted above — so at most one notice
        reaches the user. Graceful degradation, never silent: the emit itself
        logs on failure.
        """
        if len(items) == 0 and not getattr(self, 'author_search_no_results', False):
            logger.warning("[EMPTY] Retrieval returned 0 final items — notifying frontend")
            try:
                await self.message_sender.send_message({
                    "message_type": "empty_results",
                    "content": "在目前的資料範圍中沒有找到相關內容。這個主題可能尚未被收錄，或不在本系統的新聞涵蓋範圍內。"
                })
            except Exception as e:
                logger.warning(f"Failed to send empty_results message: {e}")

    def decontextualizeQuery(self):
        if (len(self.prev_queries) < 1):
            self.decontextualized_query = self.query
            return decontextualize.NoOpDecontextualizer(self)
        elif (self.decontextualized_query != ''):
            return decontextualize.NoOpDecontextualizer(self)
        else:
            # prev_queries is non-empty and no pre-existing decontextualized_query
            return decontextualize.PrevQueryDecontextualizer(self)
    
    async def get_ranked_answers(self):
        try:
            # Progress: Ranking results
            await self.message_sender.send_progress("ranking", "排序結果中...", 40)

            await ranking.Ranking(self, self.final_retrieved_items, ranking.Ranking.REGULAR_TRACK).do()
            return self.return_value
        except Exception as e:
            traceback.print_exc()
            raise

    async def route_query_based_on_tools(self):
        """Route the query based on tool selection results."""

        # Check if we have tool routing results
        if not hasattr(self, 'tool_routing_results') or not self.tool_routing_results:
            # No tool routing results, falling back to get_ranked_answers
            await self.get_ranked_answers()
            return

        top_tool = self.tool_routing_results[0]
        tool = top_tool['tool']
        tool_name = tool.name
        params = top_tool['result']

        if tool.handler_class:
            try:
                # For non-search tools, clear any previously populated items
                if tool_name != "search":
                    self.final_retrieved_items = []
                    self.retrieved_items = []

                # Dynamic import of handler module and class
                module_path, class_name = tool.handler_class.rsplit('.', 1)
                module = importlib.import_module(module_path)
                handler_class = getattr(module, class_name)

                handler_instance = handler_class(params, self)
                await handler_instance.do()

            except Exception as e:
                logger.error(f"ERROR executing {tool_name}: {e}")
                traceback.print_exc()
                # Fall back to search
                await self.get_ranked_answers()
        else:
            # Default behavior for tools without handlers (like search)
            await self.get_ranked_answers()

