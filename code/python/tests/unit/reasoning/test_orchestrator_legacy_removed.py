import reasoning.orchestrator as orch


def test_run_research_legacy_method_removed():
    """死碼移除後 _run_research_legacy 不應再存在。"""
    assert not hasattr(orch.DeepResearchOrchestrator, "_run_research_legacy"), \
        "_run_research_legacy 仍存在 — 死碼未移除"
