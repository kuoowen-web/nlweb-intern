"""
Unit tests for the DR zero-results → web-search short-circuit (β path).

Covers the new `_attempt_zero_results_web_search` helper and its wiring into
`_phase_filter_and_prepare`. Follows the mock pattern in
test_phase_refactor_integration.py: a MagicMock orchestrator with selected
methods bound to the REAL implementation, search machinery mocked as AsyncMock.

The real `_phase_filter_and_prepare` runs:
    _filter_and_prepare_sources (1a) -> _format_research_context (1b) -> sync ->
    `if not state.source_map:` -> β branch.
So binding the real phase requires mocking ALL of:
    _filter_and_prepare_sources, _format_research_context, _format_context_shared,
    _emit_phase_event, _process_gap_resolutions.
`_create_no_results_response` is bound REAL (pure builder; T2 asserts its output).
"""

import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reasoning.research_state import ResearchState
from reasoning.orchestrator import DeepResearchOrchestrator
from reasoning.schemas_enhanced import GapResolutionType


# === Fixtures ===

def make_web_source_map(n=2):
    """A non-empty source_map as if web search rebuilt it."""
    return {
        i: {"url": f"https://web.example/{i}", "title": f"Web {i}", "site": "web"}
        for i in range(1, n + 1)
    }


def make_state(**overrides):
    """ResearchState with empty Phase-1 outputs by default (zero-results case)."""
    defaults = dict(
        query="高端訓",
        mode="discovery",
        items=[],
        current_context=[],
        formatted_context="",
        source_map={},
        query_id="zr_q_001",
        enable_web_search=True,
        tracer=None,
    )
    defaults.update(overrides)
    return ResearchState(**defaults)


def make_orchestrator(format_research_result=("", {}), process_side_effect=None):
    """
    Mock orchestrator for binding the real _phase_filter_and_prepare /
    _attempt_zero_results_web_search.

    format_research_result: (formatted_context, source_map) returned by the mocked
        Phase-1b _format_research_context — controls whether retrieval is "empty".
    process_side_effect: async callable assigned to _process_gap_resolutions
        (e.g. to append to current_context, or raise).
    """
    orch = MagicMock()
    orch.logger = MagicMock()
    orch.formatted_context = ""
    orch.source_map = {}

    # Phase 1a: returns the filtered context list (empty for zero-results).
    orch._filter_and_prepare_sources = AsyncMock(return_value=[])

    # Phase 1b: returns (formatted_context, source_map).
    orch._format_research_context = AsyncMock(return_value=format_research_result)

    # β re-format helper (step 7): rebuild contiguous map from enriched context.
    orch._format_context_shared = MagicMock(
        return_value=("[1] Web 1\n[2] Web 2", make_web_source_map())
    )

    orch._emit_phase_event = AsyncMock()

    if process_side_effect is None:
        orch._process_gap_resolutions = AsyncMock()
    else:
        orch._process_gap_resolutions = AsyncMock(side_effect=process_side_effect)

    # Bind the REAL β helper so the bound real _phase_filter_and_prepare invokes the
    # real branch logic (rather than an auto-generated MagicMock). The helper's own
    # collaborators (_process_gap_resolutions / _format_context_shared) stay mocked.
    orch._attempt_zero_results_web_search = (
        DeepResearchOrchestrator._attempt_zero_results_web_search.__get__(orch)
    )

    return orch


async def _run_phase(orch, state):
    return await DeepResearchOrchestrator._phase_filter_and_prepare(orch, state)


async def _run_helper(orch, state):
    return await DeepResearchOrchestrator._attempt_zero_results_web_search(orch, state)


# === Tests ===

