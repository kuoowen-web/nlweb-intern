"""Core API routes for aiohttp server"""

from aiohttp import web
import asyncio
import logging
import json
import os
import time as time_mod
from typing import Dict, Any
from core.whoHandler import WhoHandler
from methods.generate_answer import GenerateAnswer
from webserver.aiohttp_streaming_wrapper import AioHttpStreamingWrapper
from core.retriever import get_vector_db_client
from core.utils.utils import get_param
from core.utils.message_senders import inject_user_id
from core.config import CONFIG
from webserver.middleware.ip_utils import get_client_ip as _get_client_ip
from core.query_analysis.query_sanitizer import MAX_QUERY_LENGTH
from webserver.middleware.concurrency_limiter import (
    ConcurrencyLimiter,
    SEARCH_SESSION_LIMIT,
    SEARCH_IP_LIMIT,
    DR_USER_LIMIT,
    DR_IP_LIMIT,
)

logger = logging.getLogger(__name__)

# Mock session state for Live Research mock mode (in-memory, dev only)
_mock_lr_sessions: dict = {}


def setup_api_routes(app: web.Application):
    """Setup core API routes"""
    # Query endpoints
    app.router.add_get('/ask', ask_handler)
    app.router.add_post('/ask', ask_handler)
    app.router.add_get('/api/deep_research', deep_research_handler)
    app.router.add_post('/api/deep_research', deep_research_handler)

    # Research rerun endpoint (KG editing selective re-run)
    app.router.add_post('/api/research/rerun', research_rerun_handler)

    # Live Research endpoints
    app.router.add_post('/api/live_research', live_research_start_handler)
    app.router.add_post('/api/live_research/continue', live_research_continue_handler)
    # Feedback endpoint
    app.router.add_post('/api/feedback', feedback_handler)

    # Info endpoints
    app.router.add_get('/who', who_handler)
    app.router.add_get('/sites', sites_handler)
    app.router.add_get('/sites_config', sites_config_handler)


async def ask_handler(request: web.Request) -> web.Response:
    """Handle /ask endpoint for generating answers"""
    
    # Get query parameters
    query_params = dict(request.query)
    
    # For POST requests, merge body parameters
    if request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                body_data = await request.json()
                query_params.update(body_data)
            elif request.content_type == 'application/x-www-form-urlencoded':
                body_data = await request.post()
                query_params.update(dict(body_data))
        except Exception as e:
            logger.warning(f"Failed to parse POST body: {e}")
    
    # Inject auth user info into query_params (overrides query param spoofing)
    user = request.get('user')
    if user and user.get('authenticated'):
        query_params['user_id'] = user['id']
        if user.get('org_id'):
            query_params['org_id'] = user['org_id']

    # P1-2: Query length pre-check (before SSE stream starts — must return HTTP 400 JSON)
    query = query_params.get('query', '')
    if len(query) > MAX_QUERY_LENGTH:
        client_ip = _get_client_ip(request)
        user = request.get('user')
        uid = user.get('id') if user and user.get('authenticated') else None
        try:
            from core.guardrail_logger import GuardrailLogger
            await GuardrailLogger.get_instance().log_event(
                event_type='query_rejected',
                severity='info',
                user_id=uid,
                client_ip=client_ip,
                details={'reason': 'query_too_long', 'length': len(query)},
            )
        except Exception as _log_err:
            logger.warning(f"GuardrailLogger failed in ask_handler: {_log_err}")
        return web.json_response(
            {'error': 'query_too_long', 'message': '查詢過長，請縮短至 500 字元以內'},
            status=400,
        )

    # P1-1b: General search concurrency check
    client_ip = _get_client_ip(request)
    user = request.get('user')
    uid = user.get('id') if user and user.get('authenticated') else None
    request_id = f"req_{int(time_mod.time() * 1000)}_{id(request)}"
    session_id = query_params.get('session_id') or uid or client_ip

    if uid:
        conc_key = f"search:{session_id}"
        conc_limit = SEARCH_SESSION_LIMIT
    else:
        conc_key = f"search_ip:{client_ip}"
        conc_limit = SEARCH_IP_LIMIT

    limiter = ConcurrencyLimiter.get_instance()
    if not limiter.try_acquire(conc_key, request_id, conc_limit):
        try:
            from core.guardrail_logger import GuardrailLogger
            await GuardrailLogger.get_instance().log_event(
                event_type='concurrency_limit',
                severity='warning',
                user_id=uid,
                client_ip=client_ip,
                details={'key': conc_key, 'limit': conc_limit},
            )
        except Exception as _log_err:
            logger.warning(f"GuardrailLogger failed (concurrency): {_log_err}")
        return web.json_response(
            {'error': 'rate_limited', 'message': '目前查詢量過大，請稍後再試', 'retry_after_seconds': 30},
            status=429,
        )

    # Check if SSE streaming is requested
    is_sse = request.get('is_sse', False)
    streaming = get_param(query_params, "streaming", str, "True")
    streaming = streaming not in ["False", "false", "0"]

    dr_key = None
    dr_request_id = None
    try:
        # P1-1b: DR concurrency check for /ask?generate_mode=deep_research
        generate_mode = query_params.get('generate_mode', 'none')
        if generate_mode == 'deep_research':
            # Kill switch
            if os.environ.get('GUARDRAIL_DR_ENABLED', 'true').lower() == 'false':
                return web.json_response(
                    {'error': 'dr_disabled', 'message': 'Deep Research 功能暫時關閉'},
                    status=503,
                )
            if uid:
                dr_key = f"dr_user:{uid}"
                dr_limit = DR_USER_LIMIT
            else:
                dr_key = f"dr_ip:{client_ip}"
                dr_limit = DR_IP_LIMIT
            dr_request_id = f"dr_{request_id}"
            if not limiter.try_acquire(dr_key, dr_request_id, dr_limit):
                try:
                    from core.guardrail_logger import GuardrailLogger
                    await GuardrailLogger.get_instance().log_event(
                        event_type='concurrency_limit',
                        severity='warning',
                        user_id=uid,
                        client_ip=client_ip,
                        details={'key': dr_key, 'limit': dr_limit, 'reason': 'dr_concurrency'},
                    )
                except Exception as _log_err:
                    logger.warning(f"GuardrailLogger failed (DR concurrency): {_log_err}")
                return web.json_response(
                    {'error': 'rate_limited', 'message': 'Deep Research 同時只能進行一個，請等待完成後再試', 'retry_after_seconds': 30},
                    status=429,
                )

        if is_sse or streaming:
            return await handle_streaming_ask(request, query_params)
        else:
            return await handle_regular_ask(request, query_params)
    finally:
        # Always release slots — even if request crashes
        limiter.release(conc_key, request_id)
        if dr_key and dr_request_id:
            limiter.release(dr_key, dr_request_id)


