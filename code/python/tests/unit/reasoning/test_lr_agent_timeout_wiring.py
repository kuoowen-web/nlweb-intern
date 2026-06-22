"""LR analyst/critic/lazy-critic instantiate 接 config timeout 的接線驗證。

修法目標（lr-llm-timeout-config-wiring-fix-plan）：LR 在 DR-parity sprint 漏接
「instantiate 時從 config 讀 timeout」這條線，導致 config 的 analyst_timeout=300 /
critic_timeout=120 對 LR 無效，LR analyst/critic 吃 __init__ 預設 → 大 context 推理
撞 timeout。

本測試斷言 **config 值真的傳進去**（agent.timeout == 設定的 config 值），不只驗
「有帶 timeout 參數」。LLM-safe：完全不打真 LLM（agent 構造被攔截，research/review
均 mock）。

額外驗 schema enum 'supports' 補齊（schemas_enhanced.RelationType）+ 走完 KG
validate_relationships consumer path（plan Task 2 驗收）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers（沿 test_loop_engine_revise_loop.py 既有 pattern）
# ---------------------------------------------------------------------------
def _make_engine():
    from reasoning.live_research.loop_engine import BABLoopEngine
    handler = MagicMock(query_params={}, site="all", enable_web_search=False)
    engine = BABLoopEngine(associator=MagicMock(), handler=handler, max_iterations=1)
    engine._current_iteration = 1
    engine._current_topic_id = "topic_x"
    from reasoning.live_research.stage_state import LiveResearchStageState
    engine.state = LiveResearchStageState()
    from reasoning.schemas_live import EvidencePoolEntry
    engine.evidence_pool = {1: EvidencePoolEntry(evidence_id=1, title="t", url="u")}
    return engine


def _make_analyst_output():
    out = MagicMock()
    out.draft = "draft"
    out.status = "DRAFT_READY"
    out.gap_resolutions = []
    out.knowledge_graph = None
    node = MagicMock()
    node.claim = "c"
    node.reasoning_type = "induction"
    node.confidence = "medium"
    node.evidence_ids = [1]
    out.argument_graph = [node]
    return out


# ---------------------------------------------------------------------------
# Task 1 — mini-reasoning analyst + critic 從 config 讀 timeout
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mini_reasoning_analyst_critic_read_config_timeout(monkeypatch):
    """mock CONFIG.reasoning_params → 斷言 analyst/critic 構造時 timeout == config 值。"""
    import core.config
    monkeypatch.setattr(
        core.config.CONFIG,
        "reasoning_params",
        {"analyst_timeout": 277, "critic_timeout": 188},
        raising=False,
    )

    captured = {}

    def fake_analyst_ctor(handler, timeout=None):
        captured["analyst_timeout"] = timeout
        m = MagicMock()
        m.research = AsyncMock(return_value=_make_analyst_output())
        m.revise = AsyncMock(return_value=_make_analyst_output())
        return m

    def fake_critic_ctor(handler, timeout=None):
        captured["critic_timeout"] = timeout
        m = MagicMock()
        m.review = AsyncMock(return_value=MagicMock(status="PASS"))
        return m

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", fake_analyst_ctor)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", fake_critic_ctor)

    engine = _make_engine()
    cm = MagicMock()
    cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "formatted results text")

    assert captured["analyst_timeout"] == 277, "analyst 未讀到 config analyst_timeout"
    assert captured["critic_timeout"] == 188, "critic 未讀到 config critic_timeout"


@pytest.mark.asyncio
async def test_mini_reasoning_timeout_fallback_when_config_missing(monkeypatch):
    """config 缺 key → fallback 120（對齊 base.py / analyst.py / critic.py 預設）。"""
    import core.config
    monkeypatch.setattr(
        core.config.CONFIG, "reasoning_params", {}, raising=False
    )

    captured = {}

    def fake_analyst_ctor(handler, timeout=None):
        captured["analyst_timeout"] = timeout
        m = MagicMock()
        m.research = AsyncMock(return_value=_make_analyst_output())
        m.revise = AsyncMock(return_value=_make_analyst_output())
        return m

    def fake_critic_ctor(handler, timeout=None):
        captured["critic_timeout"] = timeout
        m = MagicMock()
        m.review = AsyncMock(return_value=MagicMock(status="PASS"))
        return m

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", fake_analyst_ctor)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", fake_critic_ctor)

    engine = _make_engine()
    cm = MagicMock()
    cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "formatted results text")

    assert captured["analyst_timeout"] == 120
    assert captured["critic_timeout"] == 120


# ---------------------------------------------------------------------------
# Task 1 — orchestrator lazy critic property 從 config 讀 timeout
# ---------------------------------------------------------------------------
def test_lr_orchestrator_lazy_critic_reads_config_timeout(monkeypatch):
    """LiveResearchOrchestrator.critic_agent property 構造 CriticAgent 時 timeout == config。"""
    import core.config
    monkeypatch.setattr(
        core.config.CONFIG,
        "reasoning_params",
        {"critic_timeout": 155, "analyst_timeout": 300, "features": {}},
        raising=False,
    )

    captured = {}

    def fake_critic_ctor(handler, timeout=None):
        captured["critic_timeout"] = timeout
        return MagicMock()

    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", fake_critic_ctor)

    # 用最小 stub 直接掛 property，繞過 orchestrator 完整 __init__（避免構造其他 agent）。
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    orch = object.__new__(LiveResearchOrchestrator)
    orch._critic_agent = None
    orch.handler = MagicMock(query_params={})

    _ = orch.critic_agent  # 觸發 lazy init

    assert captured["critic_timeout"] == 155, "lazy critic 未讀到 config critic_timeout"


def test_lr_orchestrator_lazy_critic_fallback_when_config_missing(monkeypatch):
    """config 缺 critic_timeout → fallback 120。"""
    import core.config
    monkeypatch.setattr(
        core.config.CONFIG, "reasoning_params", {}, raising=False
    )

    captured = {}

    def fake_critic_ctor(handler, timeout=None):
        captured["critic_timeout"] = timeout
        return MagicMock()

    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", fake_critic_ctor)

    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    orch = object.__new__(LiveResearchOrchestrator)
    orch._critic_agent = None
    orch.handler = MagicMock(query_params={})

    _ = orch.critic_agent
    assert captured["critic_timeout"] == 120


# ---------------------------------------------------------------------------
# Task 3 — analyst/critic __init__ 預設 60→120（對齊 base.py:168）
# ---------------------------------------------------------------------------
def test_analyst_critic_default_timeout_is_120():
    """未帶 timeout 時 __init__ 預設應為 120（消除 subclass 60 < base 120 矛盾）。"""
    from reasoning.agents.analyst import AnalystAgent
    from reasoning.agents.critic import CriticAgent
    handler = MagicMock(query_params={})
    assert AnalystAgent(handler).timeout == 120
    assert CriticAgent(handler).timeout == 120


# ---------------------------------------------------------------------------
# Task 2 — schema enum 'supports' 補齊 + 走完 consumer path
# ---------------------------------------------------------------------------
def test_relation_type_supports_constructs():
    """RelationType('supports') 可 construct（prod LLM 自然輸出此值）。"""
    from reasoning.schemas_enhanced import RelationType
    assert RelationType("supports") == RelationType.SUPPORTS
    assert RelationType.SUPPORTS.value == "supports"


def test_knowledge_graph_consumes_supports_relation():
    """含 'supports' relation 的 KnowledgeGraph 可建構並走完 validate_relationships，
    不被整包丟棄（這正是 prod bug：缺 enum 值 → analyst KG 輸出被 validation 丟）。"""
    from reasoning.schemas_enhanced import (
        Entity, EntityType, Relationship, RelationType, KnowledgeGraph,
    )
    e1 = Entity(entity_id="A", name="政策", entity_type=EntityType.CONCEPT)
    e2 = Entity(entity_id="B", name="產業成長", entity_type=EntityType.CONCEPT)
    rel = Relationship(
        source_entity_id="A",
        target_entity_id="B",
        relation_type=RelationType.SUPPORTS,
        description="政策支持產業成長",
    )
    kg = KnowledgeGraph(entities=[e1, e2], relationships=[rel])
    # validate_relationships 應保留此 relation（兩端 entity 都存在），不丟棄
    assert len(kg.relationships) == 1
    assert kg.relationships[0].relation_type == RelationType.SUPPORTS
