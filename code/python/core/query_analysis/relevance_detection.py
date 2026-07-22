# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
This file contains the methods for detecting if the query is irrelevant to the site.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

from core.prompts import PromptRunner
import asyncio
import os
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("relevance_detection")

# Modes: 'log_only' (default), 'enforce'
# Phase 2 starts in 'log_only'. Graduate to 'enforce' after 1 week + 500 queries + <2% FP rate.
RELEVANCE_DETECTION_MODE = os.environ.get('GUARDRAIL_RELEVANCE_MODE', 'log_only')


class RelevanceDetection(PromptRunner):

    RELEVANCE_PROMPT_NAME = "DetectIrrelevantQueryPrompt"
    STEP_NAME = "Relevance"

    def __init__(self, handler):
        super().__init__(handler)
        self.handler.state.start_precheck_step(self.STEP_NAME)

    async def do(self):
        if (self.handler.site == 'all' or self.handler.site == 'nlws'):
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return
        # CORE-5 (full-scan 批7) try/finally：LLM 回缺 key（CORE-4）或 run_prompt 拋錯，
        # finally 都保證 precheck_step_done("Relevance") 被呼叫，避免 all(DONE) 永假 →
        # pre_checks_done_event 永不 set。enforce 模式漏擋亦被 fail-open 預設兜住。
        try:
            response = await self.run_prompt(self.RELEVANCE_PROMPT_NAME, level="high")
            if (not response):
                return
            # CORE-4：不裸取 → 缺 key 時 fail-open 預設「相關」（不擋 query），並 log。
            self.site_is_irrelevant_to_query = response.get("site_is_irrelevant_to_query")
            self.explanation_for_irrelevance = response.get("explanation_for_irrelevance", "")
            if self.site_is_irrelevant_to_query is None:
                logger.warning(
                    "[Relevance] response missing 'site_is_irrelevant_to_query'; "
                    "fail-open (treat as relevant, do not block query)"
                )
                self.handler.query_is_irrelevant = False
                return
            if (self.site_is_irrelevant_to_query == "True"):
                if RELEVANCE_DETECTION_MODE == 'enforce':
                    # G3 (SSE typed pipeline Task 2): reroute site_is_irrelevant_to_query ->
                    # empty_results so enforce mode surfaces a visible zh-TW notice instead of
                    # a silent blank (frontend had 0 handler for site_is_irrelevant_to_query).
                    message = {"message_type": "empty_results", "content": "這個站台似乎沒有與您問題相關的內容，建議換個關鍵字或改到其他站台查詢。"}
                    self.handler.query_is_irrelevant = True
                    self.handler.query_done = True
                    asyncio.create_task(self.handler.send_message(message))
                else:
                    # log_only mode: log the detection but do NOT block the query
                    self.handler.query_is_irrelevant = False
                    from core.guardrail_logger import GuardrailLogger
                    asyncio.create_task(GuardrailLogger.get_instance().log_event(
                        event_type='relevance_detected',
                        severity='info',
                        user_id=getattr(self.handler, 'user_id', None),
                        client_ip=getattr(self.handler, 'client_ip', None),
                        details={
                            'query': self.handler.query,
                            'site': self.handler.site,
                            'explanation': self.explanation_for_irrelevance,
                            'mode': RELEVANCE_DETECTION_MODE,
                        },
                    ))
            else:
                self.handler.query_is_irrelevant = False
        finally:
            if not self.handler.state.is_precheck_step_done(self.STEP_NAME):
                await self.handler.state.precheck_step_done(self.STEP_NAME)