async def handle_streaming_ask(request: web.Request, query_params: Dict[str, Any]) -> web.StreamResponse:
    """Handle streaming (SSE) ask requests"""
    
    # Create SSE response
    response = web.StreamResponse(
        status=200,
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )
    
    await response.prepare(request)
    
    # Create aiohttp-compatible wrapper
    wrapper = AioHttpStreamingWrapper(request, response, query_params)
    await wrapper.prepare_response()
    
    try:
        # Determine which handler to use based on generate_mode
        generate_mode = query_params.get('generate_mode', 'none')

        if generate_mode == 'generate':
            handler = GenerateAnswer(query_params, wrapper)
            wrapper.set_on_disconnect(lambda: handler.connection_alive_event.clear())
            await handler.runQuery()
        elif generate_mode == 'deep_research':
            # Deep research mode with multi-agent reasoning
            from methods.deep_research import DeepResearchHandler
            handler = DeepResearchHandler(query_params, wrapper)

            def _on_dr_disconnect():
                handler.connection_alive_event.clear()
                # Task 6: Cancel background research task if running
                if hasattr(handler, '_research_task') and handler._research_task:
                    handler._research_task.cancel()

            wrapper.set_on_disconnect(_on_dr_disconnect)
            await handler.runQuery()
        elif generate_mode == 'unified':
            # Unified mode: single SSE stream for articles + summary + AI answer
            unified_start_time = time_mod.time()
            unified_error = False

            from core.baseHandler import NLWebHandler
            handler = NLWebHandler(query_params, wrapper)
            wrapper.set_on_disconnect(lambda: handler.connection_alive_event.clear())
            handler.skip_end_response = True  # api.py controls end timing

            try:
                await handler.runQuery()  # retrieval + ranking + PostRanking

                await asyncio.sleep(0)  # flush pending create_tasks

                # Check connection before synthesis
                if not handler.connection_alive_event.is_set():
                    logger.info("Client disconnected before synthesis, skipping")
                else:
                    # Inject conversation_id into query_params for GenerateAnswer (Issue #2)
                    query_params['conversation_id'] = handler.conversation_id
                    gen_handler = GenerateAnswer(query_params, wrapper)

                    # Inject state from first handler
                    gen_handler.final_ranked_answers = handler.final_ranked_answers
                    gen_handler.items = [
                        [r.get('url', ''), json.dumps(r.get('schema_object', {})), r.get('name', ''), r.get('site', '')]
                        for r in handler.final_ranked_answers
                    ]
                    gen_handler.decontextualized_query = handler.decontextualized_query
                    gen_handler.connection_alive_event = handler.connection_alive_event
                    gen_handler.query_id = handler.query_id  # Issue #3: same query

                    await gen_handler.synthesizeAnswer()
            except Exception as e:
                unified_error = True
                logger.error(f"Error in unified mode: {e}", exc_info=True)
            finally:
                # Always send end response (api.py controls timing)
                await handler.message_sender.send_end_response(error=unified_error)

            # Issue #4: Log unified analytics with full latency
            try:
                unified_total_ms = (time_mod.time() - unified_start_time) * 1000
                from core.query_logger import get_query_logger
                query_logger = get_query_logger()
                num_results = len(handler.final_ranked_answers) if hasattr(handler, 'final_ranked_answers') else 0
                query_logger.log_query_complete(
                    query_id=handler.query_id,
                    latency_total_ms=unified_total_ms,
                    num_results_retrieved=getattr(handler, 'num_retrieved', 0),
                    num_results_ranked=getattr(handler, 'num_ranked', 0),
                    num_results_returned=num_results,
                    cost_usd=getattr(handler, 'estimated_cost', 0),
                    error_occurred=unified_error
                )
            except Exception as e:
                logger.warning(f"Failed to log unified analytics: {e}")
        else:
            # Use base NLWebHandler for other modes (summarize, none)
            from core.baseHandler import NLWebHandler
            handler = NLWebHandler(query_params, wrapper)
            wrapper.set_on_disconnect(lambda: handler.connection_alive_event.clear())
            await handler.runQuery()
        
        # Send completion message (Phase 4b.5 Fix 1: stamp user_id)
        complete_msg = {"message_type": "complete", "sender_info": {"id": "system", "name": "NLWeb"}}
        inject_user_id(complete_msg, handler)
        await wrapper.write_stream(complete_msg)

    except Exception as e:
        import traceback; traceback.print_exc()  # DEBUG: print full traceback to stderr
        logger.error(f"Error in streaming ask handler: {e}", exc_info=True)
        await wrapper.send_error_response(500, str(e))
    finally:
        await wrapper.finish_response()
    
    return response


async def handle_regular_ask(request: web.Request, query_params: Dict[str, Any]) -> web.Response:
    """Handle non-streaming ask requests"""
    
    try:
        # Determine which handler to use
        generate_mode = query_params.get('generate_mode', 'none')

        # #2 (deploy-env-hardening): deep_research 同步路徑在 prod 會被
        # Cloudflare 100s 砍 524,且 524 後 origin 仍背景燒 LLM 跑完。
        # fail-fast 早退,引導 client 改用 streaming endpoint。
        # 只擋 deep_research;generate/unified/none/summarize 一律放行。
        if generate_mode == 'deep_research':
            logger.info(
                "Rejected synchronous deep_research on /ask "
                "(would 524 at Cloudflare); guiding client to streaming endpoint"
            )
            return web.json_response(
                {
                    "message_type": "error",
                    "error": "deep_research_requires_streaming",
                    "message": "deep_research 模式請改用 streaming endpoint"
                              "（/ask?streaming=true 或 /api/deep_research），"
                              "同步請求會在邊緣逾時。",
                },
                status=400,
            )

        if generate_mode == 'generate':
            handler = GenerateAnswer(query_params, None)
        elif generate_mode == 'deep_research':
            # Deep research mode with multi-agent reasoning
            from methods.deep_research import DeepResearchHandler
            handler = DeepResearchHandler(query_params, None)
        else:
            # Use base NLWebHandler for other modes (summarize, none)
            from core.baseHandler import NLWebHandler
            handler = NLWebHandler(query_params, None)
        
        # Run the query - it will return the complete response
        result = await handler.runQuery()
        
        # Return the response directly
        return web.json_response(result)
        
    except Exception as e:
        logger.error(f"Error in regular ask handler: {e}", exc_info=True)
        return web.json_response({
            "message_type": "error",
            "error": str(e)
        }, status=500)


