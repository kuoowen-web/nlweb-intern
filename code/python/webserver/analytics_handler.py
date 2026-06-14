# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Analytics API Handler for NLWeb

Provides REST API endpoints for the analytics dashboard to query
training data and usage statistics.

Supports both SQLite (local) and PostgreSQL (production).

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import json
import csv
import io
import os
from aiohttp import web
from pathlib import Path
import time
from misc.logger.logging_config_helper import get_configured_logger
from core.analytics_db import AnalyticsDB

logger = get_configured_logger("analytics_handler")


class AnalyticsHandler:
    """
    Handles HTTP requests for analytics data.
    """

    def __init__(self, db_path: str = None):
        """
        Initialize the analytics handler.

        Args:
            db_path: Ignored; always uses the shared singleton AnalyticsDB instance.
                     Kept for API compatibility with register_analytics_routes callers.
        """
        # Always use the shared singleton instance to avoid multiple connection pools
        self.db = AnalyticsDB.get_instance()
        logger.info(f"Analytics handler initialized with {self.db.db_type} database")

    async def get_stats(self, request: web.Request) -> web.Response:
        """
        Get overall statistics.

        Query params:
            days: Number of days to look back (default: 7)
        """
        try:
            days = int(request.query.get('days', 7))
            cutoff_timestamp = time.time() - (days * 24 * 60 * 60)

            def get_val(result):
                """Extract scalar value from fetchone() dict result."""
                if result is None:
                    return 0
                return list(result.values())[0] or 0

            # Total queries
            row = await self.db.fetchone(
                "SELECT COUNT(*) FROM queries WHERE timestamp > ?",
                (cutoff_timestamp,)
            )
            total_queries = get_val(row)

            queries_per_day = total_queries / days if days > 0 else 0

            # Average latency
            row = await self.db.fetchone(
                "SELECT AVG(latency_total_ms) FROM queries WHERE timestamp > ? AND latency_total_ms IS NOT NULL",
                (cutoff_timestamp,)
            )
            avg_latency = get_val(row)

            # Total cost
            row = await self.db.fetchone(
                "SELECT SUM(cost_usd) FROM queries WHERE timestamp > ? AND cost_usd IS NOT NULL",
                (cutoff_timestamp,)
            )
            total_cost = get_val(row)

            cost_per_query = total_cost / total_queries if total_queries > 0 else 0

            # Error rate
            row = await self.db.fetchone(
                "SELECT COUNT(*) FROM queries WHERE timestamp > ? AND error_occurred = 1",
                (cutoff_timestamp,)
            )
            error_count = get_val(row)
            error_rate = error_count / total_queries if total_queries > 0 else 0

            # Click-through rate
            row = await self.db.fetchone(
                "SELECT COUNT(DISTINCT query_id) FROM user_interactions WHERE interaction_timestamp > ? AND clicked = 1",
                (cutoff_timestamp,)
            )
            queries_with_clicks = get_val(row)
            ctr = queries_with_clicks / total_queries if total_queries > 0 else 0

            # Training samples
            row = await self.db.fetchone(
                "SELECT COUNT(*) FROM retrieved_documents WHERE query_id IN (SELECT query_id FROM queries WHERE timestamp > ?)",
                (cutoff_timestamp,)
            )
            training_samples = get_val(row)

            stats = {
                "total_queries": total_queries,
                "queries_per_day": queries_per_day,
                "avg_latency_ms": avg_latency,
                "total_cost_usd": total_cost,
                "cost_per_query": cost_per_query,
                "error_rate": error_rate,
                "click_through_rate": ctr,
                "training_samples": training_samples,
                "days": days
            }

            response = web.json_response(stats)
            response.headers['Cache-Control'] = 'no-store'
            return response

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def get_queries(self, request: web.Request) -> web.Response:
        """
        Get recent queries with metrics.

        Query params:
            days: Number of days to look back (default: 7)
            limit: Maximum number of queries to return (default: 50)
        """
        try:
            days = int(request.query.get('days', 7))
            limit = int(request.query.get('limit', 50))
            cutoff_timestamp = time.time() - (days * 24 * 60 * 60)

            rows = await self.db.fetchall(
                """
                SELECT
                    q.query_id,
                    q.query_text,
                    q.timestamp,
                    q.site,
                    q.mode,
                    q.latency_total_ms,
                    q.num_results_returned,
                    q.cost_usd,
                    (SELECT COUNT(*) FROM user_interactions
                     WHERE query_id = q.query_id AND clicked = 1) as clicks
                FROM queries q
                WHERE q.timestamp > ? AND q.parent_query_id IS NULL
                ORDER BY q.timestamp DESC
                LIMIT ?
                """,
                (cutoff_timestamp, limit)
            )

            queries = []
            for row in rows:
                num_results = int(row['num_results_returned']) if row['num_results_returned'] is not None else 0
                clicks = int(row['clicks']) if row['clicks'] is not None else 0
                ctr = clicks / num_results if num_results > 0 else 0

                queries.append({
                    "query_id": row['query_id'],
                    "query_text": row['query_text'],
                    "timestamp": row['timestamp'],
                    "site": row['site'],
                    "mode": row['mode'],
                    "latency_total_ms": row['latency_total_ms'],
                    "num_results_returned": num_results,
                    "cost_usd": row['cost_usd'],
                    "clicks": clicks,
                    "ctr": ctr
                })

            response = web.json_response(queries)
            response.headers['Cache-Control'] = 'no-store'
            return response

        except Exception as e:
            logger.error(f"Error getting queries: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def get_top_clicks(self, request: web.Request) -> web.Response:
        """
        Get top clicked results.

        Query params:
            days: Number of days to look back (default: 7)
            limit: Maximum number of results to return (default: 20)
        """
        try:
            days = int(request.query.get('days', 7))
            limit = int(request.query.get('limit', 20))
            cutoff_timestamp = time.time() - (days * 24 * 60 * 60)

            rows = await self.db.fetchall(
                """
                SELECT
                    ui.doc_url,
                    rd.doc_title,
                    COUNT(*) as click_count,
                    AVG(ui.result_position) as avg_position,
                    AVG(ui.dwell_time_ms) as avg_dwell_time
                FROM user_interactions ui
                LEFT JOIN retrieved_documents rd ON ui.doc_url = rd.doc_url AND ui.query_id = rd.query_id
                WHERE ui.clicked = 1
                  AND ui.interaction_timestamp > ?
                GROUP BY ui.doc_url, rd.doc_title
                ORDER BY click_count DESC
                LIMIT ?
                """,
                (cutoff_timestamp, limit)
            )

            clicks = []
            for row in rows:
                clicks.append({
                    "doc_url": row['doc_url'],
                    "doc_title": row['doc_title'],
                    "click_count": int(row['click_count']) if row['click_count'] else 0,
                    "avg_position": float(row['avg_position']) if row['avg_position'] else None,
                    "avg_dwell_time": float(row['avg_dwell_time']) if row['avg_dwell_time'] else None
                })

            response = web.json_response(clicks)
            response.headers['Cache-Control'] = 'no-store'
            return response

        except Exception as e:
            logger.error(f"Error getting top clicks: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def export_training_data(self, request: web.Request) -> web.Response:
        """
        Export training data as CSV from raw logs.

        Phase 1: Exports raw interaction data from 4 tables (queries, retrieved_documents,
        ranking_scores, user_interactions) for ML model training.

        Query params:
            days: Number of days to look back (default: 7)
        """
        try:
            days = int(request.query.get('days', 7))
            cutoff_timestamp = time.time() - (days * 24 * 60 * 60)

            logger.info(f"Export: Using {self.db.db_type} database")

            rows = await self.db.fetchall(
                """
                SELECT
                    q.query_id,
                    q.query_text,
                    q.query_length_words,
                    q.query_length_chars,
                    q.has_temporal_indicator,
                    rd.doc_url,
                    rd.doc_title,
                    rd.doc_length,
                    rd.title_exact_match,
                    rd.desc_exact_match,
                    rd.keyword_overlap_ratio,
                    rd.recency_days,
                    rd.has_author,
                    rd.vector_similarity_score,
                    rd.keyword_boost_score,
                    rd.bm25_score,
                    rd.final_retrieval_score,
                    rd.retrieval_position,
                    rd.retrieval_algorithm,
                    rs.llm_final_score,
                    rs.relative_score,
                    rs.score_percentile,
                    rs.ranking_position,
                    rs.ranking_method,
                    CASE WHEN ui.clicked = 1 THEN 1 ELSE 0 END as clicked,
                    COALESCE(ui.dwell_time_ms, 0) as dwell_time_ms,
                    q.mode,
                    q.latency_total_ms,
                    q.schema_version
                FROM queries q
                LEFT JOIN retrieved_documents rd ON q.query_id = rd.query_id
                LEFT JOIN ranking_scores rs ON q.query_id = rs.query_id AND rd.doc_url = rs.doc_url
                LEFT JOIN user_interactions ui ON q.query_id = ui.query_id AND rd.doc_url = ui.doc_url
                WHERE q.timestamp > ? AND rd.doc_url IS NOT NULL
                ORDER BY q.timestamp DESC, rd.retrieval_position ASC
                """,
                (cutoff_timestamp,)
            )

            headers = [
                'query_id', 'query_text', 'query_length_words', 'query_length_chars', 'has_temporal_indicator',
                'doc_url', 'doc_title', 'doc_length', 'title_exact_match', 'desc_exact_match',
                'keyword_overlap_ratio', 'recency_days', 'has_author',
                'vector_similarity_score', 'keyword_boost_score', 'bm25_score', 'final_retrieval_score',
                'retrieval_position', 'retrieval_algorithm',
                'llm_final_score', 'relative_score', 'score_percentile', 'ranking_position', 'ranking_method',
                'clicked', 'dwell_time_ms', 'mode', 'latency_total_ms', 'schema_version'
            ]

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(headers)

            for row in rows:
                writer.writerow([row.get(header) for header in headers])

            # Add UTF-8 BOM for proper Chinese character display in Excel
            csv_data = '\ufeff' + output.getvalue()
            output.close()

            return web.Response(
                body=csv_data.encode('utf-8'),
                content_type='text/csv',
                charset='utf-8',
                headers={
                    'Content-Disposition': f'attachment; filename="training_data_{int(time.time())}.csv"',
                    'Cache-Control': 'no-store'
                }
            )

        except Exception as e:
            import traceback
            logger.error(f"Error exporting training data: {e}")
            logger.error(traceback.format_exc())
            return web.json_response({"error": str(e)}, status=500)

    async def handle_analytics_event(self, request: web.Request) -> web.Response:
        """
        Handle single analytics event from frontend.

        POST body:
        {
            "type": "analytics_event",
            "event_type": "result_clicked",
            "timestamp": 1234567890,
            "data": { ... }
        }
        """
        try:
            event = await request.json()
            event_type = event.get('event_type')
            data = event.get('data', {})

            logger.debug(f"Received analytics event: {event_type}")

            from core.query_logger import get_query_logger
            query_logger = get_query_logger()

            # Extract authenticated user info for B2B analytics
            auth_user = request.get('user') or {}
            req_user_id = auth_user.get('id') if auth_user.get('authenticated') else None
            req_org_id = auth_user.get('org_id') if auth_user.get('authenticated') else None

            if event_type == 'query_start':
                pass

            elif event_type == 'result_displayed':
                pass

            elif event_type == 'result_clicked':
                query_id = data.get('query_id')
                doc_url = data.get('doc_url')

                query_logger.log_user_interaction(
                    query_id=query_id,
                    doc_url=doc_url,
                    interaction_type='click',
                    result_position=data.get('result_position', 0),
                    clicked=True,
                    client_user_agent=data.get('client_user_agent', ''),
                    client_ip_hash=self._hash_ip(request),
                    user_id=req_user_id,
                    org_id=req_org_id,
                )

            elif event_type == 'dwell_time':
                query_logger.log_user_interaction(
                    query_id=data.get('query_id'),
                    doc_url=data.get('doc_url'),
                    interaction_type='dwell',
                    result_position=data.get('result_position', 0),
                    dwell_time_ms=data.get('dwell_time_ms', 0),
                    scroll_depth_percent=data.get('scroll_depth_percent', 0),
                    user_id=req_user_id,
                    org_id=req_org_id,
                )

            response = web.json_response({"status": "ok"})
            response.headers['Cache-Control'] = 'no-store'
            return response

        except Exception as e:
            logger.error(f"Error handling analytics event: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_analytics_batch(self, request: web.Request) -> web.Response:
        """
        Handle batch of analytics events from frontend.

        POST body:
        {
            "events": [ {...}, {...}, ... ]
        }
        """
        try:
            body = await request.json()
            events = body.get('events', [])

            logger.debug(f"Received batch of {len(events)} analytics events")

            from core.query_logger import get_query_logger
            query_logger = get_query_logger()

            # Extract authenticated user info for B2B analytics
            auth_user = request.get('user') or {}
            batch_user_id = auth_user.get('id') if auth_user.get('authenticated') else None
            batch_org_id = auth_user.get('org_id') if auth_user.get('authenticated') else None

            for event in events:
                event_type = event.get('event_type')
                data = event.get('data', {})

                try:
                    if event_type == 'result_displayed':
                        pass

                    elif event_type == 'result_clicked':
                        query_logger.log_user_interaction(
                            query_id=data.get('query_id'),
                            doc_url=data.get('doc_url'),
                            interaction_type='click',
                            result_position=data.get('result_position', 0),
                            clicked=True,
                            client_user_agent=data.get('client_user_agent', ''),
                            client_ip_hash=self._hash_ip(request),
                            user_id=batch_user_id,
                            org_id=batch_org_id,
                        )

                    elif event_type == 'dwell_time':
                        query_logger.log_user_interaction(
                            query_id=data.get('query_id'),
                            doc_url=data.get('doc_url'),
                            interaction_type='dwell',
                            dwell_time_ms=data.get('dwell_time_ms', 0),
                            scroll_depth_percent=data.get('scroll_depth_percent', 0),
                            user_id=batch_user_id,
                            org_id=batch_org_id,
                        )

                except Exception as e:
                    logger.error(f"Error processing event in batch: {e}")
                    continue

            response = web.json_response({
                "status": "ok",
                "processed": len(events)
            })
            response.headers['Cache-Control'] = 'no-store'
            return response

        except Exception as e:
            logger.error(f"Error handling analytics batch: {e}")
            return web.json_response({"error": str(e)}, status=500)

    def _hash_ip(self, request: web.Request) -> str:
        """Hash client IP address for privacy."""
        import hashlib

        ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        if not ip:
            peername = request.transport.get_extra_info('peername')
            if peername:
                ip = peername[0]
            else:
                ip = 'unknown'

        salt = os.getenv('ANALYTICS_SALT', 'nlweb-analytics-salt-default')
        hashed = hashlib.sha256(f"{ip}{salt}".encode()).hexdigest()[:16]

        return hashed


def register_analytics_routes(app: web.Application, db_path: str = None):
    """
    Register analytics routes with the aiohttp application.

    Args:
        app: aiohttp Application instance
        db_path: Path to SQLite database. If None, uses absolute path from project root.
    """
    handler = AnalyticsHandler(db_path=db_path)

    app.router.add_get('/api/analytics/stats', handler.get_stats)
    app.router.add_get('/api/analytics/queries', handler.get_queries)
    app.router.add_get('/api/analytics/top_clicks', handler.get_top_clicks)
    app.router.add_get('/api/analytics/export_training_data', handler.export_training_data)

    app.router.add_post('/api/analytics/event', handler.handle_analytics_event)
    app.router.add_post('/api/analytics/event/batch', handler.handle_analytics_batch)

    logger.info("Analytics API routes registered")
