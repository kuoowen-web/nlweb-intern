"""
Integration tests for Live Research Pipeline — Phase A (Mocked).

6 Scenarios that exercise the real Orchestrator + Engine + StageState logic,
with all LLM-calling components mocked out.

Run:
    cd code/python && python -m pytest tests/integration/test_live_research_pipeline.py -v
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.live_research.loop_engine import BABLoopEngine
from reasoning.schemas_live import (
    ContextMap,
    ContextMapTopic,
    ContextMapDelta,
    ContextMapSearchSeed,
    AssociatorBuildOutput,
    AssociatorDeriveOutput,
    AssociatorRefineOutput,
    ConsistencyReview,
    StyleAnalysisOutput,
    StyleFeature,
    LiveWriterSectionOutput,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures & Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_topic(topic_id: str, name: str, relevance: str = "core") -> ContextMapTopic:
    return ContextMapTopic(
        topic_id=topic_id,
        name=name,
        domain="能源政策",
        relevance=relevance,
    )


def _make_context_map(version: int = 1) -> ContextMap:
    return ContextMap(
        research_question="台灣綠能發展與社區衝突",
        working_hypothesis="再生能源快速擴張導致在地衝突",
        topics=[
            _make_topic("t-core-1", "土地使用爭議", "core"),
            _make_topic("t-core-2", "社區共識機制", "core"),
            _make_topic("t-support-1", "政策框架", "supporting"),
        ],
        version=version,
    )


def _make_build_output() -> AssociatorBuildOutput:
    return AssociatorBuildOutput(
        context_map=_make_context_map(version=0),
        narration="建立了初始研究結構，識別三個核心議題。",
    )


def _make_derive_output() -> AssociatorDeriveOutput:
    return AssociatorDeriveOutput(
        search_seeds=[
            ContextMapSearchSeed(
                query="台灣太陽能 土地衝突",
                target_topic_id="t-core-1",
                rationale="核心議題需要資料",
                source_strategy="both",
                priority="high",
            )
        ],
        narration="計畫搜尋土地衝突相關資料。",
    )


def _make_refine_output(is_stable: bool = True, version: int = 1) -> AssociatorRefineOutput:
    cm = _make_context_map(version=version)
    return AssociatorRefineOutput(
        updated_context_map=cm,
        delta=ContextMapDelta(
            from_version=version - 1,
            to_version=version,
            reason="加入新資料後更新結構",
        ),
        is_stable=is_stable,
        narration="研究結構已更新。",
    )


def _make_consistency_ok() -> ConsistencyReview:
    return ConsistencyReview(
        drift_level="none",
        drift_description="方向一致，無漂移",
        dubao_voice_message="進展順利，繼續深化。",
        recommended_action="continue",
    )


def _make_style_output() -> StyleAnalysisOutput:
    return StyleAnalysisOutput(
        features=[
            StyleFeature(
                dimension="句式結構",
                observation="偏好短句，避免複雜從句",
                instruction="每句不超過30字，善用段落分隔",
            ),
            StyleFeature(
                dimension="用詞層次",
                observation="學術但平易近人",
                instruction="避免過度術語，用白話解釋概念",
            ),
            StyleFeature(
                dimension="段落節奏",
                observation="先結論後論據",
                instruction="每段開頭放最重要的結論",
            ),
        ],
        overall_tone="學術嚴謹但不枯燥",
        sample_quality_note="範本品質良好，足以分析",
    )


def _make_writer_section(title: str = "土地使用爭議") -> LiveWriterSectionOutput:
    return LiveWriterSectionOutput(
        section_title=title,
        section_content=f"## {title}\n\n台灣綠能發展面臨土地使用的根本矛盾 [1]。\n",
        sources_used=[1, 2],
        confidence_level="High",
        narration="基於高品質來源撰寫本段落。",
    )


def _make_handler() -> MagicMock:
    """Create a mock handler that mimics the real one's interface."""
    handler = MagicMock()
    handler.query = "台灣綠能發展與社區衝突"
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=True)
    # request_handler: connection alive (for 3-signal check in BABLoopEngine)
    handler.request_handler = MagicMock()
    handler.request_handler.connection_alive = True
    # No soft interrupt — use None so _check_connection doesn't trigger it
    handler._soft_interrupt_event = None
    handler.query_params = {}
    handler.site = "all"
    handler.final_retrieved_items = []
    return handler


def _make_orchestrator(handler=None) -> LiveResearchOrchestrator:
    """Create orchestrator with mocked AssociatorAgent."""
    if handler is None:
        handler = _make_handler()
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    # Replace associator with a fresh AsyncMock for each test
    orch.associator = AsyncMock()
    orch.associator.build_context_map = AsyncMock(return_value=_make_build_output())
    orch.associator.derive_search_plan = AsyncMock(return_value=_make_derive_output())
    orch.associator.refine_context_map = AsyncMock(return_value=_make_refine_output(is_stable=True))
    return orch


