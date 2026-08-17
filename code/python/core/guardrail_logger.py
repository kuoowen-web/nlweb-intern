# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""GuardrailLogger — Defense & quality-signal event logging.

Writes events where the system **detected an anomaly and took action** to the
guardrail_events table. Two families share this table:
  - Defense（原始用途）: rate_limit, query_sanitized, concurrency_limit,
    injection_detected, pii_filtered
  - Quality signal（票 2026-08-01-k 起）: llm_numeric_coerce_failed
    —— 上游（LLM）產出 schema 沒宣告的形態，系統移除該欄位以自保。
    寫入端見 core/llm_coerce_signal.py，常數見該檔 COERCE_EVENT_TYPE。

⚠ 查「防禦指標」時要**依 event_type 過濾**，不要對整表做 count——兩個 family 的
語義不同（防禦是「擋掉了攻擊/濫用」，品質訊號是「上游品質退化」）。

Singleton pattern, reuses AnalyticsDB for DB access.
Fire-and-forget: log_event never raises; errors go to Python logger only.
"""

import json
import time
from typing import Optional

from core.analytics_db import AnalyticsDB
from misc.logger.logging_config_helper import get_configured_logger

# 票 2026-08-04-e（K-73 更正版）：stdlib 零 handler logger 的 ERROR 仍會走
# logging.lastResort stderr（訊息不消失），但不進結構化 log、無人巡檢。
# 改配置化 logger；log_event 的 never-raise 契約不變。
logger = get_configured_logger("guardrail_logger")


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
            event_type: Defense: 'rate_limit', 'query_sanitized', 'concurrency_limit',
                        'injection_detected', 'pii_filtered'.
                        Quality signal: 'llm_numeric_coerce_failed'（見
                        core/llm_coerce_signal.COERCE_EVENT_TYPE）。
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
            # 🔧R1（in-house nit-2）：LazyLogger.error 是 async worker enqueue，
            # exc_info=True 在 worker thread 解析時已無 traceback；.exception 在
            # call site 就捕 sys.exc_info()。
            logger.exception(
                f"GuardrailLogger.log_event failed (event_type={event_type}, severity={severity}): {e}",
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
            logger.exception(
                f"GuardrailLogger.get_recent_events failed (minutes={minutes}): {e}",
            )
            return []
