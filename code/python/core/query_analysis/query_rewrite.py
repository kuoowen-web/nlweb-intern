# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Query expansion for news search: expands vague/abstract queries into concrete
news-oriented search phrases to improve recall. Skips expansion for already-specific queries.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

from core.prompts import PromptRunner
import asyncio
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("query_rewrite")


class QueryRewrite(PromptRunner):
    
    QUERY_REWRITE_PROMPT_NAME = "QueryRewrite"
    STEP_NAME = "QueryRewrite"
    
    def __init__(self, handler):
        super().__init__(handler)
        self.handler.state.start_precheck_step(self.STEP_NAME)
        
    async def do(self):
        """
        Expand the query into concrete news-oriented search phrases.

        Sets on handler:
          - rewritten_queries: list of expansion queries (empty list if no expansion needed)
          - needs_query_expansion: bool indicating whether LLM deemed expansion useful

        The original query is always searched separately by the retriever.
        rewritten_queries contains ONLY the additional expansion queries.
        """
        # Wait for decontextualization to complete since we need the decontextualized query
        await self.handler.state._decon_event.wait()

        logger.info(f"Starting query expansion for: {self.handler.decontextualized_query}")

        try:
            response = await self.run_prompt(self.QUERY_REWRITE_PROMPT_NAME, level="high")

            if not response:
                logger.warning("No response from QueryRewrite prompt, skipping expansion")
                self.handler.rewritten_queries = []
                self.handler.needs_query_expansion = False
                await self.handler.state.precheck_step_done(self.STEP_NAME)
                return

            needs_expansion = str(response.get("needs_expansion", "false")).lower() == "true"
            self.handler.needs_query_expansion = needs_expansion

            if not needs_expansion:
                logger.info("LLM determined query is already specific, skipping expansion")
                self.handler.rewritten_queries = []
            else:
                rewritten_queries = response.get("rewritten_queries", [])

                if not rewritten_queries or not isinstance(rewritten_queries, list):
                    logger.warning("needs_expansion=true but no valid queries returned")
                    self.handler.rewritten_queries = []
                else:
                    valid_queries = [q for q in rewritten_queries if q and isinstance(q, str) and q.strip()]
                    # Limit to 4 expansion queries maximum
                    self.handler.rewritten_queries = valid_queries[:4]
                    logger.info(f"Generated {len(self.handler.rewritten_queries)} expansion queries: {self.handler.rewritten_queries}")

            # Notify the client about expansion queries
            if self.handler.rewritten_queries:
                message = {
                    "message_type": "query_rewrite",
                    "original_query": self.handler.decontextualized_query,
                    "rewritten_queries": self.handler.rewritten_queries,
                    "needs_expansion": needs_expansion,
                }
                asyncio.create_task(self.handler.send_message(message))

        except Exception as e:
            logger.error(f"Error during query expansion: {e}")
            self.handler.rewritten_queries = []
            self.handler.needs_query_expansion = False

        finally:
            # Always mark the step as done
            await self.handler.state.precheck_step_done(self.STEP_NAME)