async def who_handler(request: web.Request) -> web.Response:
    """Handle /who endpoint with optional streaming support"""
    
    try:
        # Get query parameters
        query_params = dict(request.query)
        
        # Check if SSE streaming is requested
        is_sse = request.get('is_sse', False)
        streaming = get_param(query_params, "streaming", str, "False")
        streaming = streaming not in ["False", "false", "0"]
        
        if is_sse or streaming:
            # Handle streaming response
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )
            
            await response.prepare(request)
            
            # Create aiohttp-compatible wrapper
            wrapper = AioHttpStreamingWrapper(request, response, query_params)
            await wrapper.prepare_response()
            
            try:
                # Run the who handler with streaming
                handler = WhoHandler(query_params, wrapper)
                await handler.runQuery()
                
                # Send completion message (Phase 4b.5 Fix 1: stamp user_id)
                complete_msg = {"message_type": "complete", "sender_info": {"id": "system", "name": "NLWeb"}}
                inject_user_id(complete_msg, handler)
                await wrapper.write_stream(complete_msg)

            except Exception as e:
                logger.error(f"Error in streaming who handler: {e}", exc_info=True)
                await wrapper.send_error_response(500, str(e))
            finally:
                await wrapper.finish_response()
            
            return response
        else:
            # Handle non-streaming response
            handler = WhoHandler(query_params, None)
            result = await handler.runQuery()
            return web.json_response(result)
        
    except Exception as e:
        logger.error(f"Error in who handler: {e}", exc_info=True)
        return web.json_response({
            "message_type": "error",
            "error": str(e)
        }, status=500)


async def sites_handler(request: web.Request) -> web.Response:
    """Handle /sites endpoint to get available sites"""
    
    try:
        # Get query parameters
        query_params = dict(request.query)
        
        # Check if streaming is requested
        streaming = get_param(query_params, "streaming", str, "False")
        streaming = streaming not in ["False", "false", "0"]
        
        # Create a retriever client
        retriever = get_vector_db_client(query_params=query_params)
        
        # Get the list of sites
        sites = await retriever.get_sites()
        
        # Prepare the response
        response_data = {
            "message-type": "sites",
            "sites": sites
        }
        
        if streaming or request.get('is_sse', False):
            # Return as SSE
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )
            await response.prepare(request)
            await response.write(f"data: {json.dumps(response_data)}\n\n".encode())
            return response
        else:
            # Return as JSON
            return web.json_response(response_data)
            
    except Exception as e:
        logger.error(f"Error getting sites: {e}", exc_info=True)
        error_data = {
            "message-type": "error",
            "error": f"Failed to get sites: {str(e)}"
        }
        return web.json_response(error_data, status=500)


# --- /sites_config cache ---
_sites_config_cache: dict | None = None
_sites_config_cache_time: float = 0
_SITES_CONFIG_CACHE_TTL = 300  # 5 minutes


def _build_sites_xml_only() -> list:
    """Fallback: build sites list from sites.xml only (no DB)."""
    site_configs = {}
    if hasattr(CONFIG, 'nlweb') and hasattr(CONFIG.nlweb, 'site_configs'):
        site_configs = CONFIG.nlweb.site_configs

    sites_list = []
    for site_name, config in site_configs.items():
        desc = config.description or site_name
        display = desc.split(" - ")[0].strip() if " - " in desc else desc
        sites_list.append({
            "name": site_name,
            "description": desc,
            "display_name": display,
            "item_types": config.item_types,
        })
    sites_list.sort(key=lambda x: x["name"])
    return sites_list


async def sites_config_handler(request: web.Request) -> web.Response:
    """Handle /sites_config endpoint.

    Merges DB-discovered sources with sites.xml metadata overlay.
    DB is the source of truth for which sources exist; sites.xml provides
    display names and item_types. Falls back to sites.xml-only on DB error.
    """
    global _sites_config_cache, _sites_config_cache_time

    try:
        now = time_mod.time()
        if _sites_config_cache and (now - _sites_config_cache_time) < _SITES_CONFIG_CACHE_TTL:
            return web.json_response(_sites_config_cache)

        # sites.xml metadata (loaded at startup)
        site_configs = {}
        if hasattr(CONFIG, 'nlweb') and hasattr(CONFIG.nlweb, 'site_configs'):
            site_configs = CONFIG.nlweb.site_configs

        # DB distinct sources
        db_sources = None
        try:
            retriever = get_vector_db_client(query_params=dict(request.query))
            db_sources = await retriever.get_sites()
        except Exception as e:
            logger.warning(f"DB source discovery failed, falling back to sites.xml: {e}")

        if not db_sources:
            # Fallback to sites.xml only
            sites_list = _build_sites_xml_only()
        else:
            # Merge: DB sources + sites.xml overlay
            sites_list = []
            for source in db_sources:
                xml_config = site_configs.get(source)
                if xml_config:
                    desc = xml_config.description or source
                    display = desc.split(" - ")[0].strip() if " - " in desc else desc
                    item_types = xml_config.item_types
                else:
                    desc = source
                    display = source
                    item_types = ["Article"]
                sites_list.append({
                    "name": source,
                    "description": desc,
                    "display_name": display,
                    "item_types": item_types,
                })
            sites_list.sort(key=lambda x: x["name"])

        response_data = {
            "message_type": "sites_config",
            "sites": sites_list,
        }

        _sites_config_cache = response_data
        _sites_config_cache_time = now

        return web.json_response(response_data)

    except Exception as e:
        logger.error(f"Error getting sites config: {e}", exc_info=True)
        return web.json_response({
            "message_type": "error",
            "error": f"Failed to get sites config: {str(e)}"
        }, status=500)


