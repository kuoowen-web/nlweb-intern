"""Task 1 (DR-parity revise loop): mini-reasoning REJECT→analyst.revise()→re-review.

LLM-safe: Analyst.research / Analyst.revise / Critic.review 全 mock，不打真 LLM。
參考既有 pattern: test_loop_engine_gap_routing.py。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_engine(handler_kwargs=None):
    from reasoning.live_research.loop_engine import BABLoopEngine
    handler_kw = {"query_params": {}, "site": "all", "enable_web_search": False}
    if handler_kwargs:
        handler_kw.update(handler_kwargs)
    handler = MagicMock(**handler_kw)
    engine = BABLoopEngine(associator=MagicMock(), handler=handler, max_iterations=1)
    engine._current_iteration = 1
    engine._current_topic_id = "topic_x"
    # state with evidence_usage / knowledge_graph slots
    from reasoning.live_research.stage_state import LiveResearchStageState
    engine.state = LiveResearchStageState()
    # evidence_pool 須含 analyst 引用的 eid（invariant #3：eid 必須在 pool）
    from reasoning.schemas_live import EvidencePoolEntry
    engine.evidence_pool = {1: EvidencePoolEntry(evidence_id=1, title="t", url="u")}
    return engine


def _make_analyst_output(status_draft_graph):
    """構造 mock AnalystResearchOutputLive-like 物件。"""
    draft, nodes = status_draft_graph
    out = MagicMock()
    out.draft = draft
    out.status = "DRAFT_READY"
    out.gap_resolutions = []
    out.knowledge_graph = None
    out.argument_graph = nodes
    return out


def _make_node(claim, eid):
    node = MagicMock()
    node.claim = claim
    node.reasoning_type = "induction"
    node.confidence = "medium"
    node.evidence_ids = [eid]
    return node


@pytest.mark.asyncio
async def test_reject_then_revise_pass_uses_revised_output(monkeypatch):
    """Critic REJECT → analyst.revise() 被叫一次 → revise 回 PASS → revised claims 以 PASS 入庫。"""
    engine = _make_engine()

    revised_node = _make_node("revised claim", 1)
    revised_output = _make_analyst_output(("revised draft", [revised_node]))

    orig_node = _make_node("original claim", 1)
    orig_output = _make_analyst_output(("original draft", [orig_node]))

    fake_analyst = MagicMock()
    fake_analyst.research = AsyncMock(return_value=orig_output)
    fake_analyst.revise = AsyncMock(return_value=revised_output)
    fake_critic = MagicMock()
    # 第一次 review → REJECT，第二次（revise 後）→ PASS
    fake_critic.review = AsyncMock(side_effect=[
        MagicMock(status="REJECT"),
        MagicMock(status="PASS"),
    ])

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h: fake_analyst)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h: fake_critic)

    cm = MagicMock()
    cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "formatted results text")

    # revise 被叫剛好一次
    fake_analyst.revise.assert_awaited_once()
    # 索引進 evidence_usage 的 claim 是 revised 的、critic_status=PASS
    usage = engine.state.evidence_usage.get(1, [])
    assert any(u["claim"] == "revised claim" and u["critic_status"] == "PASS" for u in usage)
    assert not any(u["claim"] == "original claim" for u in usage)


@pytest.mark.asyncio
async def test_revise_still_reject_keeps_forensic_trail(monkeypatch):
    """revise 後仍 REJECT（達上限 1 輪）→ revised claims 以 REJECT 入庫（forensic），rejected_claims_log append。"""
    engine = _make_engine()
    orig_output = _make_analyst_output(("original draft", [_make_node("c1", 1)]))
    revised_output = _make_analyst_output(("revised draft", [_make_node("c1r", 1)]))
    fake_analyst = MagicMock()
    fake_analyst.research = AsyncMock(return_value=orig_output)
    fake_analyst.revise = AsyncMock(return_value=revised_output)
    fake_critic = MagicMock()
    fake_critic.review = AsyncMock(side_effect=[
        MagicMock(status="REJECT"), MagicMock(status="REJECT"),
    ])
    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h: fake_analyst)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h: fake_critic)
    cm = MagicMock(); cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "formatted results")
    fake_analyst.revise.assert_awaited_once()  # 上限 1 輪
    usage = engine.state.evidence_usage.get(1, [])
    assert any(u["critic_status"] == "REJECT" for u in usage)
    assert engine.state.rejected_claims_log  # forensic log 有 append


@pytest.mark.asyncio
async def test_revise_exception_emits_degraded_narration(monkeypatch):
    """revise 拋例外 → emit 降級旁白（lr_copy 常數）+ 退回原 REJECT 入庫，不擋流程。"""
    from reasoning.live_research import lr_copy
    engine = _make_engine()
    emitted = []
    engine._emit_narration = AsyncMock(side_effect=lambda m: emitted.append(m))
    orig_output = _make_analyst_output(("original draft", [_make_node("c1", 1)]))
    fake_analyst = MagicMock()
    fake_analyst.research = AsyncMock(return_value=orig_output)
    fake_analyst.revise = AsyncMock(side_effect=RuntimeError("boom"))
    fake_critic = MagicMock()
    fake_critic.review = AsyncMock(return_value=MagicMock(status="REJECT"))
    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h: fake_analyst)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h: fake_critic)
    cm = MagicMock(); cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "formatted results")
    assert lr_copy.MINI_REASONING_REVISE_DEGRADED_NARRATION in emitted
