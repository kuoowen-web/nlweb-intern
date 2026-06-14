"""KG merge logic + state.knowledge_graph backward-compat (Track D D1).

Track D D1 (sprint 2026-05-28):
- state.knowledge_graph: Optional[KnowledgeGraph] = None field 新增
- BABLoopEngine._merge_knowledge_graph: name-based entity dedup +
  (src, pred, tgt) triple relationship dedup, evidence_ids set union

D-AMB-1 LOCKED: reuse DR KnowledgeGraph from schemas_enhanced.py
D-AMB-2 LOCKED: merge with name-based dedup
"""
import logging

import pytest


# ============================================================================
# Part A: state.knowledge_graph schema + serialization backward-compat
# ============================================================================


def test_state_knowledge_graph_default_none():
    """LiveResearchStageState() 預設 knowledge_graph=None (sprint pre-Track-D behavior)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    s = LiveResearchStageState()
    assert s.knowledge_graph is None


def test_state_knowledge_graph_roundtrip():
    """state.knowledge_graph 設值後 to_dict → from_dict round-trip 結構完整."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
    )
    s = LiveResearchStageState()
    s.knowledge_graph = KnowledgeGraph(
        entities=[
            Entity(
                name="Cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[1, 2],
            ),
        ],
        relationships=[],
    )
    d = s.to_dict()
    assert d["knowledge_graph"]["entities"][0]["name"] == "Cayenne"
    s2 = LiveResearchStageState.from_dict(d)
    assert s2.knowledge_graph is not None
    assert s2.knowledge_graph.entities[0].name == "Cayenne"
    assert s2.knowledge_graph.entities[0].evidence_ids == [1, 2]


def test_state_knowledge_graph_backward_compat_missing():
    """舊 session (v1/v2 早期) 無 knowledge_graph 欄位 → load 後 None,
    pipeline pass-through (沿 Track E pattern)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    legacy_payload = {
        "current_stage": 5,
        "stage_status": "completed",
        # 故意省略 knowledge_graph
    }
    s = LiveResearchStageState.from_dict(legacy_payload)
    assert s.knowledge_graph is None


def test_state_knowledge_graph_backward_compat_invalid_payload(caplog):
    """既有 row 寫進髒資料 (非 dict / invalid KG) → load 後 None + log warning,
    不 raise (沿 Track E time_constraint pattern)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    legacy_payload = {
        "current_stage": 5,
        "knowledge_graph": "not-a-dict-just-a-string",
    }
    with caplog.at_level(logging.WARNING):
        s = LiveResearchStageState.from_dict(legacy_payload)
    assert s.knowledge_graph is None


# ============================================================================
# Part B: BABLoopEngine._merge_knowledge_graph helper unit tests
# ============================================================================


def test_merge_kg_into_none_state_returns_new_kg():
    """state.knowledge_graph=None + new_kg → state = new_kg (fast path)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
    )
    new_kg = KnowledgeGraph(
        entities=[
            Entity(name="X", entity_type=EntityType.CONCEPT, evidence_ids=[1]),
        ],
        relationships=[],
    )
    merged = BABLoopEngine._merge_knowledge_graph(None, new_kg)
    assert len(merged.entities) == 1
    assert merged.entities[0].name == "X"


def test_merge_kg_dedup_by_entity_name_case_insensitive():
    """重複 entity name (case-insensitive) → dedup + evidence_ids set union."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
    )
    state_kg = KnowledgeGraph(
        entities=[
            Entity(
                entity_id="e-1",
                name="Cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[1, 3],
            ),
        ],
        relationships=[],
    )
    new_kg = KnowledgeGraph(
        entities=[
            Entity(
                entity_id="e-2",
                name="cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[5],
            ),
            Entity(
                entity_id="e-3",
                name="新 Entity",
                entity_type=EntityType.CONCEPT,
                evidence_ids=[7],
            ),
        ],
        relationships=[],
    )
    merged = BABLoopEngine._merge_knowledge_graph(state_kg, new_kg)
    assert len(merged.entities) == 2  # Cayenne dedup, 新 Entity 新增
    cayenne = next(e for e in merged.entities if e.name.lower() == "cayenne")
    assert sorted(cayenne.evidence_ids) == [1, 3, 5]
    assert cayenne.entity_id == "e-1"  # 沿用 existing entity_id


