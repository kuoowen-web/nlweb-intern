# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Ranking Analytics API Handler for NLWeb

Provides REST API endpoints for the ranking dashboard to query
detailed pipeline metrics and system configurations.
"""

import json
import logging
import time
from typing import Dict, Any, List, Optional
from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger
from core.analytics_db import AnalyticsDB

logger = get_configured_logger("ranking_analytics_handler")


class RankingAnalyticsHandler:
    """Handles HTTP requests for ranking analytics data."""

    def __init__(self, db_path: str = None):
        """
        Initialize the ranking analytics handler.

        Args:
            db_path: Ignored; always uses the shared singleton AnalyticsDB instance.
                     Kept for API compatibility with register_ranking_analytics_routes callers.
        """
        # Always use the shared singleton instance to avoid multiple connection pools
        self.db = AnalyticsDB.get_instance()

    async def get_config(self, request: web.Request) -> web.Response:
        """
        Get current system configuration ("Rules of the Game").

        GET /api/ranking/config
        """
        try:
            from core.config import CONFIG
            from core.ranking import Ranking

            ranking_prompt_text = ""
            try:
                if isinstance(Ranking.RANKING_PROMPT, list) and len(Ranking.RANKING_PROMPT) > 0:
                    ranking_prompt_text = Ranking.RANKING_PROMPT[0]
            except Exception as e:
                ranking_prompt_text = f"Error fetching prompt: {str(e)}"

            llm_model = "unknown"
            try:
                preferred_endpoint = CONFIG.preferred_llm_endpoint
                if preferred_endpoint and preferred_endpoint in CONFIG.llm_endpoints:
                    endpoint_config = CONFIG.llm_endpoints[preferred_endpoint]
                    if endpoint_config.models:
                        llm_model = f"{endpoint_config.models.high} (high) / {endpoint_config.models.low} (low)"
                    else:
                        llm_model = endpoint_config.llm_type
            except Exception as e:
                logger.warning(f"Could not fetch LLM model info: {e}")

            response = web.json_response({
                'llm_config': {
                    'system_prompt': ranking_prompt_text,
                    'model': llm_model
                },
                'bm25_params': CONFIG.bm25_params,
                'xgboost_params': CONFIG.xgboost_params,
                'mmr_params': CONFIG.mmr_params,
                'ranking_constants': {
                    'num_results_to_send': Ranking.NUM_RESULTS_TO_SEND,
                    'early_send_threshold': Ranking.EARLY_SEND_THRESHOLD
                }
            })
            response.headers['Cache-Control'] = 'no-store'
            return response
        except Exception as e:
            logger.error(f"Error getting ranking config: {e}", exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    async def get_pipeline_details(self, request: web.Request) -> web.Response:
        """
        Get detailed pipeline trace for a specific query.

        GET /api/ranking/pipeline/{query_id}?limit=10
        """
        query_id = request.match_info['query_id']
        try:
            limit = int(request.query.get('limit', 10))
        except (ValueError, TypeError):
            return web.json_response({'error': 'Invalid limit parameter; must be an integer'}, status=400)

        try:
            # 1. Get query info
            query_data = await self.db.fetchone(
                "SELECT * FROM queries WHERE query_id = ?",
                (query_id,)
            )

            if not query_data:
                return web.json_response({'error': 'Query not found'}, status=404)

            # 2. Get Pipeline Stats (Counts)
            row = await self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM retrieved_documents WHERE query_id = ?",
                (query_id,)
            )
            retrieved_count = row['cnt'] if row else 0

            row = await self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM ranking_scores WHERE query_id = ?",
                (query_id,)
            )
            ranked_count = row['cnt'] if row else 0

            # 3. Get Top K Ranked Documents
            ranking_scores = await self.db.fetchall(
                f"""
                SELECT
                    doc_url,
                    MAX(llm_final_score) as llm_final_score,
                    MAX(llm_snippet) as llm_snippet,
                    MAX(mmr_diversity_score) as mmr_diversity_score,
                    MAX(xgboost_score) as xgboost_score,
                    MAX(final_ranking_score) as final_ranking_score
                FROM ranking_scores
                WHERE query_id = ?
                GROUP BY doc_url
                ORDER BY final_ranking_score DESC
                LIMIT {limit}
                """,
                (query_id,)
            )

            body = {
                'query': query_data,
                'stats': {
                    'retrieved_count': retrieved_count,
                    'ranked_count': ranked_count,
                    'returned_count': query_data.get('num_results_returned', 0)
                },
                'top_results': ranking_scores
            }

            resp = web.json_response(body)
            resp.headers['Cache-Control'] = 'no-store'
            return resp

        except Exception as e:
            logger.error(f"Error getting pipeline details: {e}")
            return web.json_response({'error': str(e)}, status=500)


def register_ranking_analytics_routes(app: web.Application, db_path: str = None):
    """
    Register ranking analytics routes with the aiohttp application.

    Args:
        app: aiohttp Application instance
        db_path: Path to SQLite database. If None, uses absolute path from project root.
    """
    handler = RankingAnalyticsHandler(db_path)

    app.router.add_get('/api/ranking/config', handler.get_config)
    app.router.add_get('/api/ranking/pipeline/{query_id}', handler.get_pipeline_details)

    logger.info("Registered ranking analytics routes")
