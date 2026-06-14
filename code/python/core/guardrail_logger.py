# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
GuardrailLogger — Defense event logging for guardrail system.

Writes all defense events (rate_limit, query_sanitized, concurrency_limit,
injection_detected, pii_filtered) to the guardrail_events table.

Singleton pattern, reuses AnalyticsDB for DB access.
Fire-and-forget: log_event never raises; errors go to Python logger only.
"""

import json
import time
import logging
from typing import Optional

from core.analytics_db import AnalyticsDB

logger = logging.getLogger(__name__)


class GuardrailLogger:
    """
    Singleton logger for guardrail defense events.

    Usage:
        gl = GuardrailLogger.get_instance()
        await gl.log_event('rate_limit', 'warning', user_id='u123', client_ip='1.2.3.4',
                           details={'reason': 'DR concurrency exceeded'})
    """

    _instance: Optional['GuardrailLogger'] = None

    @classmethod
    def get_instance(cls) -> 'GuardrailLogger':
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def log_event(
        self,
        event_type: str,
        severity: str,
        user_id: str = None,
        client_ip: str = None,
        details: dict = None,
    ) -> None:
        """
        Insert one row into guardrail_events. Fire-and-forget.

        Args:
            event_type: One of 'rate_limit', 'query_sanitized', 'concurrency_limit',
                        'injection_detected', 'pii_filtered'
            severity:   One of 'info', 'warning', 'critical'
            user_id:    Authenticated user ID (nullable)
            client_ip:  Client IP address (nullable)
            details:    JSON-serializable dict with event details (nullable)
        """
        try:
            db = AnalyticsDB.get_instance()
            details_json = json.dumps(details, ensure_ascii=False) if details is not None else None

            sql = """
                INSERT INTO guardrail_events
                    (timestamp, event_type, severity, user_id, client_ip, details, schema_version)
                VALUES
                    (?, ?, ?, ?, ?, ?, 2)
            """
            await db.execute(sql, (time.time(), event_type, severity, user_id, client_ip, details_json))
        except Exception as e:
            # Never raise — guardrail logging must not break the request path
            logger.error(
                f"GuardrailLogger.log_event failed (event_type={event_type}, severity={severity}): {e}",
                exc_info=True,
            )

    async def get_recent_events(
        self,
        minutes: int = 10,
        event_type: str = None,
        client_ip: str = None,
    ) -> list:
        """
        Query recent guardrail events for alert rule evaluation.

        Args:
            minutes:    Look-back window in minutes (default 10)
            event_type: Filter by event_type (optional)
            client_ip:  Filter by client_ip (optional)

        Returns:
            List of dicts, each representing one guardrail_events row.
            Returns [] on any error.
        """
        try:
            db = AnalyticsDB.get_instance()
            since = time.time() - (minutes * 60)

            conditions = ["timestamp >= ?"]
            params: list = [since]

            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)

            if client_ip is not None:
                conditions.append("client_ip = ?")
                params.append(client_ip)

            where_clause = " AND ".join(conditions)
            sql = f"SELECT * FROM guardrail_events WHERE {where_clause} ORDER BY timestamp DESC"

            rows = await db.fetchall(sql, tuple(params))
            return rows
        except Exception as e:
            logger.error(
                f"GuardrailLogger.get_recent_events failed (minutes={minutes}): {e}",
                exc_info=True,
            )
            return []
