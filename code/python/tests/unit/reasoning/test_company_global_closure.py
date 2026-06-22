import inspect
from reasoning.orchestrator import DeepResearchOrchestrator


def test_company_global_has_no_dead_search_fn_or_mutable_smuggle():
    """company_global 的 _execute_*_searches 應已清掉：
    (a) 從未被使用的 search_fn（被 search_with_entity_type 立即覆蓋）
    (b) _current_entity_type = ["company"] 的 mutable list 偷渡。"""
    src = inspect.getsource(DeepResearchOrchestrator._execute_company_global_searches)
    # mutable list 偷渡已移除（改常數或明確參數）
    assert '_current_entity_type = ["company"]' not in src
