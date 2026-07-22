"""
OrchestratorBase - Shared base class for research orchestrators.

Provides common SSE push, connection checking, and session setup
logic used by both DeepResearchOrchestrator and LiveResearchOrchestrator.
"""

from misc.logger.logging_config_helper import get_configured_logger
from core.config import CONFIG


logger = get_configured_logger("reasoning.orchestrator_base")


class ResearchCancelledError(Exception):
    """Raised when client disconnects during research."""
    pass


class ProgressConfig:
    """進度條配置，用於SSE串流。"""

    STAGES = {
        "analyst_analyzing": {
            "weight": 0.3,
            "message": "正在深度分析資料來源...",
        },
        "analyst_complete": {
            "weight": 0.5,
            "message": "分析完成，開始品質審查",
        },
        "critic_reviewing": {
            "weight": 0.6,
            "message": "正在檢查邏輯與來源可信度...",
        },
        "cov_verifying": {
            "weight": 0.65,
            "message": "正在驗證事實宣稱...",
        },
        "cov_complete": {
            "weight": 0.75,
            "message": "事實驗證完成",
        },
        "critic_complete": {
            "weight": 0.8,
            "message": "審查完成",
        },
        "writer_planning": {
            "weight": 0.82,
            "message": "正在規劃報告結構...",
        },
        "writer_composing": {
            "weight": 0.85,
            "message": "正在撰寫最終報告...",
        },
        "writer_complete": {
            "weight": 1.0,
            "message": "報告生成完成",
        },
        "gap_search_started": {
            "weight": 0.55,
            "message": "偵測到資訊缺口，正在補充搜尋...",
        },
        "analyst_integrating_new_data": {
            "weight": 0.58,
            "message": "整合新資料中，重新分析...",
        }
    }

    @staticmethod
    def calculate_progress(stage: str, iteration: int, total_iterations: int) -> int:
        """計算給定stage的進度百分比。"""
        if total_iterations <= 0:
            total_iterations = 1
        stage_info = ProgressConfig.STAGES.get(stage, {"weight": 0.5})
        base = int((iteration - 1) / total_iterations * 100)
        offset = int(stage_info["weight"] * (100 / total_iterations))
        return min(base + offset, 100)


class OrchestratorBase:
    """
    Abstract-ish base class for research orchestrators.

    Provides shared infrastructure:
    - SSE progress pushing (_send_progress, _emit_phase_event)
    - Connection alive checking (_check_connection)
    - Research session initialization (_setup_research_session)

    Subclasses must call super().__init__(handler) and may add
    their own domain-specific attributes after that.
    """

    def __init__(self, handler):
        """
        Initialize base orchestrator.

        Args:
            handler: Request handler with LLM configuration, message_sender,
                     connection_alive_event, etc.
        """
        self.handler = handler
        self.logger = get_configured_logger("reasoning.orchestrator_base")

    async def _send_progress(self, message: dict) -> None:
        """
        Enhanced progress with user-friendly messages.

        Progress messages are sent to frontend to show real-time updates
        during the research loop. Failures are logged but don't interrupt execution.

        Args:
            message: Progress message dict with message_type, stage, etc.
        """
        # Add user-friendly message based on stage (using ProgressConfig)
        if CONFIG.reasoning_params.get("features", {}).get("user_friendly_sse", False):
            stage = message.get("stage", "")
            iteration = message.get("iteration", 1)
            total = message.get("total_iterations", 3)

            # Use configuration class instead of hardcoded dict
            stage_info = ProgressConfig.STAGES.get(stage)
            if stage_info:
                message["user_message"] = stage_info["message"]
                message["progress"] = ProgressConfig.calculate_progress(stage, iteration, total)

        # Task 12 (B2 dependency direction): delegate send + disconnect-raise to
        # send_sse(path="progress"). The mutate above STAYS here (caller-side).
        # send_sse replicates: hasattr(handler,'message_sender') guard, send via
        # message_sender.send_message, swallow send exception as warning, then
        # detect disconnect and raise the injected on_disconnect factory. We inject
        # ResearchCancelledError so core/sse/send.py never imports reasoning.
        from core.sse.send import send_sse
        await send_sse(
            self.handler, message, path="progress",
            on_disconnect=lambda: ResearchCancelledError(
                "Client disconnected (detected in send_sse progress)"),
        )

    async def _emit_phase_event(self, phase_name: str, status: str):
        """Push phase progress event to frontend via SSE.

        Used by composable phase methods to report phase boundaries.
        Frontend can use these events to show research progress indicators.

        Args:
            phase_name: Name of the phase (e.g., "filter_and_prepare", "actor_critic_loop")
            status: Either "started" or "completed"
        """
        await self._send_progress({
            "message_type": "research_phase",
            "phase": phase_name,
            "status": status,
        })

    def _check_connection(self):
        """Check if client is still connected; raise ResearchCancelledError if not.

        Also checks the soft interrupt event (Task 6), which allows user-typing
        interrupts during research phases. Uses getattr for backward compatibility
        with handlers that don't have _soft_interrupt_event.
        """
        wrapper = getattr(self.handler, 'http_handler', None)
        event = getattr(self.handler, 'connection_alive_event', None)

        # Check wrapper's connection_alive flag
        if wrapper and not wrapper.connection_alive:
            # Bridge: also clear the event so downstream checks work
            if event and event.is_set():
                event.clear()
            raise ResearchCancelledError("Client disconnected (wrapper)")

        # Check handler's connection_alive_event
        if event and not event.is_set():
            raise ResearchCancelledError("Client disconnected (event)")

        # Task 6: Soft interrupt check (user typing during research)
        soft_interrupt = getattr(self.handler, '_soft_interrupt_event', None)
        if soft_interrupt and soft_interrupt.is_set():
            raise ResearchCancelledError("User interrupted (soft)")

    def _setup_research_session(
        self,
        query_id: str,
        query: str,
        mode: str,
        items: list,
        enable_web_search: bool = False,
    ):
        """
        Initialize logging and tracing for research session.

        Returns:
            Tuple of (iteration_logger, tracer)
        """
        from reasoning.utils.iteration_logger import IterationLogger

        # Initialize iteration logger
        iteration_logger = IterationLogger(query_id)

        # Initialize console tracer
        tracer = None
        tracing_config = CONFIG.reasoning_params.get("tracing", {})
        if tracing_config.get("console", {}).get("enabled", True):
            import os
            verbosity = os.getenv("REASONING_TRACE_LEVEL") or \
                        tracing_config.get("console", {}).get("level", "DEBUG")
            from reasoning.utils.console_tracer import ConsoleTracer
            tracer = ConsoleTracer(query_id=query_id, verbosity=verbosity)

        self.logger.info(
            f"Starting research session: query='{query}', mode={mode}, "
            f"items={len(items)}, enable_web_search={enable_web_search}"
        )

        # Tracing: Research start
        if tracer:
            tracer.start_research(query=query, mode=mode, items=items)

        return iteration_logger, tracer
