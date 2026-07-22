# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
This file contains the classes for the different levels of decontextualization. 

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import json

import core.retriever as retriever
from core.prompts import PromptRunner
from core.utils.json_utils import trim_json
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("decontextualizer")

class NoOpDecontextualizer(PromptRunner):
  
    DECONTEXTUALIZE_QUERY_PROMPT_NAME = "NoOpDecontextualizer"
    STEP_NAME = "Decon"

    def __init__(self, handler):
        super().__init__(handler)
        self.handler.state.start_precheck_step(self.STEP_NAME)
    
    async def do(self):
        # Check if decontextualization is enabled in config
        if not CONFIG.is_decontextualize_enabled():
            logger.info("Decontextualization is disabled in config, skipping")
            self.handler.decontextualized_query = self.handler.query
            self.handler.requires_decontextualization = False
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return
        
        self.handler.decontextualized_query = self.handler.query
        self.handler.requires_decontextualization = False
        await self.handler.state.precheck_step_done(self.STEP_NAME)
        logger.info("Decontextualization not required")
        return
    
class PrevQueryDecontextualizer(NoOpDecontextualizer):

    DECONTEXTUALIZE_QUERY_PROMPT_NAME = "PrevQueryDecontextualizer"
  
    def __init__(self, handler):
        super().__init__(handler)

    async def do(self):
        # Check if decontextualization is enabled in config
        if not CONFIG.is_decontextualize_enabled():
            logger.info("Decontextualization is disabled in config, skipping")
            self.handler.decontextualized_query = self.handler.query
            self.handler.requires_decontextualization = False
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return

        # CORE-5 (full-scan 批7) try/finally 死鎖防線：無論 run_prompt / key 存取
        # 是否拋例外，finally 都保證 precheck_step_done("Decon") 被呼叫（→ 一併 set
        # _decon_event），使 wait_for_decontextualization() 的 waiter 不會永久阻塞。
        # fail-open 預設：先把 decontextualized_query 設回原 query，任何失敗都退回
        # 「不做 decon」而非阻塞或炸 pipeline。
        self.handler.decontextualized_query = self.handler.query
        self.handler.requires_decontextualization = False
        try:
            response = await self.run_prompt(self.DECONTEXTUALIZE_QUERY_PROMPT_NAME,
                                             level="high", verbose=True)
            logger.info(f"response: {response}")
            if not response:
                logger.info("No response from decontextualizer")
                return
            elif "requires_decontextualization" not in response:
                error_msg = f"Missing 'requires_decontextualization' key in response: {response}"
                logger.error(error_msg)
                if CONFIG.should_raise_exceptions():
                    raise KeyError(f"Decontextualization failed: {error_msg}")
                # Fallback in production mode（fail-open，已在上方預設）
                return
            elif (response["requires_decontextualization"] == "True"):
                self.handler.requires_decontextualization = True
                # CORE-4：decontextualized_query 缺 key 不裸取 → fail-open 保留原 query
                dq = response.get("decontextualized_query")
                if dq:
                    self.handler.decontextualized_query = dq
                else:
                    logger.warning(
                        "[Decon] requires_decontextualization=True but "
                        "'decontextualized_query' missing; keeping original query (fail-open)"
                    )
                # dead-emit removed (decontextualized_query: frontend 0 handler) — SSE typed pipeline Task 2
            else:
                logger.info("No decontextualization required despite previous query")
                # dead-emit removed (decontextualized_query: frontend 0 handler) — SSE typed pipeline Task 2
        finally:
            if not self.handler.state.is_precheck_step_done(self.STEP_NAME):
                await self.handler.state.precheck_step_done(self.STEP_NAME)

class ContextUrlDecontextualizer(PrevQueryDecontextualizer):
    
    DECONTEXTUALIZE_QUERY_PROMPT_NAME = "DecontextualizeContextPrompt"
     
    def __init__(self, handler):    
        super().__init__(handler)
        self.context_url = handler.context_url
        self.retriever = self.retriever()

    def retriever(self):
        return retriever.DBItemRetriever(self.handler)  

    async def do(self):
        # Check if decontextualization is enabled in config
        if not CONFIG.is_decontextualize_enabled():
            logger.info("Decontextualization is disabled in config, skipping")
            self.handler.decontextualized_query = self.handler.query
            self.handler.requires_decontextualization = False
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return

        # CORE-5 (full-scan 批7) try/finally 死鎖防線 + fail-open 預設（同 PrevQuery）。
        # run_prompt / retriever.do() / decontextualized_query 缺 key 任一拋錯，finally
        # 都保證 precheck_step_done("Decon") 被呼叫，避免 _decon_event 永不 set 死鎖。
        self.handler.decontextualized_query = self.handler.query
        self.handler.requires_decontextualization = False
        try:
            response = await self.run_prompt(self.DECONTEXTUALIZE_QUERY_PROMPT_NAME, level="high", verbose=False)
            if not response:
                return
            await self.retriever.do()
            item = self.retriever.handler.context_item
            if (item is None):
                return
            (url, schema_json, name, site) = item
            self.context_description = json.dumps(trim_json(schema_json))
            self.handler.context_description = self.context_description
            response = await self.run_prompt(self.DECONTEXTUALIZE_QUERY_PROMPT_NAME, verbose=True)
            self.handler.requires_decontextualization = True
            # CORE-4：不裸取 response["decontextualized_query"] → fail-open 保留原 query
            dq = response.get("decontextualized_query") if response else None
            if dq:
                self.handler.decontextualized_query = dq
            else:
                logger.warning(
                    "[Decon/ContextUrl] 'decontextualized_query' missing in response; "
                    "keeping original query (fail-open)"
                )
            # dead-emit removed (decontextualized_query: frontend 0 handler) — SSE typed pipeline Task 2
        finally:
            if not self.handler.state.is_precheck_step_done(self.STEP_NAME):
                await self.handler.state.precheck_step_done(self.STEP_NAME)

class FullDecontextualizer(ContextUrlDecontextualizer):
    
    DECONTEXTUALIZE_QUERY_PROMPT_NAME = "FullDecontextualizePrompt"

    def __init__(self, handler):
       super().__init__(handler)
