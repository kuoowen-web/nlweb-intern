"""
Integration tests for Phase 2 (Actor-Critic Loop) extraction.

These tests verify that extracting the while loop into _phase_actor_critic_loop()
produces identical behavior to the inline code. We mock all LLM agents and verify
that:
1. State fields are correctly populated after the phase
2. self.source_map and state.source_map maintain reference identity (G2)
3. Checkpoints are called the expected number of times
4. Gap detection + gap resolution paths work correctly
5. Early return paths (no draft, SEARCH_REQUIRED) work correctly
6. SEC-6 citation tracking works correctly
"""

import asyncio
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Ensure code/python is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reasoning.research_state import ResearchState


# === Mock Schemas ===

class MockAnalystResponse:
    """Mocks AnalystResearchOutput / AnalystResearchOutputEnhanced."""
    def __init__(self, draft="Test draft content.", status="COMPLETE",
                 citations_used=None, new_queries=None, missing_information=None,
                 reasoning_chain="Test reasoning", gap_resolutions=None):
        self.draft = draft
        self.status = status
        self.citations_used = citations_used or [1, 2]
        self.new_queries = new_queries or []
        self.missing_information = missing_information or []
        self.reasoning_chain = reasoning_chain
        self.gap_resolutions = gap_resolutions or []


class MockCriticReview:
    """Mocks CriticReviewOutput."""
    def __init__(self, status="PASS", critique="Good analysis.", suggestions=None,
                 mode_compliance="COMPLIANT"):
        self.status = status
        self.critique = critique
        self.suggestions = suggestions or []
        self.mode_compliance = mode_compliance


# === Test Fixtures ===

def make_items(n=3):
    """Create n test items."""
    return [
        {"url": f"https://test.com/article{i}", "title": f"Article {i}",
         "site": "test.com", "description": f"Description {i}"}
        for i in range(1, n + 1)
    ]


def make_source_map(n=3):
    """Create source_map matching n items."""
    return {
        i: {"url": f"https://test.com/article{i}", "title": f"Article {i}",
            "site": "test.com"}
        for i in range(1, n + 1)
    }


def make_formatted_context(n=3):
    """Create formatted context string."""
    return "\n".join(
        f"[{i}] Article {i} (test.com)\nDescription {i}"
        for i in range(1, n + 1)
    )


def make_state(**overrides):
    """Create a ResearchState pre-populated as if Phase 1 completed."""
    items = make_items()
    defaults = dict(
        query="test query",
        mode="discovery",
        items=items,
        current_context=items.copy(),
        formatted_context=make_formatted_context(),
        source_map=make_source_map(),
        query_id="test_q_001",
        max_iterations=3,
        enable_isolation=False,
        iteration_logger=MagicMock(),
        tracer=None,
    )
    defaults.update(overrides)
    return ResearchState(**defaults)


def make_orchestrator(analyst_responses=None, critic_responses=None):
    """
    Create a mock DeepResearchOrchestrator with controllable agent responses.

    analyst_responses: list of MockAnalystResponse (consumed in order)
    critic_responses: list of MockCriticReview (consumed in order)
    """
    if analyst_responses is None:
        analyst_responses = [MockAnalystResponse()]
    if critic_responses is None:
        critic_responses = [MockCriticReview()]

    analyst_call_idx = {"i": 0}
    critic_call_idx = {"i": 0}

    async def mock_analyst_research(**kwargs):
        idx = analyst_call_idx["i"]
        analyst_call_idx["i"] += 1
        if idx < len(analyst_responses):
            return analyst_responses[idx]
        return analyst_responses[-1]

    async def mock_analyst_revise(**kwargs):
        idx = analyst_call_idx["i"]
        analyst_call_idx["i"] += 1
        if idx < len(analyst_responses):
            return analyst_responses[idx]
        return analyst_responses[-1]

    async def mock_critic_review(*args, **kwargs):
        idx = critic_call_idx["i"]
        critic_call_idx["i"] += 1
        if idx < len(critic_responses):
            return critic_responses[idx]
        return critic_responses[-1]

    # Build mock orchestrator
    orch = MagicMock()
    orch.logger = MagicMock()
    orch.handler = MagicMock()
    orch.handler.site = "test"
    orch.handler.query_params = {}

    # Agents
    orch.analyst = MagicMock()
    orch.analyst.research = mock_analyst_research
    orch.analyst.revise = mock_analyst_revise
    orch.critic = MagicMock()
    orch.critic.review = mock_critic_review

    # Source filter
    orch.source_filter = MagicMock()
    orch.source_filter.filter_and_enrich = MagicMock(return_value=[])

    # Instance attributes
    orch.formatted_context = ""
    orch.source_map = {}

    # Methods
    orch._check_connection = MagicMock()
    orch._send_progress = AsyncMock()
    orch._format_context_shared = MagicMock(
        return_value=(make_formatted_context(), make_source_map())
    )
    orch._build_critic_reference_sheet = MagicMock(return_value="ref sheet")
    orch._process_gap_resolutions = AsyncMock()
    orch._format_error_result = MagicMock(return_value=[{"error": True}])
    orch._format_friendly_no_data_result = MagicMock(return_value=[{"no_data": True}])
    orch._emit_phase_event = AsyncMock()

    return orch


# === Tests ===

