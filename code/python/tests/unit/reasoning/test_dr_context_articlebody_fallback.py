"""DR orchestrator articleBody->snippet fallback tests.

bug: reasoning/orchestrator.py's _format_context_shared /
_build_critic_reference_sheet read description; internal corpus schema only has
articleBody -> DR context / reference sheet snippet empty -> Analyst/Critic
grounding only gets the title.
"""
import json
import logging

from reasoning.orchestrator import DeepResearchOrchestrator


def _make_orchestrator():
    """Bypass __init__ via __new__, only testing pure functions (no LLM / DB).
    _format_context_shared / _build_critic_reference_sheet
    only depend on self.logger / self.source_map."""
    orch = DeepResearchOrchestrator.__new__(DeepResearchOrchestrator)
    orch.logger = logging.getLogger("test")
    orch.source_map = {}
    return orch


def test_format_context_dict_item_fallbacks_to_articleBody():
    """dict item with only articleBody (no description) -> context snippet uses articleBody."""
    orch = _make_orchestrator()
    items = [{
        "title": "Internal Title",
        "site": "cna",
        "articleBody": "Internal corpus body text should appear in DR context snippet.",
    }]
    formatted, _ = orch._format_context_shared(items, start_id=1)
    assert "Internal corpus body text should appear in DR context snippet." in formatted


def test_format_context_tuple_item_fallbacks_to_articleBody():
    """list/tuple item, schema only has articleBody -> context snippet uses articleBody."""
    orch = _make_orchestrator()
    schema = json.dumps({
        "@type": "NewsArticle",
        "headline": "Internal Title",
        "articleBody": "Tuple path DR context internal body should appear.",
        "source": "cna",
    }, ensure_ascii=False)
    items = [["https://internal.example/a", schema, "Internal Title", "cna"]]
    formatted, _ = orch._format_context_shared(items, start_id=1)
    assert "Tuple path DR context internal body should appear." in formatted


def test_format_context_web_description_unchanged():
    """web dict item has description -> behavior unchanged (regression guard)."""
    orch = _make_orchestrator()
    items = [{"title": "web", "site": "web.example", "description": "web context snippet body."}]
    formatted, _ = orch._format_context_shared(items, start_id=1)
    assert "web context snippet body." in formatted


def test_reference_sheet_dict_item_fallbacks_to_articleBody():
    """source_map dict item with only articleBody -> reference sheet snippet uses articleBody."""
    orch = _make_orchestrator()
    orch.source_map = {
        1: {"title": "Internal Title", "site": "cna",
            "articleBody": "Internal corpus body should appear in reference sheet."}
    }
    sheet = orch._build_critic_reference_sheet([1])
    assert "Internal corpus body should appear in reference sheet." in sheet


def test_reference_sheet_tuple_item_fallbacks_to_articleBody():
    """source_map list/tuple item, schema only has articleBody -> snippet uses articleBody."""
    orch = _make_orchestrator()
    schema = json.dumps({
        "@type": "NewsArticle",
        "headline": "Internal Title",
        "articleBody": "Tuple path reference sheet internal body should appear.",
        "source": "cna",
    }, ensure_ascii=False)
    orch.source_map = {2: ["https://internal.example/a", schema, "Internal Title", "cna"]}
    sheet = orch._build_critic_reference_sheet([2])
    assert "Tuple path reference sheet internal body should appear." in sheet


def test_reference_sheet_web_description_unchanged():
    """web item has description -> behavior unchanged (regression guard)."""
    orch = _make_orchestrator()
    orch.source_map = {
        3: {"title": "web", "site": "web.example", "description": "web reference snippet body."}
    }
    sheet = orch._build_critic_reference_sheet([3])
    assert "web reference snippet body." in sheet
