"""Graph fallback：actor-critic 多輪迭代中，最終輪 analyst 輸出省略 graph（KG/argument/推論鏈）
時，用本 run 前輪的非空版本補回——防 gap-enrichment/revise 輪 LLM 非決定性省略導致產出蒸發。

背景（2026-07-15 rerun E2E first-hand 撞出，主 run 同樣暴露）：
- research 輪 KG 10 entities + 10 relationships；gap enrichment 後 enriched 輪 LLM 省略 KG
  （KnowledgeGraph 空物件）→ pipeline 拿最終輪 serialize → 前端收到 0+0 空殼。
- 陷阱：KnowledgeGraph() 空物件是 truthy（pydantic BaseModel），`if analyst_output.knowledge_graph:`
  擋不住 → 需要「有意義內容」判準（entities 或 relationships 非空）。
"""
import os, sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.schemas_enhanced import (
    AnalystResearchOutputEnhancedKG,
    KnowledgeGraph,
    Entity,
    ArgumentNode,
    ReasoningChainAnalysis,
)


def _mk_response(kg=None, ag=None, rca=None):
    """建最小合法 analyst 輸出（DRAFT_READY 需 draft ≥100 字元）。"""
    return AnalystResearchOutputEnhancedKG(
        status="DRAFT_READY",
        draft="x" * 120,
        reasoning_chain="reasoning",
        knowledge_graph=kg,
        argument_graph=ag,
        reasoning_chain_analysis=rca,
    )


def _mk_kg(n=1):
    ents = [Entity(name=f"實體{i}", entity_type="concept") for i in range(n)]
    return KnowledgeGraph(entities=ents, relationships=[])


def test_empty_kg_object_is_not_content():
    """釘死 truthy 陷阱：KnowledgeGraph 空物件（entities=[] rels=[]）不算有內容。"""
    from reasoning.orchestrator import _graph_field_has_content
    assert _graph_field_has_content("knowledge_graph", KnowledgeGraph()) is False
    assert _graph_field_has_content("knowledge_graph", None) is False
    assert _graph_field_has_content("knowledge_graph", _mk_kg(1)) is True
    # 只有 relationships 也算有內容（極端 case，不丟）
    kg_rel_only = KnowledgeGraph(entities=[], relationships=[])
    assert _graph_field_has_content("knowledge_graph", kg_rel_only) is False


def test_track_records_latest_nonempty_graphs():
    """兩輪：第一輪 KG 有料、第二輪空殼 → tracker 保留第一輪版本。"""
    from reasoning.orchestrator import track_nonempty_graphs
    tracker = {}
    r1 = _mk_response(kg=_mk_kg(3))
    track_nonempty_graphs(r1, tracker)
    assert tracker["knowledge_graph"] is r1.knowledge_graph

    r2 = _mk_response(kg=KnowledgeGraph())  # enriched 輪省略（空殼）
    track_nonempty_graphs(r2, tracker)
    assert tracker["knowledge_graph"] is r1.knowledge_graph, "空殼不可覆蓋已記錄的非空版本"


def test_track_prefers_newer_nonempty():
    """兩輪都非空 → tracker 記最新一輪（最終輪語意優先，只在空時才回退）。"""
    from reasoning.orchestrator import track_nonempty_graphs
    tracker = {}
    r1 = _mk_response(kg=_mk_kg(3))
    r2 = _mk_response(kg=_mk_kg(5))
    track_nonempty_graphs(r1, tracker)
    track_nonempty_graphs(r2, tracker)
    assert tracker["knowledge_graph"] is r2.knowledge_graph


def test_apply_fallback_restores_kg_when_final_empty():
    """最終輪 KG 空殼 + tracker 有前輪版本 → 補回；回傳 restored 欄位清單（可觀測）。"""
    from reasoning.orchestrator import track_nonempty_graphs, apply_graph_fallback
    tracker = {}
    r1 = _mk_response(kg=_mk_kg(3))
    track_nonempty_graphs(r1, tracker)

    final = _mk_response(kg=KnowledgeGraph())
    restored_resp, restored_fields = apply_graph_fallback(final, tracker)
    assert restored_fields == ["knowledge_graph"]
    assert len(restored_resp.knowledge_graph.entities) == 3, "空殼必須被前輪 3 實體版本補回"
    # 其他欄位不可被動到
    assert restored_resp.draft == final.draft


def test_apply_fallback_noop_when_final_has_content():
    """最終輪自己有 KG → 不覆蓋（最終輪語意優先）。"""
    from reasoning.orchestrator import track_nonempty_graphs, apply_graph_fallback
    tracker = {}
    track_nonempty_graphs(_mk_response(kg=_mk_kg(3)), tracker)

    final = _mk_response(kg=_mk_kg(7))
    restored_resp, restored_fields = apply_graph_fallback(final, tracker)
    assert restored_fields == []
    assert restored_resp is final, "無補回時不做 model_copy（零開銷）"
    assert len(restored_resp.knowledge_graph.entities) == 7


def test_apply_fallback_noop_when_tracker_empty():
    """整 run 全輪都空（tracker 空）→ 不補、不 crash。"""
    from reasoning.orchestrator import apply_graph_fallback
    final = _mk_response(kg=KnowledgeGraph())
    restored_resp, restored_fields = apply_graph_fallback(final, {})
    assert restored_fields == []
    assert restored_resp is final


def test_argument_graph_and_reasoning_chain_same_mechanism():
    """argument_graph（list）與 reasoning_chain_analysis（物件）同機制 fallback。"""
    from reasoning.orchestrator import track_nonempty_graphs, apply_graph_fallback
    tracker = {}
    ag = [ArgumentNode(claim="主張一")]
    rca = ReasoningChainAnalysis(total_nodes=1, max_depth=1)
    r1 = _mk_response(kg=_mk_kg(2), ag=ag, rca=rca)
    track_nonempty_graphs(r1, tracker)

    # 最終輪三欄位全空（None / 空殼）
    final = _mk_response(kg=KnowledgeGraph(), ag=None, rca=None)
    restored_resp, restored_fields = apply_graph_fallback(final, tracker)
    assert restored_fields == ["argument_graph", "knowledge_graph", "reasoning_chain_analysis"]
    assert restored_resp.argument_graph[0].claim == "主張一"
    assert restored_resp.reasoning_chain_analysis.total_nodes == 1
    assert len(restored_resp.knowledge_graph.entities) == 2