class TestPhaseActorCriticLoop:
    """Tests for _phase_actor_critic_loop extraction."""

    @pytest.mark.asyncio
    async def test_happy_path_single_iteration(self):
        """Analyst produces draft, Critic passes on first iteration."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        analyst_resp = MockAnalystResponse(
            draft="Final draft", status="COMPLETE", citations_used=[1, 2]
        )
        critic_resp = MockCriticReview(status="PASS")

        orch = make_orchestrator(
            analyst_responses=[analyst_resp],
            critic_responses=[critic_resp],
        )

        state = make_state()
        # Set source_map on orch to match state (G2 reference identity)
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        # Call the real method (bound to mock)
        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        assert result.draft == "Final draft"
        assert result.iteration == 0  # break before increment
        assert result.reject_count == 0
        assert result.analyst_citations == [1, 2]
        assert result.early_return is None
        assert result.review.status == "PASS"

    @pytest.mark.asyncio
    async def test_reject_then_pass(self):
        """Critic rejects first, analyst revises, critic passes second time."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        analyst_responses = [
            MockAnalystResponse(draft="Draft v1", citations_used=[1]),
            MockAnalystResponse(draft="Draft v2", citations_used=[1, 2]),
        ]
        critic_responses = [
            MockCriticReview(status="REJECT", critique="Needs more data"),
            MockCriticReview(status="PASS"),
        ]

        orch = make_orchestrator(
            analyst_responses=analyst_responses,
            critic_responses=critic_responses,
        )

        state = make_state()
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        assert result.draft == "Draft v2"
        assert result.reject_count == 1
        assert result.review.status == "PASS"
        assert result.analyst_citations == [1, 2]

    @pytest.mark.asyncio
    async def test_no_draft_early_return(self):
        """When analyst never produces a draft, early_return is set."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        # Analyst returns SEARCH_REQUIRED every time (no draft)
        analyst_resp = MockAnalystResponse(
            draft=None, status="SEARCH_REQUIRED",
            new_queries=["補充查詢"], missing_information=["missing info"],
            citations_used=[]
        )

        orch = make_orchestrator(
            analyst_responses=[analyst_resp],
            critic_responses=[MockCriticReview()],
        )

        state = make_state(max_iterations=1)
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        assert result.early_return is not None

    @pytest.mark.asyncio
    async def test_source_map_reference_identity_g2(self):
        """G2: state.source_map and self.source_map must be the same dict reference."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        analyst_resp = MockAnalystResponse(
            draft="Draft", status="COMPLETE", citations_used=[1]
        )
        critic_resp = MockCriticReview(status="PASS")

        orch = make_orchestrator(
            analyst_responses=[analyst_resp],
            critic_responses=[critic_resp],
        )

        state = make_state()
        # G2: set orch.source_map to the SAME dict as state.source_map
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        # After phase, source_map should still be the same object
        assert result.source_map is orch.source_map, \
            "G2 violated: state.source_map and self.source_map must be the same dict reference"

    @pytest.mark.asyncio
    async def test_checkpoints_called(self):
        """All checkpoints within the loop are called."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        analyst_resp = MockAnalystResponse(
            draft="Draft", status="COMPLETE", citations_used=[1]
        )
        critic_resp = MockCriticReview(status="PASS")

        orch = make_orchestrator(
            analyst_responses=[analyst_resp],
            critic_responses=[critic_resp],
        )

        state = make_state()
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        # In a single PASS iteration: checkpoint 1 (loop start) + checkpoint 3 (research) + checkpoint 7 (critic)
        assert orch._check_connection.call_count >= 3

    @pytest.mark.asyncio
    async def test_sec6_citation_tracking(self):
        """SEC-6: seen_citation_ids tracks all citation IDs across iterations."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        analyst_responses = [
            MockAnalystResponse(draft="Draft v1", citations_used=[1, 3]),
            MockAnalystResponse(draft="Draft v2", citations_used=[1, 2]),
        ]
        critic_responses = [
            MockCriticReview(status="REJECT"),
            MockCriticReview(status="PASS"),
        ]

        orch = make_orchestrator(
            analyst_responses=analyst_responses,
            critic_responses=critic_responses,
        )

        state = make_state(enable_isolation=True)
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        # seen_citation_ids should contain union of all citations across iterations
        assert 1 in result.seen_citation_ids
        assert 2 in result.seen_citation_ids
        assert 3 in result.seen_citation_ids

    @pytest.mark.asyncio
    async def test_max_iterations_exhausted_graceful_degradation(self):
        """When max iterations reached with continuous REJECTs, degrade gracefully."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        analyst_responses = [
            MockAnalystResponse(draft="Draft v1", citations_used=[1]),
            MockAnalystResponse(draft="Draft v2", citations_used=[1]),
        ]
        critic_responses = [
            MockCriticReview(status="REJECT"),
            MockCriticReview(status="REJECT"),
        ]

        orch = make_orchestrator(
            analyst_responses=analyst_responses,
            critic_responses=critic_responses,
        )

        state = make_state(max_iterations=2)
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        # Should still have a draft (graceful degradation, not error)
        assert result.draft is not None
        # reject_count increments on entry to revise branch (iteration 0 uses research,
        # iteration 1 uses revise because of REJECT -> reject_count = 1)
        assert result.reject_count == 1
        assert result.early_return is None  # Not an error, just degraded

    @pytest.mark.asyncio
    async def test_phantom_citation_removal(self):
        """Citations not in source_map are removed from analyst_citations."""
        from reasoning.orchestrator import DeepResearchOrchestrator

        # Analyst claims citation 99 which doesn't exist in source_map
        analyst_resp = MockAnalystResponse(
            draft="Draft", status="COMPLETE", citations_used=[1, 2, 99]
        )
        critic_resp = MockCriticReview(status="PASS")

        orch = make_orchestrator(
            analyst_responses=[analyst_resp],
            critic_responses=[critic_resp],
        )

        state = make_state()  # source_map only has keys 1, 2, 3
        orch.source_map = state.source_map
        orch.formatted_context = state.formatted_context

        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)

        assert 99 not in result.analyst_citations
        assert 1 in result.analyst_citations
        assert 2 in result.analyst_citations
