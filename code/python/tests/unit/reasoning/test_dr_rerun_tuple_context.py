"""DR re-run analyst tuple-item regression (source tier Phase B 140ffb3a).

bug: orchestrator.py 非 isolation re-run 分支 (:850-852) 對 list/tuple item 裸呼
doc.get() -> AttributeError: 'list' object has no attribute 'get'。
Phase B 把 source_tier.filter_and_enrich 改 pass-through，連帶移除原本的
tuple->dict 正規化副作用，secondary-search 的 raw tuple item 流進 current_context
後 re-run analyst 即 crash。

修法：re-run else 分支改走已 tuple-safe 的 _format_context_shared。
本測試直接呼叫 _format_context_shared（與 else 分支修後共用的 formatter），
餵 tuple-format item，驗證 (1) 不丟 AttributeError (2) 內容正確抽出。
"""
import json
import logging

from reasoning.orchestrator import DeepResearchOrchestrator


def _make_orchestrator():
    """Bypass __init__ via __new__；_format_context_shared 只依賴 self.logger。
    沿用 test_dr_context_articlebody_fallback.py 既有 pattern。"""
    orch = DeepResearchOrchestrator.__new__(DeepResearchOrchestrator)
    orch.logger = logging.getLogger("test")
    orch.source_map = {}
    return orch


def test_rerun_format_tuple_item_does_not_crash():
    """list/tuple item（secondary-search raw retriever 格式）走 re-run formatter 不崩。"""
    orch = _make_orchestrator()
    schema = json.dumps({
        "@type": "NewsArticle",
        "headline": "能源政策標題",
        "articleBody": "再生能源佔比與電網韌性的內部語料內文。",
        "source": "cna",
    }, ensure_ascii=False)
    # postgres_client.search() / google_search_client 格式：[url, schema_json, title, site, ?vector]
    tuple_item = ["https://internal.example/energy", schema, "能源政策標題", "cna"]
    # 混合 dict + tuple，模擬初始 dict context + secondary-search 加入的 tuple
    items = [
        {"title": "既有 dict 來源", "site": "web.example", "description": "既有 dict 內文。"},
        tuple_item,
    ]
    # 不可丟 AttributeError
    formatted, source_map = orch._format_context_shared(items, start_id=1)
    assert "能源政策標題" in formatted          # tuple item 的 title 有被抽出
    assert "cna" in formatted                   # tuple item 的 site 有被抽出
    assert len(source_map) == 2                 # dict + tuple 都進 source_map


def test_rerun_format_pure_tuple_list_does_not_crash():
    """全 tuple list（最壞情況：current_context 全是 raw retriever tuple）不崩。"""
    orch = _make_orchestrator()
    schema = json.dumps({"articleBody": "純 tuple 路徑內文。"}, ensure_ascii=False)
    items = [
        ["https://a.example", schema, "標題A", "siteA"],
        ["https://b.example", schema, "標題B", "siteB"],
    ]
    formatted, source_map = orch._format_context_shared(items, start_id=1)
    assert "標題A" in formatted
    assert "標題B" in formatted
    assert len(source_map) == 2


def test_rerun_else_branch_uses_shared_formatter_not_raw_get():
    """Regression guard：非 isolation re-run 分支必須走 _format_context_shared，
    不可回退成對 state.current_context 裸 doc.get()（Phase B P0 根因）。
    Source-level 檢查，因該邏輯 inline 在 async loop 無法純函式呼叫。"""
    import inspect
    from reasoning import orchestrator as orch_mod

    src = inspect.getsource(orch_mod.DeepResearchOrchestrator._phase_actor_critic_loop)
    # 修後：else 分支應呼叫 _format_context_shared
    assert "_format_context_shared" in src
    # 不可再出現「for i, doc in enumerate(state.current_context)」這種裸列舉 + doc.get 的 re-run header
    assert "doc.get('title'" not in src.replace('"', "'"), (
        "re-run else 分支疑似回退成裸 doc.get()，會對 tuple item crash"
    )
    # AR Critical（Blocker 1）防回退：else 分支必須同步更新 state.formatted_context，
    # 否則非 isolation Critic（:937 critic_context = state.formatted_context）吃 stale。
    # 綁定 else 分支特有的這行（只在改後 else 分支出現；不可用
    # "state.formatted_context, state.source_map = ..." 因 :709/:723 既有先例已含該字串、
    # 未修也會 PASS、無區分力 — 閉環複查抓出此點）。
    assert "formatted_context_enriched = state.formatted_context" in src, (
        "re-run else 分支必須四值一起設（formatted_context_enriched = state.formatted_context），"
        "只重建 source_map 會讓非 isolation Critic 吃 stale context（AR Blocker 1）"
    )