async def deep_research_handler(request: web.Request) -> web.Response:
    """Handle /api/deep_research endpoint for Deep Research mode with SSE streaming"""

    # Get query parameters
    query_params = dict(request.query)

    # For POST requests, merge body parameters
    if request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                body_data = await request.json()
                query_params.update(body_data)
            elif request.content_type == 'application/x-www-form-urlencoded':
                body_data = await request.post()
                query_params.update(dict(body_data))
        except Exception as e:
            logger.warning(f"Failed to parse POST body: {e}")

    # Force Deep Research mode
    query_params['generate_mode'] = 'deep_research'
    query_params['streaming'] = 'true'  # Always use streaming for Deep Research

    # Extract query
    query = get_param(query_params, "query", str, "")
    if not query:
        return web.json_response({
            "message_type": "error",
            "error": "Missing query parameter"
        }, status=400)

    # P1-2: Query length pre-check (before SSE stream starts — must return HTTP 400 JSON)
    if len(query) > MAX_QUERY_LENGTH:
        client_ip = _get_client_ip(request)
        user = request.get('user')
        uid = user.get('id') if user and user.get('authenticated') else None
        try:
            from core.guardrail_logger import GuardrailLogger
            await GuardrailLogger.get_instance().log_event(
                event_type='query_rejected',
                severity='info',
                user_id=uid,
                client_ip=client_ip,
                details={'reason': 'query_too_long', 'length': len(query)},
            )
        except Exception as _log_err:
            logger.warning(f"GuardrailLogger failed in deep_research_handler: {_log_err}")
        return web.json_response(
            {'error': 'query_too_long', 'message': '查詢過長，請縮短至 500 字元以內'},
            status=400,
        )

    logger.info(f"Deep Research request: {query}")

    # P1-1b: Kill switch
    if os.environ.get('GUARDRAIL_DR_ENABLED', 'true').lower() == 'false':
        return web.json_response(
            {'error': 'dr_disabled', 'message': 'Deep Research 功能暫時關閉'},
            status=503,
        )

    # P1-1b: Concurrency checks — general search slot + DR-specific slot
    dr_client_ip = _get_client_ip(request)
    dr_user = request.get('user')
    dr_uid = dr_user.get('id') if dr_user and dr_user.get('authenticated') else None
    dr_request_id = f"req_{int(time_mod.time() * 1000)}_{id(request)}"
    dr_session_id = query_params.get('session_id') or dr_uid or dr_client_ip

    if dr_uid:
        dr_search_key = f"search:{dr_session_id}"
        dr_search_limit = SEARCH_SESSION_LIMIT
    else:
        dr_search_key = f"search_ip:{dr_client_ip}"
        dr_search_limit = SEARCH_IP_LIMIT

    if dr_uid:
        dr_conc_key = f"dr_user:{dr_uid}"
        dr_conc_limit = DR_USER_LIMIT
    else:
        dr_conc_key = f"dr_ip:{dr_client_ip}"
        dr_conc_limit = DR_IP_LIMIT

    dr_limiter = ConcurrencyLimiter.get_instance()
    dr_search_acquired = False
    dr_slot_acquired = False

    # Acquire general search slot
    if not dr_limiter.try_acquire(dr_search_key, dr_request_id, dr_search_limit):
        try:
            from core.guardrail_logger import GuardrailLogger
            await GuardrailLogger.get_instance().log_event(
                event_type='concurrency_limit',
                severity='warning',
                user_id=dr_uid,
                client_ip=dr_client_ip,
                details={'key': dr_search_key, 'limit': dr_search_limit},
            )
        except Exception as _log_err:
            logger.warning(f"GuardrailLogger failed (DR search concurrency): {_log_err}")
        return web.json_response(
            {'error': 'rate_limited', 'message': '目前查詢量過大，請稍後再試', 'retry_after_seconds': 30},
            status=429,
        )
    dr_search_acquired = True

    # Acquire DR-specific slot
    dr_slot_id = f"dr_{dr_request_id}"
    if not dr_limiter.try_acquire(dr_conc_key, dr_slot_id, dr_conc_limit):
        dr_limiter.release(dr_search_key, dr_request_id)
        try:
            from core.guardrail_logger import GuardrailLogger
            await GuardrailLogger.get_instance().log_event(
                event_type='concurrency_limit',
                severity='warning',
                user_id=dr_uid,
                client_ip=dr_client_ip,
                details={'key': dr_conc_key, 'limit': dr_conc_limit, 'reason': 'dr_concurrency'},
            )
        except Exception as _log_err:
            logger.warning(f"GuardrailLogger failed (DR concurrency): {_log_err}")
        return web.json_response(
            {'error': 'rate_limited', 'message': 'Deep Research 同時只能進行一個，請等待完成後再試', 'retry_after_seconds': 30},
            status=429,
        )
    dr_slot_acquired = True

    try:
        # Create SSE response with proper headers
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no'
            }
        )
        await response.prepare(request)

        # Create streaming wrapper
        wrapper = AioHttpStreamingWrapper(request, response, query_params)
        await wrapper.prepare_response()

        # Import and create Deep Research handler
        from methods.deep_research import DeepResearchHandler
        handler = DeepResearchHandler(query_params, wrapper)

        def _on_dr_endpoint_disconnect():
            handler.connection_alive_event.clear()
            # Task 6: Cancel background research task if running
            if hasattr(handler, '_research_task') and handler._research_task:
                handler._research_task.cancel()

        wrapper.set_on_disconnect(_on_dr_endpoint_disconnect)

        # Pre-generate query_id so begin message and executeQuery use the same ID
        if not hasattr(handler, 'query_id') or not handler.query_id:
            handler.query_id = f"query_{int(time_mod.time() * 1000)}"

        # Register query_id in queries table BEFORE prepare()/retrieval writes retrieved_documents
        try:
            from core.query_logger import get_query_logger
            ql = get_query_logger()
            if ql:
                ql.log_query_start(
                    query_id=handler.query_id,
                    user_id=getattr(handler, 'user_id', '') or 'anonymous',
                    query_text=query,
                    site=str(getattr(handler, 'site', 'all')),
                    mode='deep_research',
                )
        except Exception as e:
            logger.warning(f"Failed to pre-register query_id (non-fatal): {e}")

        # Send begin-nlweb-response so frontend can capture conversation_id and query_id
        begin_message = {
            "message_type": "begin-nlweb-response",
            "query": query,
            "conversation_id": handler.conversation_id,
            "query_id": handler.query_id
        }
        # Phase 4b.5 Fix 1: stamp user_id so frontend Trigger G can do envelope-level identity check.
        inject_user_id(begin_message, handler)
        await wrapper.write_stream(begin_message)
        logger.info(f"[Deep Research] Sent begin-nlweb-response with conversation_id={handler.conversation_id}, query_id={handler.query_id}")

        # Run Deep Research query (will stream progress via SSE)
        result = await handler.runQuery()

        # Send final result message (skip if clarification is pending)
        if result and result.get('status') != 'clarification_pending':
            final_message = {
                "message_type": "final_result",
                "final_report": result.get('answer', ''),
                "confidence_level": result.get('confidence_level', 'Medium'),
                "methodology": result.get('methodology_note', ''),
                "sources": result.get('sources_used', [])
            }

            # Extract argument_graph and reasoning_chain_analysis from schema_object (Phase 4)
            # These are stored in the first item's schema_object by the orchestrator
            items = result.get('items', [])
            logger.info(f"[Deep Research] Result items count: {len(items)}, result keys: {list(result.keys())}")
            if items and len(items) > 0:
                schema_obj = items[0].get('schema_object', {})
                logger.info(f"[Deep Research] schema_object keys: {list(schema_obj.keys()) if schema_obj else 'NONE'}")
                if schema_obj.get('argument_graph'):
                    final_message['argument_graph'] = schema_obj['argument_graph']
                if schema_obj.get('reasoning_chain_analysis'):
                    final_message['reasoning_chain_analysis'] = schema_obj['reasoning_chain_analysis']
                if schema_obj.get('knowledge_graph'):
                    final_message['knowledge_graph'] = schema_obj['knowledge_graph']
                    logger.info(f"[Deep Research] KG included: {len(schema_obj['knowledge_graph'].get('entities', []))} entities")
                else:
                    logger.warning("[Deep Research] NO knowledge_graph in schema_object")
                # RSN-4: Include verification status for frontend warning banner
                if schema_obj.get('verification_status'):
                    final_message['verification_status'] = schema_obj['verification_status']
                if schema_obj.get('verification_message'):
                    final_message['verification_message'] = schema_obj['verification_message']
            else:
                logger.warning(f"[Deep Research] No items in result — KG cannot be extracted")

            logger.info(f"[Deep Research] final_message keys: {list(final_message.keys())}")
            # Phase 4b.5 Fix 1: stamp user_id on final_message (ad-hoc envelope).
            inject_user_id(final_message, handler)
            await wrapper.write_stream(final_message)

            # Note: Research report is now passed directly from frontend to backend
            # via query_params in free conversation mode, no DB storage needed

        # Close the stream (Phase 4b.5 Fix 1: stamp user_id)
        complete_msg = {"message_type": "complete"}
        inject_user_id(complete_msg, handler)
        await wrapper.write_stream(complete_msg)
        await wrapper.finish_response()

        return response

    except ConnectionResetError as e:
        logger.info(f"Deep Research client disconnected: {e}")
        try:
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    except Exception as e:
        logger.error(f"Deep Research error: {e}", exc_info=True)
        error_data = {
            "message_type": "error",
            "error": str(e)
        }
        try:
            await wrapper.write_stream(error_data)
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    finally:
        # Always release concurrency slots — even if request crashes
        if dr_search_acquired:
            dr_limiter.release(dr_search_key, dr_request_id)
        if dr_slot_acquired:
            dr_limiter.release(dr_conc_key, dr_slot_id)


