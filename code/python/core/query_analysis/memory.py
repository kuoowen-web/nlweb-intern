# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
This file is used to analyze the query to see if it mentions anything that
should go into the long term memory for the user.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

from core.prompts import PromptRunner
import asyncio
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("memory")


class Memory(PromptRunner):

    MEMORY_PROMPT_NAME = "DetectMemoryRequestPrompt"
    STEP_NAME = "Memory"
    
    def __init__(self, handler):
        super().__init__(handler)
        self.handler.state.start_precheck_step(self.STEP_NAME)

    async def do(self):
        if not CONFIG.is_memory_enabled():
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            logger.info("Memory is disabled in config, skipping")
            return
        # CORE-5 (full-scan 批7) try/finally：LLM 回缺 key（CORE-4）或 run_prompt 拋錯，
        # finally 都保證 precheck_step_done("Memory") 被呼叫，避免「記住」功能靜默死
        # 又拖垮 all(DONE)。fail-open：缺 key 視為非記憶請求，不阻塞。
        try:
            response = await self.run_prompt(self.MEMORY_PROMPT_NAME, level="high")
            if (not response):
                logger.warning("No response from DetectMemoryRequestPrompt, skipping memory step")
                return
            # CORE-4：不裸取 → 缺 key 時 fail-open 預設非記憶請求，並 log。
            self.is_memory_request = response.get("is_memory_request")
            self.memory_request = response.get("memory_request", "")
            if self.is_memory_request is None:
                logger.warning(
                    "[Memory] response missing 'is_memory_request'; "
                    "treating as non-memory request (fail-open)"
                )
                return
            if (self.is_memory_request == "True"):
                # this is where we would write to a database
                logger.debug(f"writing memory request: {self.memory_request}")
                message = {"message_type": "remember", "item_to_remember": self.memory_request, "content": "I'll remember that"}
                asyncio.create_task(self.handler.send_message(message))
            else:
                logger.info("Memory not required")
        finally:
            if not self.handler.state.is_precheck_step_done(self.STEP_NAME):
                await self.handler.state.precheck_step_done(self.STEP_NAME)