def test_merge_kg_relationship_remap_on_entity_dedup():
    """new entity dedup → relationship 的 source/target entity_id 自動 remap 到 existing entity_id."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
        Relationship,
        RelationType,
    )
    state_kg = KnowledgeGraph(
        entities=[
            Entity(entity_id="A1", name="A", entity_type=EntityType.ORGANIZATION),
            Entity(entity_id="B1", name="B", entity_type=EntityType.ORGANIZATION),
        ],
        relationships=[],
    )
    new_kg = KnowledgeGraph(
        entities=[
            Entity(entity_id="A2", name="a", entity_type=EntityType.ORGANIZATION),  # dedup → A1
            Entity(entity_id="B2", name="b", entity_type=EntityType.ORGANIZATION),  # dedup → B1
        ],
        relationships=[
            Relationship(
                source_entity_id="A2",
                target_entity_id="B2",
                relation_type=RelationType.OWNS,
                evidence_ids=[9],
            ),
        ],
    )
    merged = BABLoopEngine._merge_knowledge_graph(state_kg, new_kg)
    # 2 entities (dedup), 1 relationship 加入但 entity_id remap 到 A1/B1
    assert len(merged.entities) == 2
    assert len(merged.relationships) == 1
    rel = merged.relationships[0]
    assert rel.source_entity_id == "A1"
    assert rel.target_entity_id == "B1"


def test_merge_kg_relationship_triple_dedup():
    """重複 (src, pred, tgt) triple → dedup + evidence_ids set union."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
        Relationship,
        RelationType,
    )
    state_kg = KnowledgeGraph(
        entities=[
            Entity(entity_id="A", name="A", entity_type=EntityType.ORGANIZATION),
            Entity(entity_id="B", name="B", entity_type=EntityType.ORGANIZATION),
        ],
        relationships=[
            Relationship(
                source_entity_id="A",
                target_entity_id="B",
                relation_type=RelationType.OWNS,
                evidence_ids=[1],
            ),
        ],
    )
    new_kg = KnowledgeGraph(
        entities=[
            Entity(entity_id="A", name="A", entity_type=EntityType.ORGANIZATION),
            Entity(entity_id="B", name="B", entity_type=EntityType.ORGANIZATION),
        ],
        relationships=[
            Relationship(
                source_entity_id="A",
                target_entity_id="B",
                relation_type=RelationType.OWNS,
                evidence_ids=[5],
            ),
        ],
    )
    merged = BABLoopEngine._merge_knowledge_graph(state_kg, new_kg)
    assert len(merged.relationships) == 1
    assert sorted(merged.relationships[0].evidence_ids) == [1, 5]


def test_merge_kg_empty_new_kg_no_op():
    """new_kg 空 entities → state 不變 (N-5: empty new_kg no-op, info log)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
    )
    state_kg = KnowledgeGraph(
        entities=[
            Entity(name="X", entity_type=EntityType.CONCEPT, evidence_ids=[1]),
        ],
        relationships=[],
    )
    new_kg = KnowledgeGraph(entities=[], relationships=[])
    merged = BABLoopEngine._merge_knowledge_graph(state_kg, new_kg)
    assert len(merged.entities) == 1
    assert merged.entities[0].name == "X"


def test_merge_kg_dangling_relationship_filtered():
    """fix-up round 1 I-4: new_kg 含 dangling relationship (source/target 不在 entities) →
    DR KnowledgeGraph.validate_relationships() 自動 filter (沿 N-7 紀律)，不入 state."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
        Relationship,
        RelationType,
    )
    state_kg = KnowledgeGraph(
        entities=[
            Entity(entity_id="A", name="A", entity_type=EntityType.ORGANIZATION),
        ],
        relationships=[],
    )
    # new_kg 含 dangling rel：target_entity_id 不在 new_kg.entities 也不在 state_kg
    new_kg = KnowledgeGraph(
        entities=[
            Entity(entity_id="B", name="B", entity_type=EntityType.ORGANIZATION),
        ],
        relationships=[
            Relationship(
                source_entity_id="B",
                target_entity_id="GHOST",  # dangling — 不存在
                relation_type=RelationType.OWNS,
                evidence_ids=[1],
            ),
        ],
    )
    # validate_relationships 應自動 filter dangling — construct 階段已 filter
    assert len(new_kg.relationships) == 0, (
        "Pydantic validator 應在 construct 時就 filter dangling"
    )
    merged = BABLoopEngine._merge_knowledge_graph(state_kg, new_kg)
    assert len(merged.entities) == 2  # A + B 都進
    assert len(merged.relationships) == 0  # dangling rel 已被 DR validator filter


def test_merge_kg_internal_duplicate_entity_names_fast_path():
    """fix-up round 1 S-2: new_kg 內部多個同名 entity (LLM duplicate)，
    state_kg=None fast path 直接 return new_kg (不跑 dedup logic)。

    state=None path 走 fast return — dedup 留給後續 iteration
    (sprint design 紀律: state=None 走 fast path)。
    """
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import (
        Entity,
        EntityType,
        KnowledgeGraph,
    )
    state_kg = None  # fresh state
    new_kg = KnowledgeGraph(
        entities=[
            Entity(
                entity_id="x-1",
                name="Cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[1],
            ),
            Entity(
                entity_id="x-2",
                name="cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[2],
            ),
            Entity(
                entity_id="x-3",
                name="CAYENNE",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[3],
            ),
        ],
        relationships=[],
    )
    merged = BABLoopEngine._merge_knowledge_graph(state_kg, new_kg)
    # fast path 不做 dedup
    assert len(merged.entities) == 3