async def research_rerun_handler(request: web.Request) -> web.Response:
    """Handle POST /api/research/rerun — selective re-run of deep research with KG edits.

    Skips phase 1 (search) and reuses cached formatted_context from a previous
    deep research run. Runs phases 2-4 (actor-critic, writer, format) with a
    modified query that includes the user's KG edit instructions.

    Request body (JSON):
        query_id: str — query_id of the original deep research run
        kg_edits: str — serialized JSON of KG edits (schema_version 1.0)
        query: str — original query text (for building modified query)
    """
    # Feature flag gate
    enable_composable = CONFIG.reasoning_params.get("features", {}).get(
        "composable_pipeline", False
    )
    if not enable_composable:
        return web.json_response(
            {'error': 'not_implemented', 'message': 'Selective re-run requires composable_pipeline feature flag'},
            status=501,
        )

    # Parse request body
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    original_query_id = data.get('query_id')
    kg_edits = data.get('kg_edits')
    query = data.get('query', '')

    if not original_query_id or not kg_edits:
        return web.json_response(
            {'error': 'query_id and kg_edits required'},
            status=400,
        )

    if not query:
        return web.json_response(
            {'error': 'query (original query text) is required'},
            status=400,
        )

    # Validate cached state exists before starting SSE stream
    from reasoning.orchestrator import get_cached_research_state
    if get_cached_research_state(original_query_id) is None:
        return web.json_response(
            {'error': 'cache_miss', 'message': f'No cached research state for query_id={original_query_id}. The original research may have expired or the server was restarted.'},
            status=400,
        )

    logger.info(f"[RERUN] Research rerun request: query_id={original_query_id}")

    # P1-1b: Kill switch (same as deep_research_handler)
    if os.environ.get('GUARDRAIL_DR_ENABLED', 'true').lower() == 'false':
        return web.json_response(
            {'error': 'dr_disabled', 'message': 'Deep Research 功能暫時關閉'},
            status=503,
        )

    # P1-1b: DR concurrency check (rerun consumes same LLM resources as DR)
    rerun_client_ip = _get_client_ip(request)
    rerun_user = request.get('user')
    rerun_uid = rerun_user.get('id') if rerun_user and rerun_user.get('authenticated') else None
    rerun_request_id = f"req_{int(time_mod.time() * 1000)}_{id(request)}"

    if rerun_uid:
        rerun_dr_key = f"dr_user:{rerun_uid}"
        rerun_dr_limit = DR_USER_LIMIT
    else:
        rerun_dr_key = f"dr_ip:{rerun_client_ip}"
        rerun_dr_limit = DR_IP_LIMIT

    rerun_limiter = ConcurrencyLimiter.get_instance()
    rerun_slot_id = f"dr_{rerun_request_id}"
    rerun_slot_acquired = False

    if not rerun_limiter.try_acquire(rerun_dr_key, rerun_slot_id, rerun_dr_limit):
        try:
            from core.guardrail_logger import GuardrailLogger
            await GuardrailLogger.get_instance().log_event(
                event_type='concurrency_limit',
                severity='warning',
                user_id=rerun_uid,
                client_ip=rerun_client_ip,
                details={'key': rerun_dr_key, 'limit': rerun_dr_limit, 'reason': 'dr_rerun_concurrency'},
            )
        except Exception as _log_err:
            logger.warning(f"GuardrailLogger failed (rerun DR concurrency): {_log_err}")
        return web.json_response(
            {'error': 'rate_limited', 'message': 'Deep Research 同時只能進行一個，請等待完成後再試', 'retry_after_seconds': 30},
            status=429,
        )
    rerun_slot_acquired = True

    # Build query_params for DeepResearchHandler
    query_params = {
        'query': query,
        'generate_mode': 'deep_research',
        'streaming': 'true',
        'skip_clarification': 'true',  # Skip clarification for rerun
    }

    # Inject auth user info
    user = request.get('user')
    if user and user.get('authenticated'):
        query_params['user_id'] = user['id']
        if user.get('org_id'):
            query_params['org_id'] = user['org_id']

    try:
        # Create SSE response
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )
        await response.prepare(request)

        # Create streaming wrapper
        wrapper = AioHttpStreamingWrapper(request, response, query_params)
        await wrapper.prepare_response()

        # Create handler (skip normal runQuery flow — we only need SSE + rerun)
        from methods.deep_research import DeepResearchHandler
        handler = DeepResearchHandler(query_params, wrapper)

        # Set query_id on handler — runQuery() normally sets this, but rerun skips
        # runQuery(). Without this, any code path that accesses handler.query_id
        # (e.g., gap search analytics in retrieval providers) would crash with
        # AttributeError.
        handler.query_id = f"rerun_{int(time_mod.time() * 1000)}"

        # Set research_mode from cached state
        cached = get_cached_research_state(original_query_id)
        handler.research_mode = cached['mode'] if cached else 'discovery'

        def _on_rerun_disconnect():
            handler.connection_alive_event.clear()
            if hasattr(handler, '_research_task') and handler._research_task:
                handler._research_task.cancel()

        wrapper.set_on_disconnect(_on_rerun_disconnect)

        # Send begin-nlweb-response
        begin_message = {
            "message_type": "begin-nlweb-response",
            "query": query,
            "conversation_id": handler.conversation_id,
            "is_rerun": True,
            "original_query_id": original_query_id,
        }
        # Phase 4b.5 Fix 1: stamp user_id (ad-hoc envelope, bypasses add_message_metadata).
        inject_user_id(begin_message, handler)
        await wrapper.write_stream(begin_message)

        # Serialize kg_edits to JSON string if not already
        kg_edits_json = kg_edits if isinstance(kg_edits, str) else json.dumps(kg_edits, ensure_ascii=False)

        # Execute rerun (phases 2-4 only)
        await handler.execute_rerun(
            original_query_id=original_query_id,
            kg_edits_json=kg_edits_json,
        )

        # Send final result
        result = handler.return_value
        if result:
            final_message = {
                "message_type": "final_result",
                "final_report": result.get('answer', ''),
                "confidence_level": result.get('confidence_level', 'Medium'),
                "methodology": result.get('methodology_note', ''),
                "sources": result.get('sources_used', []),
                "is_rerun": True,
            }

            items = result.get('items', [])
            if items and len(items) > 0:
                schema_obj = items[0].get('schema_object', {})
                if schema_obj.get('argument_graph'):
                    final_message['argument_graph'] = schema_obj['argument_graph']
                if schema_obj.get('reasoning_chain_analysis'):
                    final_message['reasoning_chain_analysis'] = schema_obj['reasoning_chain_analysis']
                if schema_obj.get('knowledge_graph'):
                    final_message['knowledge_graph'] = schema_obj['knowledge_graph']
                if schema_obj.get('verification_status'):
                    final_message['verification_status'] = schema_obj['verification_status']
                if schema_obj.get('verification_message'):
                    final_message['verification_message'] = schema_obj['verification_message']

            # Phase 4b.5 Fix 1: stamp user_id on final_message (ad-hoc envelope).
            inject_user_id(final_message, handler)
            await wrapper.write_stream(final_message)

        # Phase 4b.5 Fix 1: stamp user_id on complete envelope.
        complete_msg = {"message_type": "complete"}
        inject_user_id(complete_msg, handler)
        await wrapper.write_stream(complete_msg)
        await wrapper.finish_response()

        return response

    except ValueError as e:
        # Cache miss or invalid state — should not happen since we checked above
        logger.error(f"[RERUN] ValueError: {e}")
        error_data = {"message_type": "error", "error": str(e)}
        try:
            await wrapper.write_stream(error_data)
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    except ConnectionResetError as e:
        logger.info(f"[RERUN] Client disconnected: {e}")
        try:
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    except Exception as e:
        logger.error(f"[RERUN] Research rerun error: {e}", exc_info=True)
        error_data = {"message_type": "error", "error": str(e)}
        try:
            await wrapper.write_stream(error_data)
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    finally:
        # Always release DR concurrency slot — even if request crashes
        if rerun_slot_acquired:
            rerun_limiter.release(rerun_dr_key, rerun_slot_id)