class TestZeroResultsWebSearch:

    @pytest.mark.asyncio
    async def test_t1_beta_happy_path(self):
        """enable_web_search=True, empty source_map, web adds >=1 source -> recover."""
        async def append_one(**kwargs):
            kwargs["current_context"].append({"url": "https://web.example/1"})

        orch = make_orchestrator(
            format_research_result=("", {}),
            process_side_effect=append_one,
        )
        state = make_state(enable_web_search=True)

        result = await _run_phase(orch, state)

        # Helper recovered: no early return, fell through.
        assert result.early_return is None
        # Re-format called exactly once.
        orch._format_context_shared.assert_called_once()
        # G2: self.source_map IS state.source_map (same object).
        assert orch.source_map is result.source_map
        assert orch.formatted_context is result.formatted_context
        # source_map is now the non-empty rebuilt map.
        assert result.source_map == make_web_source_map()
        # R6: exactly one ("filter_and_prepare", "completed") emit (fall-through, no double).
        completed_calls = [
            c for c in orch._emit_phase_event.await_args_list
            if c.args == ("filter_and_prepare", "completed")
        ]
        assert len(completed_calls) == 1

    @pytest.mark.asyncio
    async def test_t1_helper_returns_true(self):
        """Direct helper assertion: returns True when a source is added."""
        async def append_one(**kwargs):
            kwargs["current_context"].append({"url": "https://web.example/1"})

        orch = make_orchestrator(process_side_effect=append_one)
        state = make_state(enable_web_search=True)

        recovered = await _run_helper(orch, state)
        assert recovered is True

    @pytest.mark.asyncio
    async def test_t2_web_search_disabled(self):
        """enable_web_search=False -> real no-results, _process_gap_resolutions NOT awaited."""
        orch = make_orchestrator(format_research_result=("", {}))
        # Bind the REAL no-results builder (do not mock).
        orch._create_no_results_response = DeepResearchOrchestrator._create_no_results_response.__get__(orch)

        state = make_state(enable_web_search=False)

        result = await _run_phase(orch, state)

        # Search machinery never invoked.
        orch._process_gap_resolutions.assert_not_awaited()
        # early_return is the real "查無相關資料" shape.
        assert result.early_return is not None
        item = result.early_return[0]
        assert item["url"] == "internal://no-results"
        assert item["name"] == f"查無相關資料：{state.query}"
        assert item["score"] == 0
        # R6: exactly one ("filter_and_prepare", "completed") emit.
        completed_calls = [
            c for c in orch._emit_phase_event.await_args_list
            if c.args == ("filter_and_prepare", "completed")
        ]
        assert len(completed_calls) == 1

    @pytest.mark.asyncio
    async def test_t2_helper_returns_false_when_disabled(self):
        """Direct helper assertion: returns False and does not call search when disabled."""
        orch = make_orchestrator()
        state = make_state(enable_web_search=False)

        recovered = await _run_helper(orch, state)
        assert recovered is False
        orch._process_gap_resolutions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_t3_web_search_returns_nothing(self):
        """enable_web_search=True but web adds 0 -> no-results fallback, no re-format."""
        orch = make_orchestrator(format_research_result=("", {}))  # process is no-op AsyncMock
        orch._create_no_results_response = DeepResearchOrchestrator._create_no_results_response.__get__(orch)
        state = make_state(enable_web_search=True)

        result = await _run_phase(orch, state)

        # Search was attempted...
        orch._process_gap_resolutions.assert_awaited_once()
        # ...but added nothing -> no-results preserved (anti-hallucination).
        assert result.early_return is not None
        assert result.early_return[0]["url"] == "internal://no-results"
        # Re-format NOT called (added == 0 short-circuits before step 7).
        orch._format_context_shared.assert_not_called()

    @pytest.mark.asyncio
    async def test_t4_synthetic_gap_correctness(self):
        """Capture the response + kwargs passed to _process_gap_resolutions."""
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            kwargs["current_context"].append({"url": "https://web.example/1"})

        orch = make_orchestrator(process_side_effect=capture)
        state = make_state(enable_web_search=True, mode="reasoning")

        await _run_helper(orch, state)

        response = captured["response"]
        assert len(response.gap_resolutions) == 1
        gap = response.gap_resolutions[0]
        assert gap.resolution == GapResolutionType.WEB_SEARCH
        assert gap.search_query == state.query
        # Defensive: mode kwarg is wired from state.mode.
        assert captured["mode"] == state.mode
        assert captured["enable_web_search"] is True

    @pytest.mark.asyncio
    async def test_t5_non_empty_path_untouched(self):
        """Non-empty source_map -> β never entered; normal completion."""
        non_empty = {1: {"url": "u1"}, 2: {"url": "u2"}}
        orch = make_orchestrator(
            format_research_result=("[1]..[2]..", non_empty)
        )
        state = make_state(source_map={})  # will be overwritten by Phase 1b mock

        result = await _run_phase(orch, state)

        # β helper never ran -> search not awaited.
        orch._process_gap_resolutions.assert_not_awaited()
        # No re-format (helper not entered).
        orch._format_context_shared.assert_not_called()
        # Normal completion.
        assert result.early_return is None
        assert result.source_map == non_empty

    @pytest.mark.asyncio
    async def test_t6_silent_fail_guard(self):
        """_process_gap_resolutions raising must propagate (no bare except in helper)."""
        async def boom(**kwargs):
            raise RuntimeError("google client exploded")

        orch = make_orchestrator(process_side_effect=boom)
        state = make_state(enable_web_search=True)

        with pytest.raises(RuntimeError, match="google client exploded"):
            await _run_helper(orch, state)
