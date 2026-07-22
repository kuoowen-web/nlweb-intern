from core.state import NLWebHandlerState
import asyncio
from core.prompts import PromptRunner
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("post_ranking")


class PostRanking:
    """This class is used to check if any post processing is needed after the ranking is done."""
    
    def __init__(self, handler):
        self.handler = handler

    async def do(self):
        if not self.handler.connection_alive_event.is_set():
            self.handler.query_done = True
            return

        if (self.handler.generate_mode == "none"):
            # nothing to do
            return

        if self.handler.generate_mode == "summarize":
            await SummarizeResults(self.handler).do()
            return
        
       
        
class SummarizeResults(PromptRunner):

    SUMMARIZE_RESULTS_PROMPT_NAME = "SummarizeResultsPrompt"

    def __init__(self, handler):
        super().__init__(handler)

    async def do(self):
        # P1-5 honest guard (2026-07-08): with zero ranked answers there is
        # nothing to summarize — feeding an empty list to the LLM invites it
        # to stitch an answer out of thin air (fabrication inlet). Explicit
        # degradation, never silent. Mirrors the existing empty-response
        # early-return below (no precheck_step_done), so state semantics
        # are unchanged.
        if not getattr(self.handler, 'final_ranked_answers', None):
            logger.warning("[SUMMARIZE] Skipped: no ranked answers to summarize (empty result set)")
            return
        # MMR diversity re-ranking is already done in ranking.py, no need to apply again
        response = await self.run_prompt(self.SUMMARIZE_RESULTS_PROMPT_NAME, timeout=20, max_length=1024)
        if (not response):
            return
        # CORE-4 (full-scan 批7)：不裸取 response["summary"]。缺 key → 無摘要可送，
        # 早退不炸（比照上方 empty-response early-return 語意），並 log。
        summary = response.get("summary")
        if not summary:
            logger.warning("[SUMMARIZE] response missing 'summary'; skipping summary emit")
            return
        self.handler.summary = summary
        # msg_type discriminator stays here (caller-side); send goes through
        # send_sse(path="full") -> message_sender.send_message.
        from core.sse.send import send_sse  # local import: avoid load cycle
        msg_type = "summary" if self.handler.generate_mode == 'unified' else "result"
        message = {"message_type": msg_type, "@type": "Summary", "content": self.handler.summary}
        if self.handler.generate_mode == 'unified':
            # Await for ordering guarantee in unified mode
            await send_sse(self.handler, message, path="full")
        else:
            asyncio.create_task(send_sse(self.handler, message, path="full"))
        # Use proper state update
        await self.handler.state.precheck_step_done("post_ranking")
