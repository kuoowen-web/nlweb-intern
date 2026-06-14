import pytest
from reasoning.research_state import ResearchState


def test_research_state_defaults():
    """Verify ResearchState creates with correct defaults."""
    state = ResearchState(
        query="test query",
        mode="discovery",
        items=[{"title": "test"}],
    )
    assert state.query == "test query"
    assert state.mode == "discovery"
    assert len(state.items) == 1
    assert state.formatted_context == ""
    assert state.source_map == {}
    assert state.draft is None
    assert state.review is None
    assert state.iteration == 0
    assert state.reject_count == 0
    assert state.enable_isolation is False
    assert state.max_iterations == 3
    assert state.early_return is None
    assert state.result is None


def test_research_state_mutable_defaults_are_independent():
    """Verify mutable defaults don't share state between instances."""
    s1 = ResearchState(query="q1", mode="strict", items=[])
    s2 = ResearchState(query="q2", mode="discovery", items=[])
    s1.current_context.append({"x": 1})
    s1.source_map[1] = {"url": "test"}
    s1.seen_citation_ids.add(1)
    assert len(s2.current_context) == 0
    assert len(s2.source_map) == 0
    assert len(s2.seen_citation_ids) == 0


def test_research_state_with_all_inputs():
    """Verify all input fields are set correctly."""
    state = ResearchState(
        query="query",
        mode="monitor",
        items=[],
        temporal_context={"is_temporal_query": True},
        enable_kg=True,
        enable_web_search=True,
        query_id="test_123",
    )
    assert state.enable_kg is True
    assert state.enable_web_search is True
    assert state.query_id == "test_123"
    assert state.temporal_context["is_temporal_query"] is True
