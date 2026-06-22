"""B9: Analyst.revise() must select the same schema as research() based on
feature flags, so REJECT→revise→converge does not strip argument_graph.

LLM-safe: call_llm_validated is mocked; no real LLM call.
Pattern reference: test_loop_engine_revise_loop.py (mock AsyncMock agents).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.agents.analyst import AnalystAgent
from reasoning.schemas import AnalystResearchOutput, CriticReviewOutput
from reasoning.schemas_enhanced import (
    AnalystResearchOutputEnhanced,
    AnalystResearchOutputEnhancedKG,
)


def _make_agent():
    handler = MagicMock()
    return AnalystAgent(handler=handler, timeout=60)


def _make_review():
    return CriticReviewOutput(
        status="REJECT",
        critique="x" * 60,
        suggestions=["s"],
        mode_compliance="符合",
        logical_gaps=[],
        source_issues=[],
    )


def _config_with(features):
    cfg = MagicMock()
    cfg.reasoning_params = {"features": features}
    return cfg


class _StubReview:
    """Plain review stub for driving _phase_format_result: keeps a clean
    __dict__ (production reads review.__dict__.get("verification_status")) while
    exposing .status and .structured_weaknesses like a CriticReviewOutput."""

    def __init__(self, status="PASS"):
        self.status = status
        self.structured_weaknesses = None


@pytest.mark.asyncio
async def test_revise_uses_enhanced_schema_when_argument_graphs_on():
    agent = _make_agent()
    captured = {}

    async def fake_call(prompt, response_schema, level):
        captured["schema"] = response_schema
        # Return a minimal valid instance of whatever schema was requested
        return (
            response_schema(
                status="DRAFT_READY",
                draft="d" * 120,
                reasoning_chain="r",
                citations_used=[],
            ),
            0,
            False,
        )

    agent.call_llm_validated = AsyncMock(side_effect=fake_call)

    with patch("core.config.CONFIG", _config_with({"argument_graphs": True})):
        result = await agent.revise(
            original_draft="orig",
            review=_make_review(),
            formatted_context="[1] ...",
            query="Q",
        )

    assert captured["schema"] is AnalystResearchOutputEnhanced
    assert isinstance(result, AnalystResearchOutputEnhanced)


@pytest.mark.asyncio
async def test_revise_uses_kg_schema_when_enable_kg():
    agent = _make_agent()
    captured = {}

    async def fake_call(prompt, response_schema, level):
        captured["schema"] = response_schema
        return (
            response_schema(
                status="DRAFT_READY", draft="d" * 120,
                reasoning_chain="r", citations_used=[],
            ),
            0, False,
        )

    agent.call_llm_validated = AsyncMock(side_effect=fake_call)
    # argument_graphs True but enable_kg must win (priority kg > graphs)
    with patch("core.config.CONFIG", _config_with({"argument_graphs": True})):
        result = await agent.revise(
            original_draft="o", review=_make_review(),
            formatted_context="[1]", query="Q", enable_kg=True,
        )
    assert captured["schema"] is AnalystResearchOutputEnhancedKG
    assert isinstance(result, AnalystResearchOutputEnhancedKG)


@pytest.mark.asyncio
async def test_revise_falls_back_to_base_when_all_flags_off():
    agent = _make_agent()
    captured = {}

    async def fake_call(prompt, response_schema, level):
        captured["schema"] = response_schema
        return (
            response_schema(
                status="DRAFT_READY", draft="d" * 120,
                reasoning_chain="r", citations_used=[],
            ),
            0, False,
        )

    agent.call_llm_validated = AsyncMock(side_effect=fake_call)
    with patch("core.config.CONFIG", _config_with({"argument_graphs": False})):
        result = await agent.revise(
            original_draft="o", review=_make_review(),
            formatted_context="[1]", query="Q",
        )
    assert captured["schema"] is AnalystResearchOutput
    # Base type exactly (not a subclass)
    assert type(result) is AnalystResearchOutput


@pytest.mark.asyncio
async def test_revise_validates_argument_graph_phantom_citations():
    """Revised argument_graph with an evidence_id not in citations_used gets
    cleaned by _validate_argument_graph (mirrors research() behavior)."""
    agent = _make_agent()
    from reasoning.schemas_enhanced import ArgumentNode

    async def fake_call(prompt, response_schema, level):
        node = ArgumentNode(claim="c", evidence_ids=[1, 99])  # 99 is phantom
        return (
            response_schema(
                status="DRAFT_READY", draft="d" * 120,
                reasoning_chain="r", citations_used=[1],
                argument_graph=[node],
            ),
            0, False,
        )

    agent.call_llm_validated = AsyncMock(side_effect=fake_call)
    with patch("core.config.CONFIG", _config_with({"argument_graphs": True})):
        result = await agent.revise(
            original_draft="o", review=_make_review(),
            formatted_context="[1]", query="Q",
        )
    # phantom id 99 removed, only valid id 1 remains
    assert result.argument_graph[0].evidence_ids == [1]


@pytest.mark.asyncio
async def test_format_result_gate_sees_argument_graph_after_revise(monkeypatch):
    """Regression for the B9 chain: a revised Enhanced response must still
    satisfy the orchestrator.py:1162 `hasattr(state.response,'argument_graph')`
    gate. Drives _phase_format_result with no LLM, asserting chain_analysis
    is populated (proof the forensic block ran)."""
    from reasoning.orchestrator import DeepResearchOrchestrator
    from reasoning.schemas_enhanced import AnalystResearchOutputEnhanced, ArgumentNode

    orch = DeepResearchOrchestrator.__new__(DeepResearchOrchestrator)
    # minimal logger
    import logging
    orch.logger = logging.getLogger("test")

    revised = AnalystResearchOutputEnhanced(
        status="DRAFT_READY", draft="d" * 120, reasoning_chain="r",
        citations_used=[1],
        argument_graph=[ArgumentNode(claim="c", evidence_ids=[1])],
    )

    state = MagicMock()
    state.response = revised
    # Use a plain object for review so __dict__ stays clean (production reads
    # both review.status and review.__dict__.get("verification_status")).
    state.review = _StubReview(status="PASS")
    state.tracer = None
    state.chain_analysis = None
    # short-circuit the parts after the gate
    monkeypatch.setattr(orch, "_emit_phase_event", AsyncMock())
    state.iteration_logger = MagicMock()
    state.iteration = 0
    state.mode = "discovery"
    state.current_context = []
    state.items = []
    state.final_report = MagicMock()
    state.final_report.__dict__ = {}
    state.query = "Q"
    monkeypatch.setattr(orch, "_format_result", MagicMock(return_value=[]))

    await orch._phase_format_result(state)

    # Gate passed → chain analysis populated (forensic block executed)
    assert state.chain_analysis is not None


@pytest.mark.asyncio
async def test_revise_then_format_gate_end_to_end(monkeypatch):
    """TRUE B9 end-to-end (C4): call revise() (LLM mocked) → put the result on
    state.response → drive _phase_format_result. With Task 1 in, revise()
    returns an Enhanced schema carrying argument_graph, so the gate fires and
    chain_analysis is populated. Reverting Task 1 makes revise() return base
    AnalystResearchOutput → gate False → chain_analysis stays None → this FAILS.
    """
    from reasoning.orchestrator import DeepResearchOrchestrator
    from reasoning.schemas_enhanced import ArgumentNode

    # 1) Drive a real revise() with the LLM mocked, argument_graphs flag on.
    agent = _make_agent()

    async def fake_call(prompt, response_schema, level):
        # LLM returns a graph-bearing instance of whatever schema revise picked
        node = ArgumentNode(claim="c", evidence_ids=[1])
        return (
            response_schema(
                status="DRAFT_READY", draft="d" * 120,
                reasoning_chain="r", citations_used=[1],
                argument_graph=[node],
            ),
            0, False,
        )

    agent.call_llm_validated = AsyncMock(side_effect=fake_call)
    with patch("core.config.CONFIG", _config_with({"argument_graphs": True})):
        revised = await agent.revise(
            original_draft="o", review=_make_review(),
            formatted_context="[1]", query="Q",
        )

    # 2) Feed revised into the orchestrator gate.
    orch = DeepResearchOrchestrator.__new__(DeepResearchOrchestrator)
    import logging
    orch.logger = logging.getLogger("test")

    state = MagicMock()
    state.response = revised  # <-- the actual revise() output, not a hand-built one
    state.review = _StubReview(status="PASS")
    state.tracer = None
    state.chain_analysis = None
    monkeypatch.setattr(orch, "_emit_phase_event", AsyncMock(), raising=False)
    state.iteration_logger = MagicMock()
    state.iteration = 0
    state.mode = "discovery"
    state.current_context = []
    state.items = []
    state.final_report = MagicMock()
    state.final_report.__dict__ = {}
    state.query = "Q"
    monkeypatch.setattr(orch, "_format_result", MagicMock(return_value=[]))

    await orch._phase_format_result(state)

    # revise() produced argument_graph → gate fired → forensic chain ran.
    assert state.chain_analysis is not None
    # C3 proof: model_copy preserved the original fields (argument_graph still there)
    assert getattr(state.response, "argument_graph", None)


@pytest.mark.asyncio
async def test_revise_lr_flags_keep_kg_type_for_merge_gate():
    """C1: LR revise must return AnalystResearchOutputLive (has knowledge_graph)
    so loop_engine's KG merge gate (`hasattr(analyst_output,'knowledge_graph')`,
    loop_engine.py:1457) stays True after `analyst_output = revised`. Without the
    flags the revise would return Enhanced → hasattr False → LR KG dropped."""
    from reasoning.schemas_live import AnalystResearchOutputLive

    agent = _make_agent()
    captured = {}

    async def fake_call(prompt, response_schema, level):
        captured["schema"] = response_schema
        return (
            response_schema(
                status="DRAFT_READY", draft="d" * 120,
                reasoning_chain="r", citations_used=[],
            ),
            0, False,
        )

    agent.call_llm_validated = AsyncMock(side_effect=fake_call)
    # CONFIG argument_graphs irrelevant once live_research wins the priority chain
    with patch("core.config.CONFIG", _config_with({"argument_graphs": True})):
        revised = await agent.revise(
            original_draft="o", review=_make_review(),
            formatted_context="[1]", query="Q",
            enable_live_research=True, enable_kg=True,
        )

    assert captured["schema"] is AnalystResearchOutputLive
    assert isinstance(revised, AnalystResearchOutputLive)
    # The exact predicate the loop_engine KG merge gate uses:
    assert hasattr(revised, "knowledge_graph") is True