async def feedback_handler(request: web.Request) -> web.Response:
    """Handle POST /api/feedback — store user feedback (thumbs up/down + comment)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    rating = body.get("rating", "")
    if rating not in ("positive", "negative"):
        return web.json_response({"error": "rating must be 'positive' or 'negative'"}, status=400)

    query = body.get("query", "")
    answer_snippet = body.get("answer_snippet", "")
    comment = body.get("comment", "")[:2000] if body.get("comment") else ""
    session_id = body.get("session_id", "")
    query_id = body.get("query_id") or None

    # Extract authenticated user info for B2B analytics
    auth_user = request.get('user') or {}
    feedback_user_id = auth_user.get('id') if auth_user.get('authenticated') else None
    feedback_org_id = auth_user.get('org_id') if auth_user.get('authenticated') else None

    try:
        from core.query_logger import get_query_logger
        ql = get_query_logger()
        ql.log_feedback(
            query=query,
            answer_snippet=answer_snippet,
            rating=rating,
            comment=comment,
            session_id=session_id,
            query_id=query_id,
            user_id=feedback_user_id,
            org_id=feedback_org_id,
        )
        logger.info(f"[Feedback] Stored: rating={rating}, query='{query[:50]}'")
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"[Feedback] Failed to store feedback: {e}", exc_info=True)
        return web.json_response({"error": "Failed to store feedback"}, status=500)


async def live_research_start_handler(request: web.Request) -> web.Response:
    """Handle POST /api/live_research — start new Live Research session."""

    # Feature flag check
    if not CONFIG.reasoning_params.get("features", {}).get("live_research", False):
        return web.json_response(
            {"error": "live_research_disabled", "message": "Live Research 功能尚未啟用"},
            status=503,
        )

    # Parse body
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    query = body.get("query", "")
    if not query:
        return web.json_response({"error": "missing_query"}, status=400)

    # Build query_params
    query_params = dict(request.query)
    query_params.update(body)
    query_params["generate_mode"] = "live_research"
    query_params["streaming"] = "true"

    # Inject auth
    user = request.get("user")
    if user and user.get("authenticated"):
        query_params["user_id"] = user["id"]
        if user.get("org_id"):
            query_params["org_id"] = user["org_id"]

    # C-2: Concurrency check — Live Research uses 8-10x LLM calls
    lr_start_limiter = None
    lr_start_search_key = None
    lr_start_conc_key = None
    lr_start_request_id = None
    lr_start_slot_id = None
    lr_start_search_acquired = False
    lr_start_slot_acquired = False

    if os.environ.get('GUARDRAIL_DR_ENABLED', 'true').lower() != 'false':
        lr_start_client_ip = _get_client_ip(request)
        lr_start_user = request.get('user')
        lr_start_uid = lr_start_user.get('id') if lr_start_user and lr_start_user.get('authenticated') else None
        lr_start_request_id = f"req_{int(time_mod.time() * 1000)}_{id(request)}"
        lr_start_session_id = query_params.get('session_id') or lr_start_uid or lr_start_client_ip

        if lr_start_uid:
            lr_start_search_key = f"search:{lr_start_session_id}"
            lr_start_search_limit = SEARCH_SESSION_LIMIT
        else:
            lr_start_search_key = f"search_ip:{lr_start_client_ip}"
            lr_start_search_limit = SEARCH_IP_LIMIT

        if lr_start_uid:
            lr_start_conc_key = f"lr_user:{lr_start_uid}"
            lr_start_conc_limit = DR_USER_LIMIT
        else:
            lr_start_conc_key = f"lr_ip:{lr_start_client_ip}"
            lr_start_conc_limit = DR_IP_LIMIT

        lr_start_limiter = ConcurrencyLimiter.get_instance()

        if not lr_start_limiter.try_acquire(lr_start_search_key, lr_start_request_id, lr_start_search_limit):
            return web.json_response(
                {'error': 'rate_limited', 'message': '目前查詢量過大，請稍後再試', 'retry_after_seconds': 30},
                status=429,
            )
        lr_start_search_acquired = True

        lr_start_slot_id = f"lr_{lr_start_request_id}"
        if not lr_start_limiter.try_acquire(lr_start_conc_key, lr_start_slot_id, lr_start_conc_limit):
            lr_start_limiter.release(lr_start_search_key, lr_start_request_id)
            lr_start_search_acquired = False
            return web.json_response(
                {'error': 'rate_limited', 'message': 'Live Research 同時只能進行一個，請等待完成後再試', 'retry_after_seconds': 30},
                status=429,
            )
        lr_start_slot_acquired = True

    # Create SSE response
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    wrapper = AioHttpStreamingWrapper(request, response, query_params)
    await wrapper.prepare_response()

    # Mock mode: send canned SSE events without LLM (dev/E2E testing only)
    if body.get("mock"):
        # Release concurrency slots immediately so rapid continues are not rate-limited
        if lr_start_limiter is not None:
            if lr_start_search_acquired:
                lr_start_limiter.release(lr_start_search_key, lr_start_request_id)
            if lr_start_slot_acquired:
                lr_start_limiter.release(lr_start_conc_key, lr_start_slot_id)
            lr_start_search_acquired = False
            lr_start_slot_acquired = False
        session_id = query_params.get("session_id") or f"mock_{int(time_mod.time() * 1000)}"
        _mock_lr_sessions[session_id] = {"stage": 1}
        mock_events = [
            {"message_type": "live_research_stage_change", "stage": 1},
            {"message_type": "live_research_narration", "text": "開始建立研究結構..."},
            {"message_type": "live_research_narration", "text": "找到了幾個核心面向：土地使用、社區參與、電網整合"},
            {"message_type": "live_research_checkpoint", "stage": 1, "proposal": "## 研究結構提案\n\n**研究問題**：台灣綠能發展衝突\n\n1. **土地使用衝突**（核心）— 光電與農地爭議\n2. **社區參與**（核心）— 居民反對與溝通\n3. **電網整合**（輔助）— 再生能源併網挑戰\n\n這是我整理的研究結構，你覺得如何？", "auto_continue_option": True},
        ]
        for evt in mock_events:
            await asyncio.sleep(0.3)
            await response.write(f"data: {json.dumps(evt, ensure_ascii=False)}\n\n".encode("utf-8"))
        try:
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    from methods.live_research import LiveResearchHandler

    handler = LiveResearchHandler(query_params, wrapper)

    # UX-4: disconnect → clear alive event + cancel in-flight LR task so
    # Stage 5 writer loop aborts instead of burning tokens. Mirrors DR
    # `_on_dr_disconnect` (line 221) pattern.
    def _on_lr_disconnect():
        handler.connection_alive_event.clear()
        if handler._lr_research_task is not None:
            handler._lr_research_task.cancel()

    wrapper.set_on_disconnect(_on_lr_disconnect)

    try:
        await handler.runQuery()
    except asyncio.CancelledError:
        logger.info("Live Research start: task cancelled (disconnect)")
    except ConnectionResetError as e:
        logger.info(f"Live Research start: client disconnected: {e}")
        try:
            await wrapper.finish_response()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Live Research start error: {e}", exc_info=True)
        try:
            await wrapper.write_stream({"message_type": "error", "error": str(e)})
        except Exception:
            pass
        try:
            await wrapper.finish_response()
        except Exception:
            pass
    finally:
        if lr_start_limiter is not None:
            if lr_start_search_acquired:
                lr_start_limiter.release(lr_start_search_key, lr_start_request_id)
            if lr_start_slot_acquired:
                lr_start_limiter.release(lr_start_conc_key, lr_start_slot_id)

    return response


async def live_research_continue_handler(request: web.Request) -> web.Response:
    """Handle POST /api/live_research/continue — continue from checkpoint."""

    if not CONFIG.reasoning_params.get("features", {}).get("live_research", False):
        return web.json_response(
            {"error": "live_research_disabled", "message": "Live Research 功能尚未啟用"},
            status=503,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    session_id = body.get("session_id")
    if not session_id:
        return web.json_response({"error": "missing_session_id"}, status=400)

    user_message = body.get("user_message", "")
    auto_continue = body.get("auto_continue", False)

    query_params = dict(request.query)
    query_params.update(body)
    query_params["generate_mode"] = "live_research"
    query_params["streaming"] = "true"
    query_params["session_id"] = session_id
    # Pass server-generated LR session UUID back to handler for state persistence
    query_params["lr_session_id"] = body.get("lr_session_id", "")

    user = request.get("user")
    if user and user.get("authenticated"):
        query_params["user_id"] = user["id"]
        if user.get("org_id"):
            query_params["org_id"] = user["org_id"]

    # C-2: Concurrency check — Live Research continue also triggers LLM pipelines
    lr_cont_limiter = None
    lr_cont_search_key = None
    lr_cont_conc_key = None
    lr_cont_request_id = None
    lr_cont_slot_id = None
    lr_cont_search_acquired = False
    lr_cont_slot_acquired = False

    if os.environ.get('GUARDRAIL_DR_ENABLED', 'true').lower() != 'false':
        lr_cont_client_ip = _get_client_ip(request)
        lr_cont_user = request.get('user')
        lr_cont_uid = lr_cont_user.get('id') if lr_cont_user and lr_cont_user.get('authenticated') else None
        lr_cont_request_id = f"req_{int(time_mod.time() * 1000)}_{id(request)}"
        lr_cont_session_id = session_id or lr_cont_uid or lr_cont_client_ip

        if lr_cont_uid:
            lr_cont_search_key = f"search:{lr_cont_session_id}"
            lr_cont_search_limit = SEARCH_SESSION_LIMIT
        else:
            lr_cont_search_key = f"search_ip:{lr_cont_client_ip}"
            lr_cont_search_limit = SEARCH_IP_LIMIT

        if lr_cont_uid:
            lr_cont_conc_key = f"lr_user:{lr_cont_uid}"
            lr_cont_conc_limit = DR_USER_LIMIT
        else:
            lr_cont_conc_key = f"lr_ip:{lr_cont_client_ip}"
            lr_cont_conc_limit = DR_IP_LIMIT

        lr_cont_limiter = ConcurrencyLimiter.get_instance()

        if not lr_cont_limiter.try_acquire(lr_cont_search_key, lr_cont_request_id, lr_cont_search_limit):
            return web.json_response(
                {'error': 'rate_limited', 'message': '目前查詢量過大，請稍後再試', 'retry_after_seconds': 30},
                status=429,
            )
        lr_cont_search_acquired = True

        lr_cont_slot_id = f"lr_{lr_cont_request_id}"
        if not lr_cont_limiter.try_acquire(lr_cont_conc_key, lr_cont_slot_id, lr_cont_conc_limit):
            lr_cont_limiter.release(lr_cont_search_key, lr_cont_request_id)
            lr_cont_search_acquired = False
            return web.json_response(
                {'error': 'rate_limited', 'message': 'Live Research 同時只能進行一個，請等待完成後再試', 'retry_after_seconds': 30},
                status=429,
            )
        lr_cont_slot_acquired = True

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    wrapper = AioHttpStreamingWrapper(request, response, query_params)
    await wrapper.prepare_response()

    # Mock mode: advance through stages using in-memory session state
    if body.get("mock"):
        # Release concurrency slots immediately so rapid continues are not rate-limited
        if lr_cont_limiter is not None:
            if lr_cont_search_acquired:
                lr_cont_limiter.release(lr_cont_search_key, lr_cont_request_id)
            if lr_cont_slot_acquired:
                lr_cont_limiter.release(lr_cont_conc_key, lr_cont_slot_id)
            lr_cont_search_acquired = False
            lr_cont_slot_acquired = False
        session_state = _mock_lr_sessions.get(session_id, {"stage": 1})
        current_stage = session_state.get("stage", 1)
        next_stage = current_stage + 1
        _mock_lr_sessions[session_id] = {"stage": next_stage}

        if next_stage == 2:
            mock_events = [
                {"message_type": "live_research_stage_change", "stage": 2},
                {"message_type": "live_research_narration", "text": "開始蒐集「土地使用」相關的資料..."},
                {"message_type": "live_research_narration", "text": "「土地使用」的資料蒐集完成。"},
                {"message_type": "live_research_narration", "text": "開始蒐集「社區參與」相關的資料..."},
                {"message_type": "live_research_narration", "text": "「社區參與」的資料蒐集完成。"},
                {"message_type": "live_research_checkpoint", "stage": 2, "proposal": "所有段落的資料都蒐集完了。需要補充哪個部分嗎？", "auto_continue_option": True},
            ]
        elif next_stage == 3:
            mock_events = [
                {"message_type": "live_research_stage_change", "stage": 3},
                {"message_type": "live_research_narration", "text": "正在分析文章的寫作風格與語氣..."},
                {"message_type": "live_research_narration", "text": "風格分析完成：新聞報導式，客觀中立。"},
                {"message_type": "live_research_checkpoint", "stage": 3, "proposal": "建議以新聞分析風格撰寫，保持客觀立場。你覺得合適嗎？", "auto_continue_option": True},
            ]
        elif next_stage == 4:
            mock_events = [
                {"message_type": "live_research_stage_change", "stage": 4},
                {"message_type": "live_research_narration", "text": "確認報告格式：包含摘要、主體段落和參考來源..."},
                {"message_type": "live_research_checkpoint", "stage": 4, "proposal": "報告將包含：引言、土地使用衝突、社區參與機制、電網整合挑戰、結語。確認後開始撰寫。", "auto_continue_option": True},
            ]
        elif next_stage == 5:
            mock_events = [
                {"message_type": "live_research_stage_change", "stage": 5},
                {"message_type": "live_research_narration", "text": "正在撰寫「土地使用衝突」段落..."},
                {"message_type": "live_research_section", "section_index": 0, "title": "土地使用衝突", "content": "台灣近年大力推動光電建設，但因涉及農地變更...\n\n根據研究 [1]，光電開發面積已達數萬公頃，引發農民與環保團體強烈反彈。政府面臨在能源轉型與糧食安全之間取得平衡的挑戰。", "sources": ["農委會 2025 年報", "環境資訊中心"]},
                {"message_type": "live_research_narration", "text": "正在撰寫「社區參與」段落..."},
                {"message_type": "live_research_section", "section_index": 1, "title": "社區參與機制", "content": "社區參與在綠能發展中扮演關鍵角色...\n\n國外案例顯示 [2]，成功的再生能源開發需要從規劃初期就納入在地居民意見，建立利益共享機制。台灣目前的制度仍有待強化。", "sources": ["IRENA 2024", "台灣環境法學"]},
                {"message_type": "live_research_checkpoint", "stage": 5, "proposal": "所有段落都完成了。需要修改哪個部分嗎？", "auto_continue_option": True},
            ]
        else:
            # Stage 6: export
            mock_events = [
                {"message_type": "live_research_stage_change", "stage": 6},
                {"message_type": "live_research_narration", "text": "報告匯出完成！"},
                {"message_type": "live_research_export", "format": "markdown", "content": "# 台灣綠能發展衝突\n\n## 土地使用衝突\n\n台灣近年大力推動光電建設，但因涉及農地變更，光電開發面積已達數萬公頃，引發農民與環保團體強烈反彈。政府面臨在能源轉型與糧食安全之間取得平衡的挑戰。\n\n## 社區參與機制\n\n社區參與在綠能發展中扮演關鍵角色。成功的再生能源開發需要從規劃初期就納入在地居民意見，建立利益共享機制。台灣目前的制度仍有待強化。"},
            ]
            # Clean up session
            _mock_lr_sessions.pop(session_id, None)

        for evt in mock_events:
            await asyncio.sleep(0.3)
            await response.write(f"data: {json.dumps(evt, ensure_ascii=False)}\n\n".encode("utf-8"))
        try:
            await wrapper.finish_response()
        except Exception:
            pass
        return response

    from methods.live_research import LiveResearchHandler

    handler = LiveResearchHandler(query_params, wrapper)

    # UX-4: same disconnect path as start handler — cancel in-flight LR task.
    def _on_lr_disconnect():
        handler.connection_alive_event.clear()
        if handler._lr_research_task is not None:
            handler._lr_research_task.cancel()

    wrapper.set_on_disconnect(_on_lr_disconnect)

    try:
        await handler.continueResearch(
            user_message=user_message,
            auto_continue=auto_continue,
        )
    except asyncio.CancelledError:
        logger.info("Live Research continue: task cancelled (disconnect)")
    except ConnectionResetError as e:
        logger.info(f"Live Research continue: client disconnected: {e}")
        try:
            await wrapper.finish_response()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Live Research continue error: {e}", exc_info=True)
        try:
            await wrapper.write_stream({"message_type": "error", "error": str(e)})
        except Exception:
            pass
        try:
            await wrapper.finish_response()
        except Exception:
            pass
    finally:
        if lr_cont_limiter is not None:
            if lr_cont_search_acquired:
                lr_cont_limiter.release(lr_cont_search_key, lr_cont_request_id)
            if lr_cont_slot_acquired:
                lr_cont_limiter.release(lr_cont_conc_key, lr_cont_slot_id)

    return response