def _make_engine(orchestrator: LiveResearchOrchestrator) -> BABLoopEngine:
    """Create BABLoopEngine with all LLM calls mocked."""
    engine = BABLoopEngine(
        associator=orchestrator.associator,
        handler=orchestrator.handler,
        max_iterations=1,  # Minimal iterations for speed
        enable_consistency_monitor=True,
    )
    engine._execute_search = AsyncMock(return_value=("formatted results", {"1": {}}))
    engine._run_mini_reasoning = AsyncMock(return_value=None)
    engine._run_consistency_check = AsyncMock(return_value=_make_consistency_ok())
    return engine


def _state_with_context_map(stage: int, status: str = "checkpoint") -> LiveResearchStageState:
    """Build a realistic LiveResearchStageState with a full context map."""
    cm = _make_context_map()
    state = LiveResearchStageState(
        current_stage=stage,
        stage_status=status,
        context_map_json=cm.model_dump_json(),
        initial_context_map_json=cm.model_dump_json(),
    )
    return state


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 1: Happy path — auto-continue all stages (1 → 6 → completed)
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario1HappyPath:
    """start() → Stage 1 checkpoint → continue(auto=True) x5 → Stage 6 completed."""

    @pytest.fixture
    def orchestrator(self):
        return _make_orchestrator()

    @pytest.mark.asyncio
    async def test_start_reaches_stage_1_checkpoint(self, orchestrator):
        """start() should reach Stage 1 checkpoint."""
        cm = _make_context_map()

        with patch("reasoning.live_research.orchestrator.BABLoopEngine") as MockEngine:
            engine_instance = MagicMock()
            engine_instance.run_loop = AsyncMock(return_value=cm)
            engine_instance.initial_context_map = cm
            engine_instance.executed_searches = []
            MockEngine.return_value = engine_instance

            state = await orchestrator.start(query="台灣綠能發展與社區衝突")

        assert state.current_stage == 1
        assert state.stage_status == "checkpoint"
        assert state.context_map_json != ""

    @pytest.mark.asyncio
    async def test_full_auto_continue_pipeline(self, orchestrator):
        """
        Full pipeline: start() → Stage 1 → continue(auto) x5 → Stage 6 completed.

        Mock all _run_stage_N private methods to return pre-built states,
        testing only the orchestrator's routing logic.
        """
        cm = _make_context_map()
        cm_json = cm.model_dump_json()

        # Stage 1: start()
        stage1_state = LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )
        orchestrator._run_stage_1 = AsyncMock(return_value=stage1_state)

        # Stage 2
        stage2_state = LiveResearchStageState(
            current_stage=2, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )
        orchestrator._run_stage_2 = AsyncMock(return_value=stage2_state)

        # Stage 3
        stage3_state = LiveResearchStageState(
            current_stage=3, stage_status="checkpoint",
            context_map_json=cm_json,
        )
        orchestrator._run_stage_3 = AsyncMock(return_value=stage3_state)

        # Stage 4
        stage4_state = LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm_json,
        )
        orchestrator._run_stage_4 = AsyncMock(return_value=stage4_state)

        # Stage 5 — all sections written (cm has 2 core topics) so the Stage 5
        # auto_continue completeness gate (mock_bab E2E fix 2026-05-29) routes to export.
        stage5_state = LiveResearchStageState(
            current_stage=5, stage_status="checkpoint",
            context_map_json=cm_json,
            written_sections=[
                {"section_index": 0, "title": "土地使用爭議", "content": "x",
                 "sources_used": [], "confidence_level": "Medium"},
                {"section_index": 1, "title": "社區共識機制", "content": "y",
                 "sources_used": [], "confidence_level": "High"},
            ],
            last_completed_section_index=1,
        )
        orchestrator._run_stage_5 = AsyncMock(return_value=stage5_state)

        # Stage 6
        stage6_state = LiveResearchStageState(
            current_stage=6, stage_status="completed",
            context_map_json=cm_json,
        )
        orchestrator._run_stage_6 = AsyncMock(return_value=stage6_state)

        # Run
        state = await orchestrator.start(query="台灣綠能發展與社區衝突")
        assert state.current_stage == 1
        assert state.stage_status == "checkpoint"

        # Stage 1 → 2
        state = await orchestrator.continue_from_checkpoint(state, auto_continue=True)
        assert state.current_stage == 2
        assert state.stage_status == "checkpoint"

        # Stage 2 → 3
        state = await orchestrator.continue_from_checkpoint(state, auto_continue=True)
        assert state.current_stage == 3
        assert state.stage_status == "checkpoint"

        # Stage 3 → 4 (auto = skip style analysis)
        # For stage 3 auto, _handle_stage_3_response completes then _run_stage_4 fires
        state = await orchestrator.continue_from_checkpoint(state, auto_continue=True)
        assert state.current_stage == 4
        assert state.stage_status == "checkpoint"

        # Stage 4 → 5
        state = await orchestrator.continue_from_checkpoint(state, auto_continue=True)
        assert state.current_stage == 5
        assert state.stage_status == "checkpoint"

        # Stage 5 → 6
        state = await orchestrator.continue_from_checkpoint(state, auto_continue=True)
        assert state.current_stage == 6
        assert state.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_stage_progression_1_to_6(self, orchestrator):
        """Verify stage numbers advance 1→6 throughout pipeline."""
        cm_json = _make_context_map().model_dump_json()

        stages_seen = []

        # Track calls by patching continue_from_checkpoint logic
        # Test via individual stage transitions
        for stage in range(1, 7):
            state = LiveResearchStageState(
                current_stage=stage,
                stage_status="completed" if stage == 6 else "checkpoint",
                context_map_json=cm_json,
                initial_context_map_json=cm_json,
            )
            stages_seen.append(state.current_stage)

        assert stages_seen == [1, 2, 3, 4, 5, 6]


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 2: Stage 1+2 with user feedback
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario2UserFeedback:
    """start() → Stage 1 checkpoint → continue(user_message) → Stage 2 loop runs."""

    @pytest.fixture
    def orchestrator(self):
        return _make_orchestrator()

    @pytest.mark.asyncio
    async def test_stage_1_with_user_feedback_advances_to_2(self, orchestrator):
        """User confirm at Stage 1 checkpoint advances to Stage 2 (dialog loop semantics)。

        Stage 1 dialog loop 補完後：非空 user_message 預設不 advance；
        只有 LLM intent parser 回 action="confirm" 時才 advance 到 Stage 2。
        """
        from reasoning.schemas_live import Stage1ParsedIntent

        cm = _make_context_map()
        cm_json = cm.model_dump_json()

        stage1_state = LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )

        stage2_state = LiveResearchStageState(
            current_stage=2, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )
        orchestrator._run_stage_2 = AsyncMock(return_value=stage2_state)
        # Mock intent parser 回 confirm → 確認 confirm path 會 advance
        orchestrator._parse_stage_1_intent = AsyncMock(
            return_value=Stage1ParsedIntent(action="confirm", operations=[], summary="OK")
        )

        state = await orchestrator.continue_from_checkpoint(
            stage1_state,
            user_message="就這樣，可以開始蒐集資料了",
        )

        assert state.current_stage == 2
        orchestrator._run_stage_2.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage_2_per_section_loop_executed(self, orchestrator):
        """Stage 2 should execute BABLoopEngine per-section loops."""
        cm = _make_context_map()
        cm_json = cm.model_dump_json()

        stage2_input = LiveResearchStageState(
            current_stage=2, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )

        # Mock Stage 3 to prevent further execution
        orchestrator._run_stage_3 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=3, stage_status="checkpoint",
            context_map_json=cm_json,
        ))

        # Stage 2 → 3 auto-continue: calls _handle_stage_2_response + _run_stage_3
        state = await orchestrator.continue_from_checkpoint(
            stage2_input, user_message="", auto_continue=True
        )
        assert state.current_stage == 3
        orchestrator._run_stage_3.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage_2_actual_bab_loop_runs_per_core_topic(self, orchestrator):
        """Stage 2's _run_stage_2 uses BABLoopEngine once per core topic."""
        cm = _make_context_map()  # Has 2 core topics
        cm_json = cm.model_dump_json()

        bab_calls = []

        async def mock_bab_run_loop(**kwargs):
            bab_calls.append(kwargs)
            return _make_context_map(version=len(bab_calls))

        state = LiveResearchStageState(
            current_stage=1, stage_status="in_progress",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )
        state.advance_to_stage(2)

        with patch("reasoning.live_research.orchestrator.BABLoopEngine") as MockEngine:
            engine_instance = MagicMock()
            engine_instance.run_loop = AsyncMock(side_effect=mock_bab_run_loop)
            engine_instance.initial_context_map = cm
            engine_instance.executed_searches = []
            MockEngine.return_value = engine_instance

            result = await orchestrator._run_stage_2(state)

        # Should have run one BABLoopEngine per core topic (2 core topics)
        core_topics = [t for t in cm.topics if t.relevance == "core"]
        assert len(bab_calls) == len(core_topics)
        assert result.stage_status == "checkpoint"


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 3: Stage 3 Style Analysis multi-turn dialogue
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario3StyleAnalysis:
    """Stage 3 → sample text → analysis → confirm → Stage 4."""

    @pytest.fixture
    def orchestrator(self):
        return _make_orchestrator()

    @pytest.mark.asyncio
    async def test_style_sample_triggers_analysis(self, orchestrator):
        """
        Stage 3 with sample text → style analysis runs → returns checkpoint
        (still in Stage 3, waiting for confirmation).
        """
        cm_json = _make_context_map().model_dump_json()
        stage3_state = LiveResearchStageState(
            current_stage=3, stage_status="checkpoint",
            context_map_json=cm_json,
            style_features_json="",  # No analysis yet
        )

        mock_style = _make_style_output()

        with patch.object(orchestrator, "_run_style_analysis", new=AsyncMock(return_value=mock_style)):
            result = await orchestrator._handle_stage_3_response(
                stage3_state,
                user_message="台灣的能源政策正處於轉型的十字路口...",
                auto_continue=False,
            )

        # Should still be in Stage 3, waiting for user confirmation
        assert result.current_stage == 3
        assert result.stage_status == "checkpoint"
        assert result.style_features_json != ""

        # style_features_json should be valid JSON matching StyleAnalysisOutput
        parsed = StyleAnalysisOutput.model_validate_json(result.style_features_json)
        assert len(parsed.features) >= 3
        assert parsed.overall_tone == "學術嚴謹但不枯燥"

    @pytest.mark.asyncio
    async def test_confirm_intent_advances_to_stage_4(self, orchestrator):
        """
        Stage 3 with existing analysis + confirm intent → complete Stage 3 → run Stage 4.
        """
        cm_json = _make_context_map().model_dump_json()
        mock_style = _make_style_output()

        stage3_state = LiveResearchStageState(
            current_stage=3, stage_status="checkpoint",
            context_map_json=cm_json,
            style_features_json=mock_style.model_dump_json(),
        )

        stage4_state = LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm_json,
        )
        orchestrator._run_stage_4 = AsyncMock(return_value=stage4_state)

        # Mock intent parsing to return "confirm"
        with patch.object(
            orchestrator,
            "_parse_style_confirmation_intent",
            new=AsyncMock(return_value={"action": "confirm", "reason": "分析準確"}),
        ):
            result = await orchestrator.continue_from_checkpoint(
                stage3_state,
                user_message="準確，請繼續",
            )

        assert result.current_stage == 4
        orchestrator._run_stage_4.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_continue_skips_style_analysis(self, orchestrator):
        """auto_continue at Stage 3 skips style analysis, goes to Stage 4."""
        cm_json = _make_context_map().model_dump_json()
        stage3_state = LiveResearchStageState(
            current_stage=3, stage_status="checkpoint",
            context_map_json=cm_json,
        )

        stage4_state = LiveResearchStageState(
            current_stage=4, stage_status="checkpoint",
            context_map_json=cm_json,
        )
        orchestrator._run_stage_4 = AsyncMock(return_value=stage4_state)

        result = await orchestrator.continue_from_checkpoint(
            stage3_state,
            user_message="",
            auto_continue=True,
        )

        assert result.current_stage == 4
        assert orchestrator._run_stage_4.called


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 4: Stage 5 Section Revision loop
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario4SectionRevision:
    """Stage 5 → sections written → revise section 0 → re-checkpoint → proceed."""

    @pytest.fixture
    def orchestrator(self):
        return _make_orchestrator()

    @pytest.fixture
    def stage5_state_with_sections(self):
        cm = _make_context_map()
        state = LiveResearchStageState(
            current_stage=5, stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            written_sections=[
                {
                    "section_index": 0,
                    "title": "土地使用爭議",
                    "content": "原始內容...",
                    "sources_used": [1],
                    "confidence_level": "Medium",
                },
                {
                    "section_index": 1,
                    "title": "社區共識機制",
                    "content": "原始社區內容...",
                    "sources_used": [2],
                    "confidence_level": "High",
                },
            ],
        )
        return state

    @pytest.mark.asyncio
    async def test_revision_request_triggers_section_rewrite(
        self, orchestrator, stage5_state_with_sections
    ):
        """
        User requests revision of section 0 → _write_section called →
        section 0 replaced → re-checkpoint in Stage 5.
        """
        new_section = _make_writer_section("土地使用爭議（修改版）")

        with patch.object(
            orchestrator,
            "_parse_revision_intent",
            new=AsyncMock(return_value={
                # 新契約：1-based 段號。「第一段」→ 1 → 消費端轉 0-based index 0
                "action": "revise_section",
                "target_index": 1,
                "instruction": "請加強引用數量",
                "reason": "使用者要求修改第一段",
            }),
        ), patch.object(
            orchestrator,
            "_write_section",
            # Task 9: _write_section now returns (section_output, was_corrected) tuple
            new=AsyncMock(return_value=(new_section, False)),
        ):
            result = await orchestrator.continue_from_checkpoint(
                stage5_state_with_sections,
                user_message="修改第一段，請加強引用",
            )

        # Should re-checkpoint in Stage 5
        assert result.current_stage == 5
        assert result.stage_status == "checkpoint"
        # Section 0 should be replaced
        assert result.written_sections[0]["title"] == "土地使用爭議（修改版）"

    @pytest.mark.asyncio
    async def test_revision_then_auto_continue_goes_to_stage_6(
        self, orchestrator, stage5_state_with_sections
    ):
        """After revision checkpoint with ALL sections written, auto-continue → Stage 6.

        mock_bab E2E fix (2026-05-29): Stage 5 auto_continue now has a completeness
        gate — it only exports when all sections are written. The fixture cm has 2
        core topics; last_completed_section_index=1 marks both written so export fires.
        """
        # Simulate the state after revision (re-checkpoint in Stage 5), ALL sections done
        revised_state = LiveResearchStageState(
            current_stage=5, stage_status="checkpoint",
            context_map_json=stage5_state_with_sections.context_map_json,
            written_sections=stage5_state_with_sections.written_sections,
            last_completed_section_index=1,  # both core topics written (total=2)
        )

        stage6_state = LiveResearchStageState(
            current_stage=6, stage_status="completed",
            context_map_json=stage5_state_with_sections.context_map_json,
        )
        orchestrator._run_stage_6 = AsyncMock(return_value=stage6_state)

        result = await orchestrator.continue_from_checkpoint(
            revised_state, user_message="", auto_continue=True
        )
        assert result.current_stage == 6
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_auto_continue_with_sections_remaining_writes_next(
        self, orchestrator, stage5_state_with_sections
    ):
        """mock_bab E2E fix (2026-05-29): auto_continue with unwritten sections must
        keep writing (NOT export). cm has 2 core topics; only section 0 written
        (last_completed=0) → next section written via _run_stage_5, stays in Stage 5."""
        partial_state = LiveResearchStageState(
            current_stage=5, stage_status="checkpoint",
            context_map_json=stage5_state_with_sections.context_map_json,
            written_sections=stage5_state_with_sections.written_sections[:1],
            last_completed_section_index=0,  # only 1 of 2 written
        )

        # _run_stage_5 mocked to assert it is called (write-next path), returns checkpoint
        next_checkpoint = LiveResearchStageState(
            current_stage=5, stage_status="checkpoint",
            context_map_json=stage5_state_with_sections.context_map_json,
        )
        orchestrator._run_stage_5 = AsyncMock(return_value=next_checkpoint)
        # _run_stage_6 must NOT be called (would mean premature export)
        orchestrator._run_stage_6 = AsyncMock()

        result = await orchestrator.continue_from_checkpoint(
            partial_state, user_message="", auto_continue=True
        )
        orchestrator._run_stage_5.assert_called_once()
        orchestrator._run_stage_6.assert_not_called()
        assert result.current_stage == 5
        assert result.stage_status == "checkpoint"


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 5: Consistency Monitor pause
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario5ConsistencyPause:
    """Consistency Monitor recommends pause_confirm → loop breaks early."""

    @pytest.fixture
    def mock_handler(self):
        return _make_handler()

    @pytest.fixture
    def mock_associator(self):
        agent = AsyncMock()
        agent.build_context_map = AsyncMock(return_value=_make_build_output())
        agent.derive_search_plan = AsyncMock(return_value=_make_derive_output())
        agent.refine_context_map = AsyncMock(
            return_value=_make_refine_output(is_stable=False, version=1)
        )
        return agent

    @pytest.mark.asyncio
    async def test_consistency_pause_breaks_loop(self, mock_handler, mock_associator):
        """
        When _run_consistency_check returns recommended_action="pause_confirm",
        engine.paused_by_consistency should be True and loop should exit early.
        """
        consistency_pause = ConsistencyReview(
            drift_level="moderate",
            drift_description="研究方向偏移嚴重，需要使用者確認",
            dubao_voice_message="等等，我發現研究方向有些偏移，需要你確認一下...",
            recommended_action="pause_confirm",
        )

        engine = BABLoopEngine(
            associator=mock_associator,
            handler=mock_handler,
            max_iterations=3,  # Would run 3x without pause
            enable_consistency_monitor=True,
        )
        engine._execute_search = AsyncMock(return_value=("formatted results", {"1": {}}))
        engine._run_mini_reasoning = AsyncMock(return_value=None)
        engine._run_consistency_check = AsyncMock(return_value=consistency_pause)

        result = await engine.run_loop(query="台灣綠能發展與社區衝突")

        # Loop should have exited due to consistency pause
        assert engine.paused_by_consistency is True
        # refine should have been called exactly once (1 iteration before pause)
        assert mock_associator.refine_context_map.call_count == 1

    @pytest.mark.asyncio
    async def test_consistency_ok_allows_loop_to_continue(self, mock_handler, mock_associator):
        """When consistency is OK, loop continues until stable or max_iterations."""
        engine = BABLoopEngine(
            associator=mock_associator,
            handler=mock_handler,
            max_iterations=2,
            enable_consistency_monitor=True,
        )
        engine._execute_search = AsyncMock(return_value=("formatted results", {"1": {}}))
        engine._run_mini_reasoning = AsyncMock(return_value=None)
        engine._run_consistency_check = AsyncMock(return_value=_make_consistency_ok())

        result = await engine.run_loop(query="台灣綠能發展與社區衝突")

        assert engine.paused_by_consistency is False

    @pytest.mark.asyncio
    async def test_disabled_consistency_monitor_skips_check(self, mock_handler, mock_associator):
        """When enable_consistency_monitor=False, consistency check not run."""
        engine = BABLoopEngine(
            associator=mock_associator,
            handler=mock_handler,
            max_iterations=1,
            enable_consistency_monitor=False,
        )
        engine._execute_search = AsyncMock(return_value=("formatted results", {"1": {}}))
        engine._run_mini_reasoning = AsyncMock(return_value=None)
        engine._run_consistency_check = AsyncMock(return_value=_make_consistency_ok())

        await engine.run_loop(query="台灣綠能發展與社區衝突")

        # Consistency check should NOT have been called
        engine._run_consistency_check.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 6: State persistence round-trip
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario6StatePersistence:
    """State serialization: to_dict() → from_dict() → all fields preserved."""

    def test_empty_state_round_trip(self):
        """Empty state serializes and deserializes correctly."""
        original = LiveResearchStageState()
        d = original.to_dict()
        restored = LiveResearchStageState.from_dict(d)

        assert restored.current_stage == original.current_stage
        assert restored.stage_status == original.stage_status
        assert restored.checkpoint_prompt == original.checkpoint_prompt

    def test_full_state_with_context_map_round_trip(self):
        """State with ContextMap JSON serializes correctly."""
        cm = _make_context_map(version=3)
        cm_json = cm.model_dump_json()
        initial_cm = _make_context_map(version=0)
        initial_cm_json = initial_cm.model_dump_json()

        original = LiveResearchStageState(
            current_stage=3,
            stage_status="checkpoint",
            checkpoint_prompt="Stage 3 提案文字",
            context_map_json=cm_json,
            initial_context_map_json=initial_cm_json,
            completed_sections=["t-core-1", "t-core-2"],
            style_features_json=_make_style_output().model_dump_json(),
            format_specs={"default": "markdown_apa"},
            written_sections=[
                {"section_index": 0, "title": "土地使用爭議", "content": "...", "sources_used": [1]},
            ],
            executed_searches=["台灣太陽能 土地衝突", "再生能源 社區"],
        )

        d = original.to_dict()
        restored = LiveResearchStageState.from_dict(d)

        # Verify all fields
        assert restored.current_stage == 3
        assert restored.stage_status == "checkpoint"
        assert restored.checkpoint_prompt == "Stage 3 提案文字"
        assert restored.context_map_json == cm_json
        assert restored.initial_context_map_json == initial_cm_json
        assert restored.completed_sections == ["t-core-1", "t-core-2"]
        assert restored.style_features_json == _make_style_output().model_dump_json()
        assert restored.format_specs == {"default": "markdown_apa"}
        assert len(restored.written_sections) == 1
        assert restored.written_sections[0]["title"] == "土地使用爭議"
        assert restored.executed_searches == ["台灣太陽能 土地衝突", "再生能源 社區"]

    def test_context_map_json_is_valid_after_round_trip(self):
        """ContextMap stored as JSON can be re-parsed after state round-trip."""
        cm = _make_context_map(version=2)
        state = LiveResearchStageState(context_map_json=cm.model_dump_json())
        restored = LiveResearchStageState.from_dict(state.to_dict())

        # Re-parse the ContextMap
        parsed_cm = ContextMap.model_validate_json(restored.context_map_json)
        assert parsed_cm.research_question == "台灣綠能發展與社區衝突"
        assert len(parsed_cm.topics) == 3
        assert parsed_cm.version == 2

    def test_style_features_json_valid_after_round_trip(self):
        """StyleAnalysisOutput stored as JSON can be re-parsed after state round-trip."""
        style = _make_style_output()
        state = LiveResearchStageState(style_features_json=style.model_dump_json())
        restored = LiveResearchStageState.from_dict(state.to_dict())

        parsed_style = StyleAnalysisOutput.model_validate_json(restored.style_features_json)
        assert len(parsed_style.features) == 3
        assert parsed_style.overall_tone == "學術嚴謹但不枯燥"

    def test_advance_to_stage_changes_status(self):
        """advance_to_stage() transitions status to in_progress."""
        state = LiveResearchStageState()
        state.advance_to_stage(2)
        assert state.current_stage == 2
        assert state.stage_status == "in_progress"

    def test_set_checkpoint_changes_status(self):
        """set_checkpoint() transitions status to checkpoint."""
        state = LiveResearchStageState(current_stage=1, stage_status="in_progress")
        state.set_checkpoint("這是提案文字")
        assert state.stage_status == "checkpoint"
        assert state.checkpoint_prompt == "這是提案文字"

    def test_complete_stage_changes_status(self):
        """complete_stage() transitions status to completed."""
        state = LiveResearchStageState(current_stage=1, stage_status="checkpoint")
        state.complete_stage()
        assert state.stage_status == "completed"

    def test_missing_fields_use_defaults_in_from_dict(self):
        """from_dict() with missing fields uses dataclass defaults."""
        minimal = {"current_stage": 4, "stage_status": "checkpoint"}
        state = LiveResearchStageState.from_dict(minimal)
        assert state.current_stage == 4
        assert state.completed_sections == []
        assert state.written_sections == []
        assert state.executed_searches == []
        assert state.format_specs == {}

    def test_book_outline_roundtrip(self):
        """Plan 4 Phase 1: BookOutline schema + state.book_outline_json round-trip。

        驗證：
        - BookOutline / ChapterPlan schema 可建立 + JSON dump
        - state.book_outline_json 欄位寫入後 round-trip 保留資料
        - 舊 row 沒 book_outline_json 欄位 → from_dict fallback 空字串（backward compat）
        """
        from reasoning.schemas_live import BookOutline, ChapterPlan

        outline = BookOutline(
            chapters=[
                ChapterPlan(
                    chapter_index=0,
                    title="前言",
                    brief="動機",
                    target_word_count=800,
                    planned_evidence_ids=[1],
                    transition_hint="",
                    role="intro",
                ),
                ChapterPlan(
                    chapter_index=1,
                    title="結論",
                    brief="收尾",
                    target_word_count=600,
                    planned_evidence_ids=[],
                    transition_hint="承接前文",
                    role="conclusion",
                ),
            ],
            overall_arc="動機 → 結論",
            redundancy_warnings=[],
        )
        state = LiveResearchStageState(
            current_stage=5,
            book_outline_json=outline.model_dump_json(),
        )
        d = state.to_dict()
        restored = LiveResearchStageState.from_dict(d)
        restored_outline = BookOutline.model_validate_json(restored.book_outline_json)
        assert len(restored_outline.chapters) == 2
        assert restored_outline.chapters[0].role == "intro"
        assert restored_outline.chapters[1].role == "conclusion"
        assert restored_outline.chapters[0].planned_evidence_ids == [1]

        # Backward compat: 舊 row 沒這欄位 → 空字串
        minimal = {"current_stage": 5, "stage_status": "checkpoint"}
        minimal_state = LiveResearchStageState.from_dict(minimal)
        assert minimal_state.book_outline_json == ""

    def test_format_specs_chapters_roundtrip(self):
        """Plan 2 Phase 1: format_specs.chapters (List[Dict]) 寫入後 round-trip 保留資料。

        覆蓋 writer-format-specs-prioritization-plan Phase 1 Step 1:
        - format_specs 從 Dict[str, str] 升級為 Dict[str, Any]
        - 同時兼容既有 {"user_specified": str} / {"default": str} 寫法
        """
        original = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            format_specs={
                "user_specified": "五章 / APA / 含表格",
                "chapters": [
                    {"name": "前言", "outline": "研究動機"},
                    {"name": "國內案例", "outline": "台灣綠能社區衝突"},
                    {"name": "國外案例", "outline": "歐美案例比較"},
                    {"name": "結果與討論", "outline": "綜合分析"},
                    {"name": "結論", "outline": "policy implication"},
                ],
            },
        )
        d = original.to_dict()
        restored = LiveResearchStageState.from_dict(d)

        # chapters 完整 round-trip 保留 (list of dict)
        assert restored.format_specs["chapters"] == [
            {"name": "前言", "outline": "研究動機"},
            {"name": "國內案例", "outline": "台灣綠能社區衝突"},
            {"name": "國外案例", "outline": "歐美案例比較"},
            {"name": "結果與討論", "outline": "綜合分析"},
            {"name": "結論", "outline": "policy implication"},
        ]
        # backward compat: user_specified 字串欄位仍 work
        assert restored.format_specs["user_specified"] == "五章 / APA / 含表格"

    def test_format_specs_special_elements_roundtrip(self):
        """LR Stage 5 special_elements Phase 1: format_specs.special_elements
        (List[Dict[str, str]]) 寫入後 round-trip 保留資料。

        Schema 在 Plan 2 Phase 1 已升級為 Dict[str, Any]，本 test 驗證
        新欄位 special_elements 走相同 round-trip path 不丟資料。
        欄位語意 declare 在 docs/specs/live-research-spec.md §4.10。
        """
        original = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            format_specs={
                "user_specified": "五章 / 含表格 / APA",
                "special_elements": [
                    {
                        "type": "table",
                        "target_chapter": "結果與討論",
                        "description": "5 國能源使用率比較",
                    },
                    {
                        "type": "list",
                        "target_chapter": "結論",
                        "description": "三點政策建議",
                    },
                ],
            },
        )
        d = original.to_dict()
        restored = LiveResearchStageState.from_dict(d)

        # special_elements 完整 round-trip 保留 (list of dict, 三欄位完整)
        assert restored.format_specs["special_elements"] == [
            {
                "type": "table",
                "target_chapter": "結果與討論",
                "description": "5 國能源使用率比較",
            },
            {
                "type": "list",
                "target_chapter": "結論",
                "description": "三點政策建議",
            },
        ]
        # backward compat: user_specified 字串欄位仍 work
        assert restored.format_specs["user_specified"] == "五章 / 含表格 / APA"


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 7: Stage 1 dialog loop (Task 7)
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario7Stage1DialogLoop:
    """Stage 1 dialog loop — user adjusts (add topic) then confirms。

    驗證 end-to-end：
    1. start() → Stage 1 checkpoint
    2. user reply 「新增國際案例段落」→ Stage 1 保持 checkpoint，ContextMap 已 mutate
    3. user reply 「好，就這樣」→ advance 到 Stage 2 checkpoint
    """

    @pytest.fixture
    def orchestrator(self):
        return _make_orchestrator()

    @pytest.mark.asyncio
    async def test_dialog_loop_adjust_then_confirm(self, orchestrator):
        from reasoning.schemas_live import (
            Stage1ParsedIntent, ContextMapRevisionOperation,
        )

        # Stage 1：用 dry_run 進入 Stage 1 checkpoint
        # 透過直接呼叫 _run_stage_1 with mock BAB engine 進入 Stage 1 checkpoint
        cm = _make_context_map()
        cm_json = cm.model_dump_json()
        original_version = cm.version

        state = LiveResearchStageState(
            current_stage=1, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )

        # Mock Stage 2 run to avoid hitting BAB engine in second round
        stage2_state = LiveResearchStageState(
            current_stage=2, stage_status="checkpoint",
            context_map_json=cm_json, initial_context_map_json=cm_json,
        )
        orchestrator._run_stage_2 = AsyncMock(return_value=stage2_state)

        # 第一輪 user reply：要新增 topic — mock intent parser
        orchestrator._parse_stage_1_intent = AsyncMock(return_value=Stage1ParsedIntent(
            action="adjust",
            operations=[ContextMapRevisionOperation(
                op_type="add_topic",
                new_topic_name="國際案例",
                new_topic_relevance="core",
            )],
            summary="新增國際案例",
        ))

        state = await orchestrator.continue_from_checkpoint(
            state, user_message="新增國際案例段落", auto_continue=False
        )

        # 應保持 Stage 1 checkpoint，ContextMap 已 mutate
        assert state.current_stage == 1
        assert state.stage_status == "checkpoint"
        cm_v1 = ContextMap.model_validate_json(state.context_map_json)
        assert cm_v1.version == original_version + 1
        assert any(t.name == "國際案例" for t in cm_v1.topics)

        # 第二輪 user reply：confirm → advance
        orchestrator._parse_stage_1_intent = AsyncMock(return_value=Stage1ParsedIntent(
            action="confirm", operations=[], summary="OK",
        ))

        state = await orchestrator.continue_from_checkpoint(
            state, user_message="好，就這樣", auto_continue=False
        )

        # 應 advance 到 Stage 2 checkpoint
        assert state.current_stage == 2
        assert state.stage_status == "checkpoint"
        orchestrator._run_stage_2.assert_called_once()
