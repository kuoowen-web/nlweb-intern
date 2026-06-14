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
        response = await self.run_prompt(self.RELEVANCE_PROMPT_NAME, level="high")
        if (not response):
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return
        self.site_is_irrelevant_to_query = response["site_is_irrelevant_to_query"]
        self.explanation_for_irrelevance = response["explanation_for_irrelevance"]
        if (self.site_is_irrelevant_to_query == "True"):
            if RELEVANCE_DETECTION_MODE == 'enforce':
                message = {"message_type": "site_is_irrelevant_to_query", "message": self.explanation_for_irrelevance}
                self.handler.query_is_irrelevant = True
                self.handler.query_done = True
                # Centralized abort checking will handle setting the event
                self.handler.state.abort_fast_track_if_needed()
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
        await self.handler.state.precheck_step_done(self.STEP_NAME)
