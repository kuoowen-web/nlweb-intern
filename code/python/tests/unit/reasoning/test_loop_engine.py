"""Tests for BABLoopEngine — B->A->B' reusable loop engine."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from reasoning.live_research.loop_engine import BABLoopEngine
from reasoning.schemas_live import (
    ContextMap, ContextMapTopic, ContextMapRelation,
    ContextMapSearchSeed, ContextMapDelta,
    AssociatorBuildOutput, AssociatorDeriveOutput, AssociatorRefineOutput,
    ConsistencyReview,
)


def _make_context_map(version=0, stable=False):
    """Helper: create a minimal ContextMap."""
    return ContextMap(
        research_question="台灣綠能衝突",
        topics=[
            ContextMapTopic(topic_id="t1", name="土地使用", domain="能源政策", relevance="core"),
            ContextMapTopic(topic_id="t2", name="社區參與", domain="治理", relevance="supporting"),
        ],
        version=version,
    )


def _make_build_output():
    return AssociatorBuildOutput(
        context_map=_make_context_map(version=0),
        narration="建立了初始研究結構"
    )


def _make_derive_output():
    return AssociatorDeriveOutput(
        search_seeds=[
            ContextMapSearchSeed(
                query="台灣綠能 土地衝突",
                target_topic_id="t1",
                rationale="核心議題需要資料",
                priority="high",
            )
        ],
        narration="計畫搜尋土地使用相關資料"
    )


def _make_refine_output(is_stable=False, version=1):
    return AssociatorRefineOutput(
        updated_context_map=_make_context_map(version=version),
        delta=ContextMapDelta(
            from_version=version - 1,
            to_version=version,
            reason="加入新資料"
        ),
        is_stable=is_stable,
        narration="更新了研究結構"
    )


def _make_consistency_ok():
    return ConsistencyReview(
        drift_level="none",
        drift_description="方向一致",
        dubao_voice_message="進展順利",
        recommended_action="continue",
    )


class TestBABLoopEngine:
    @pytest.fixture
    def mock_associator(self):
        agent = AsyncMock()
        agent.build_context_map = AsyncMock(return_value=_make_build_output())
        agent.derive_search_plan = AsyncMock(return_value=_make_derive_output())
        agent.refine_context_map = AsyncMock(return_value=_make_refine_output(is_stable=True))
        return agent

    @pytest.fixture
    def mock_handler(self):
        handler = MagicMock()
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        handler.connection_alive_event = MagicMock()
        handler.connection_alive_event.is_set = MagicMock(return_value=True)
        # http_handler: connection alive
        handler.http_handler = MagicMock()
        handler.http_handler.connection_alive = True
        # No soft interrupt — use None so _check_connection doesn't trigger it
        handler._soft_interrupt_event = None
        return handler

    @pytest.fixture
    def engine(self, mock_associator, mock_handler):
        engine = BABLoopEngine(
            associator=mock_associator,
            handler=mock_handler,
            max_iterations=3,
        )
        engine._execute_search = AsyncMock(return_value=("formatted results", {"1": {}}))
        engine._run_mini_reasoning = AsyncMock(return_value=None)
        engine._run_consistency_check = AsyncMock(return_value=_make_consistency_ok())
        return engine

    @pytest.mark.asyncio
    async def test_single_iteration_stable(self, engine, mock_associator):
        """迴圈在 is_stable=true 時停止。"""
        result = await engine.run_loop(query="台灣綠能衝突")
        assert result is not None
        assert result.version == 1  # Refined once
        mock_associator.build_context_map.assert_called_once()
        mock_associator.derive_search_plan.assert_called_once()
        mock_associator.refine_context_map.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_iterations_reached(self, engine, mock_associator):
        """迴圈在 max_iterations 時停止（即使不穩定）。"""
        mock_associator.refine_context_map = AsyncMock(
            return_value=_make_refine_output(is_stable=False, version=1)
        )
        engine.max_iterations = 2
        result = await engine.run_loop(query="台灣綠能衝突")
        assert mock_associator.refine_context_map.call_count == 2

    @pytest.mark.asyncio
    async def test_consistency_pause_breaks_loop(self, engine):
        """Consistency Monitor 建議暫停時中斷迴圈。"""
        engine._run_consistency_check = AsyncMock(return_value=ConsistencyReview(
            drift_level="moderate",
            drift_description="方向偏移",
            dubao_voice_message="等一下...",
            recommended_action="pause_confirm",
        ))
        engine.mock_associator_refine_stable = False
        result = await engine.run_loop(query="台灣綠能衝突")
        # Should break after consistency check, not continue looping
        assert result is not None

    @pytest.mark.asyncio
    async def test_focus_topic_ids_passed_through(self, engine, mock_associator):
        """focus_topic_ids 被傳遞到 derive 和 refine。"""
        await engine.run_loop(query="test", focus_topic_ids=["t1"])
        derive_call = mock_associator.derive_search_plan.call_args
        assert derive_call[1].get("focus_topic_ids") == ["t1"] or \
               (len(derive_call[0]) > 2 and derive_call[0][2] == ["t1"])

    @pytest.mark.asyncio
    async def test_executed_searches_accumulated(self, engine, mock_associator):
        """已執行的搜尋在迭代間累積。"""
        mock_associator.refine_context_map = AsyncMock(
            side_effect=[
                _make_refine_output(is_stable=False, version=1),
                _make_refine_output(is_stable=True, version=2),
            ]
        )
        await engine.run_loop(query="test")
        # Second derive call should include executed searches from first iteration
        second_derive_call = mock_associator.derive_search_plan.call_args_list[1]
        executed = second_derive_call[1].get("executed_searches", [])
        assert len(executed) > 0

    @pytest.mark.asyncio
    async def test_returns_initial_context_map(self, engine):
        """Engine 保留 initial context map（version 0）供 Consistency Monitor。"""
        result = await engine.run_loop(query="test")
        assert engine.initial_context_map is not None
        assert engine.initial_context_map.version == 0


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — BAB Analyst argument_graph indexing
# ============================================================================


@pytest.mark.asyncio
async def test_mini_reasoning_indexes_argument_graph_into_state_evidence_usage(
    monkeypatch
):
    """Track A Task 1: Analyst argument_graph node 跑完後，state.evidence_usage[eid]
    出現對應 GroundedClaim (PASS critic status default)。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import ArgumentNode, LogicType

    fake_analyst_output = MagicMock()
    fake_analyst_output.draft = "draft text"
    fake_analyst_output.argument_graph = [
        ArgumentNode(
            claim="台灣再生能源占比 9.5%",
            evidence_ids=[5, 7],
            reasoning_type=LogicType.INDUCTION,
            confidence="high",
        ),
    ]

    mock_critic_output = MagicMock()
    mock_critic_output.status = "PASS"

    class FakeAnalyst:
        async def research(self, **kwargs):
            return fake_analyst_output

    class FakeCritic:
        async def review(self, **kwargs):
            return mock_critic_output

    monkeypatch.setattr(
        "reasoning.agents.analyst.AnalystAgent",
        lambda handler, timeout=None: FakeAnalyst(),
    )
    monkeypatch.setattr(
        "reasoning.agents.critic.CriticAgent",
        lambda handler, timeout=None: FakeCritic(),
    )

    state = LiveResearchStageState()
    from reasoning.schemas_live import ContextMap, EvidencePoolEntry
    # Fix 2: invariant #3 需要 evidence_pool 含 eid=5, eid=7
    pool = {
        5: EvidencePoolEntry(evidence_id=5, title="t5", url="https://example.com/5", source_domain="example.com"),
        7: EvidencePoolEntry(evidence_id=7, title="t7", url="https://example.com/7", source_domain="example.com"),
    }
    engine = BABLoopEngine(
        associator=MagicMock(),
        handler=MagicMock(),
        max_iterations=1,
        seed_evidence_pool=pool,
    )
    engine._current_iteration = 1
    engine._current_topic_id = "core-1"
    engine.state = state

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])

    await engine._run_mini_reasoning(cm, "[5] x\n[7] y\n")

    # 預期: eid=5 / eid=7 各有一筆 GroundedClaim
    assert 5 in state.evidence_usage
    assert 7 in state.evidence_usage
    gc5 = state.evidence_usage[5][0]
    assert gc5["claim"] == "台灣再生能源占比 9.5%"
    assert gc5["confidence"] == "high"
    assert gc5["source_iteration"] == 1
    assert gc5["source_topic"] == "core-1"
    # default PASS critic_status (Task 6 才會根據 critic 結果調整)
    assert gc5["critic_status"] == "PASS"


@pytest.mark.asyncio
async def test_mini_reasoning_no_state_skips_indexing(monkeypatch):
    """state=None 時 (caller 未注入) → 不索引，pipeline 沿舊行為。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_enhanced import ArgumentNode, LogicType

    fake_analyst_output = MagicMock()
    fake_analyst_output.draft = "draft text"
    fake_analyst_output.argument_graph = [
        ArgumentNode(
            claim="c", evidence_ids=[5],
            reasoning_type=LogicType.INDUCTION, confidence="high",
        ),
    ]

    class FA:
        async def research(self, **kw):
            return fake_analyst_output

    class FC:
        async def review(self, **kw):
            m = MagicMock(); m.status = "PASS"; return m

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
    )
    engine._current_iteration = 1
    # engine.state 默認 None — 不應索引

    from reasoning.schemas_live import ContextMap
    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    # 不應 raise
    await engine._run_mini_reasoning(cm, "[5] x\n")


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — Task 6:
# BAB Critic status marks Analyst claims (REJECT/WARN/PASS marking strategy)
# ============================================================================


@pytest.mark.asyncio
async def test_mini_reasoning_indexes_with_reject_marker_when_critic_rejects(monkeypatch):
    """Gemini Critical 拍板: Critic status=REJECT → Analyst argument_graph **入庫**
    並標記 `critic_status="REJECT"` (forensic trail)；同步 append 一筆到
    `state.rejected_claims_log`。**不丟棄** (source 層保留全部, Task 3 render 層 filter)。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import ArgumentNode, LogicType
    from reasoning.schemas_live import ContextMap

    fake_analyst = MagicMock()
    fake_analyst.draft = "x"
    fake_analyst.argument_graph = [
        ArgumentNode(claim="c", evidence_ids=[5],
                     reasoning_type=LogicType.INDUCTION, confidence="high"),
    ]
    fake_critic_reject = MagicMock(); fake_critic_reject.status = "REJECT"

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_reject

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    from reasoning.schemas_live import EvidencePoolEntry
    # Fix 2: invariant #3 需要 evidence_pool 含 eid=5
    pool5 = {5: EvidencePoolEntry(evidence_id=5, title="t5", url="https://example.com/5", source_domain="example.com")}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool5,
    )
    engine.state = state
    engine._current_iteration = 1
    engine._current_topic_id = "t1"

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[5] x\n")

    # Gemini Critical: REJECT 入庫保留 forensic trail (不丟棄)
    assert 5 in state.evidence_usage
    entry = state.evidence_usage[5][0]
    assert entry["critic_status"] == "REJECT"
    # 雙路追蹤: rejected_claims_log 同步 append (metadata trace)
    assert hasattr(state, "rejected_claims_log")
    assert len(state.rejected_claims_log) >= 1
    last = state.rejected_claims_log[-1]
    assert last["topic_id"] == "t1"
    assert last["iteration"] == 1
    assert last["claim_count"] == 1
    assert last["reason"] == "critic_status_reject"


@pytest.mark.asyncio
async def test_mini_reasoning_indexes_with_low_confidence_when_critic_warns(monkeypatch):
    """Critic status=WARN → claim 進 evidence_usage 但 confidence 降為 low
    且 entry 帶 from_warned_critic_review=True tag。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import ArgumentNode, LogicType
    from reasoning.schemas_live import ContextMap

    fake_analyst = MagicMock()
    fake_analyst.draft = "x"
    fake_analyst.argument_graph = [
        ArgumentNode(claim="c", evidence_ids=[5],
                     reasoning_type=LogicType.INDUCTION, confidence="high"),
    ]
    fake_critic_warn = MagicMock(); fake_critic_warn.status = "WARN"

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_warn

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    from reasoning.schemas_live import EvidencePoolEntry
    pool5 = {5: EvidencePoolEntry(evidence_id=5, title="t5", url="https://example.com/5", source_domain="example.com")}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool5,
    )
    engine.state = state
    engine._current_iteration = 1

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[5] x\n")

    assert 5 in state.evidence_usage
    entry = state.evidence_usage[5][0]
    assert entry["confidence"] == "low"
    # WARN 降級 marker: entry 帶 from_warned_critic_review=True
    assert entry.get("from_warned_critic_review") is True
    # addendum I-5: critic_status 欄位寫入
    assert entry["critic_status"] == "WARN"


@pytest.mark.asyncio
async def test_mini_reasoning_normal_indexing_when_critic_pass(monkeypatch):
    """Critic status=PASS → 正常索引, confidence 保留 Analyst 原值,
    無 from_warned_critic_review tag。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import ArgumentNode, LogicType
    from reasoning.schemas_live import ContextMap, EvidencePoolEntry

    fake_analyst = MagicMock()
    fake_analyst.draft = "x"
    fake_analyst.argument_graph = [
        ArgumentNode(claim="c", evidence_ids=[5],
                     reasoning_type=LogicType.INDUCTION, confidence="high"),
    ]
    fake_critic_pass = MagicMock(); fake_critic_pass.status = "PASS"

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_pass

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    pool5 = {5: EvidencePoolEntry(evidence_id=5, title="t5", url="https://example.com/5", source_domain="example.com")}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool5,
    )
    engine.state = state
    engine._current_iteration = 1

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[5] x\n")

    entry = state.evidence_usage[5][0]
    assert entry["confidence"] == "high"  # 保留 Analyst 原值
    assert not entry.get("from_warned_critic_review")  # 無 WARN tag
    # PASS default critic_status
    assert entry["critic_status"] == "PASS"
    # PASS 不入 rejected_claims_log
    assert len(state.rejected_claims_log) == 0


@pytest.mark.asyncio
async def test_mini_reasoning_critic_unknown_status_defaults_to_pass(monkeypatch):
    """Critic 輸出非法 status (LLM 偶發 hallucination) → 視為 PASS (default
    behavior, 不阻塞 pipeline)。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import ArgumentNode, LogicType
    from reasoning.schemas_live import ContextMap

    fake_analyst = MagicMock()
    fake_analyst.draft = "x"
    fake_analyst.argument_graph = [
        ArgumentNode(claim="c", evidence_ids=[5],
                     reasoning_type=LogicType.INDUCTION, confidence="medium"),
    ]
    fake_critic_unknown = MagicMock(); fake_critic_unknown.status = "UNKNOWN_BLOB"

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_unknown

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    from reasoning.schemas_live import EvidencePoolEntry
    pool5 = {5: EvidencePoolEntry(evidence_id=5, title="t5", url="https://example.com/5", source_domain="example.com")}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool5,
    )
    engine.state = state
    engine._current_iteration = 1

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[5] x\n")

    entry = state.evidence_usage[5][0]
    # 非法 status → 視為 PASS
    assert entry["critic_status"] == "PASS"
    assert entry["confidence"] == "medium"  # 沿用 Analyst 原值


# ============================================================================
# Fix 2：C-4 invariant #3 — eid ∈ evidence_pool 驗證（T1 schema review）
# ============================================================================


def _make_engine_with_pool(monkeypatch, pool_eids, analyst_eids, critic_status_val="PASS"):
    """Helper：構造含指定 evidence_pool + analyst evidence_ids 的 engine。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import EvidencePoolEntry
    from reasoning.schemas_enhanced import ArgumentNode, LogicType

    fake_analyst = MagicMock()
    fake_analyst.draft = "x"
    fake_analyst.argument_graph = [
        ArgumentNode(
            claim="幻覺 claim",
            evidence_ids=analyst_eids,
            reasoning_type=LogicType.INDUCTION,
            confidence="high",
        ),
    ]
    mock_critic = MagicMock()
    mock_critic.status = critic_status_val

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return mock_critic

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    pool = {
        eid: EvidencePoolEntry(
            evidence_id=eid, title=f"標題{eid}", url=f"https://example.com/{eid}",
            source_domain="example.com",
        )
        for eid in pool_eids
    }
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool,
    )
    return engine


@pytest.mark.asyncio
async def test_mini_reasoning_hallucinated_eid_skipped_not_indexed(monkeypatch):
    """Fix 2 — C-4 invariant #3：Analyst 幻覺出 eid=999 不在 evidence_pool 中，
    必須被 skip（不進 evidence_usage），且 warning log 含 'invariant #3'。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import ContextMap
    import reasoning.live_research.loop_engine as le_module

    captured_warnings = []

    def fake_warning(msg, *args, **kwargs):
        captured_warnings.append(msg if not args else (msg % args))

    monkeypatch.setattr(le_module.logger, "warning", fake_warning)

    # evidence_pool 只有 eid 1, 2, 3；Analyst 幻覺出 eid=999
    engine = _make_engine_with_pool(
        monkeypatch, pool_eids=[1, 2, 3], analyst_eids=[1, 999]
    )
    state = LiveResearchStageState()
    engine.state = state
    engine._current_iteration = 1

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[1] real\n")

    # eid=1 應正常索引
    assert 1 in state.evidence_usage
    # eid=999 幻覺 → 不應出現在 evidence_usage
    assert 999 not in state.evidence_usage
    # warning log 必須含 invariant #3 關鍵字
    assert any("invariant #3" in w for w in captured_warnings), \
        f"未看到 invariant #3 warning，captured: {captured_warnings}"


@pytest.mark.asyncio
async def test_mini_reasoning_invariant3_applied_to_warn_branch(monkeypatch):
    """Fix 2 — Critic WARN branch：幻覺 eid 同樣被 skip + warning log。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import ContextMap
    import reasoning.live_research.loop_engine as le_module

    captured_warnings = []

    def fake_warning(msg, *args, **kwargs):
        captured_warnings.append(msg if not args else (msg % args))

    monkeypatch.setattr(le_module.logger, "warning", fake_warning)

    engine = _make_engine_with_pool(
        monkeypatch, pool_eids=[1, 2], analyst_eids=[1, 999], critic_status_val="WARN"
    )
    state = LiveResearchStageState()
    engine.state = state
    engine._current_iteration = 1

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[1] real\n")

    assert 1 in state.evidence_usage
    assert 999 not in state.evidence_usage
    assert any("invariant #3" in w for w in captured_warnings)


@pytest.mark.asyncio
async def test_mini_reasoning_invariant3_applied_to_reject_branch(monkeypatch):
    """Fix 2 — Critic REJECT branch：幻覺 eid 同樣被 skip + warning log。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import ContextMap
    import reasoning.live_research.loop_engine as le_module

    captured_warnings = []

    def fake_warning(msg, *args, **kwargs):
        captured_warnings.append(msg if not args else (msg % args))

    monkeypatch.setattr(le_module.logger, "warning", fake_warning)

    engine = _make_engine_with_pool(
        monkeypatch, pool_eids=[2], analyst_eids=[2, 999], critic_status_val="REJECT"
    )
    state = LiveResearchStageState()
    engine.state = state
    engine._current_iteration = 1

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[2] real\n")

    assert 2 in state.evidence_usage
    assert 999 not in state.evidence_usage
    assert any("invariant #3" in w for w in captured_warnings)


# ============================================================================
# Track E (sprint 2026-05-28) — Task E3: BAB retrieval datePublished filter + query suffix
# ============================================================================

def _make_e3_engine():
    """Helper: minimal BABLoopEngine for E3/E4 tests (no run_loop, only _execute_search)."""
    from reasoning.agents.associator import AssociatorAgent
    handler = MagicMock()
    handler.query_params = {}
    handler.site = 'all'
    engine = BABLoopEngine(
        associator=MagicMock(spec=AssociatorAgent),
        handler=handler,
        max_iterations=1,
    )
    return engine


@pytest.mark.asyncio
async def test_execute_search_passes_date_filter_when_time_constraint_set(monkeypatch):
    """state.time_constraint 非 None → retriever_search 收到 datePublished filter (N-8: kwarg name `filters=`)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import TimeRange

    captured_kwargs = {}

    async def fake_retriever_search(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    state.time_constraint = TimeRange(
        start_date="2024-01-01", end_date=None,
        raw_phrase="2024 之後", user_selected=True,
    )

    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="台灣離岸風電發展",
        target_topic_id="t1",
        rationale="r",
        source_strategy="internal",
    )
    await engine._execute_search([seed])

    # N-8 紀律：禁用 search_filters kwarg，固定 filters=
    assert "search_filters" not in captured_kwargs, (
        f"禁用 search_filters kwarg，必須用 filters= (N-8 紀律); captured: {captured_kwargs}"
    )
    filters = captured_kwargs.get("filters")
    assert filters is not None, f"filters not threaded; captured: {captured_kwargs}"
    assert any(
        f.get("field") == "datePublished" and f.get("operator") == "gte"
        and f.get("value") == "2024-01-01"
        for f in filters
    ), f"datePublished gte filter missing; filters={filters}"


@pytest.mark.asyncio
async def test_execute_search_no_filter_when_time_constraint_none(monkeypatch):
    """state.time_constraint None → retriever_search 不帶 datePublished filter。"""
    from reasoning.live_research.stage_state import LiveResearchStageState

    captured_kwargs = {}

    async def fake_retriever_search(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    # time_constraint stays None

    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="台灣離岸風電發展",
        target_topic_id="t1",
        rationale="r",
        source_strategy="internal",
    )
    await engine._execute_search([seed])

    assert "search_filters" not in captured_kwargs, (
        f"禁用 search_filters kwarg (N-8 紀律); captured: {captured_kwargs}"
    )
    filters = captured_kwargs.get("filters")
    assert not filters or not any(
        f.get("field") == "datePublished" for f in (filters or [])
    ), f"unexpected datePublished filter when no constraint; filters={filters}"


@pytest.mark.asyncio
async def test_execute_search_query_suffix_appended_when_time_constraint(monkeypatch):
    """state.time_constraint 非 None → seed.query 拼上時間 hint suffix (N-4: raw_phrase 優先)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import TimeRange

    captured_queries = []

    async def fake_retriever_search(**kwargs):
        captured_queries.append(kwargs.get("query"))
        return []

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    state.time_constraint = TimeRange(
        start_date="2024-01-01", raw_phrase="2024 之後", user_selected=True,
    )

    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="離岸風電",
        target_topic_id="t1",
        rationale="r",
        source_strategy="internal",
    )
    await engine._execute_search([seed])

    # raw_phrase 優先 (N-4) → query suffix 應含 "2024 之後" 或至少 "2024"
    assert any("2024" in (q or "") for q in captured_queries), f"queries={captured_queries}"


@pytest.mark.asyncio
async def test_execute_search_no_state_no_filter(monkeypatch):
    """engine.state=None (test code / dry-run path) → 不加 filter，不炸。"""
    captured_kwargs = {}

    async def fake_retriever_search(**kwargs):
        captured_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    engine = _make_e3_engine()
    # engine.state remains None (default)

    seed = ContextMapSearchSeed(
        query="x", target_topic_id="t1", rationale="r", source_strategy="internal",
    )
    await engine._execute_search([seed])

    filters = captured_kwargs.get("filters")
    assert not filters or not any(
        f.get("field") == "datePublished" for f in (filters or [])
    )


# ============================================================================
# Track E (sprint 2026-05-28) — Task E4: evidence_pool published_at + filter out-of-range
# ============================================================================

@pytest.mark.asyncio
async def test_evidence_pool_entry_filled_with_published_at(monkeypatch):
    """retrieval item schema 含 datePublished → evidence_pool entry.published_at 被填入。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    import json

    fake_item = (
        "https://news.example.com/article-1",
        json.dumps({
            "description": "2025 年離岸風電裝置容量達 X GW",
            "datePublished": "2025-03-15T10:00:00",
        }),
        "離岸風電裝置容量報告",
        "news.example.com",
    )

    async def fake_retriever_search(**kwargs):
        return [fake_item]

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="離岸風電", target_topic_id="t1", rationale="r", source_strategy="internal",
    )
    await engine._execute_search([seed])

    pool = engine.evidence_pool
    assert len(pool) == 1
    eid = next(iter(pool.keys()))
    entry = pool[eid]
    assert entry.published_at is not None
    assert entry.published_at.startswith("2025-03-15")


@pytest.mark.asyncio
async def test_evidence_pool_skips_out_of_range_items(monkeypatch):
    """retrieval item.datePublished 早於 state.time_constraint.start → 跳過不入庫。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import TimeRange
    import json

    in_range_item = (
        "https://news.example.com/article-2024",
        json.dumps({"description": "2024 年", "datePublished": "2024-06-15T00:00:00"}),
        "2024 年文章", "news.example.com",
    )
    out_of_range_item = (
        "https://news.example.com/article-2018",
        json.dumps({"description": "2018 年", "datePublished": "2018-06-15T00:00:00"}),
        "2018 年文章", "news.example.com",
    )

    async def fake_retriever_search(**kwargs):
        return [in_range_item, out_of_range_item]

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    state.time_constraint = TimeRange(
        start_date="2024-01-01", raw_phrase="2024 之後", user_selected=True,
    )

    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="離岸風電", target_topic_id="t1", rationale="r", source_strategy="internal",
    )
    await engine._execute_search([seed])

    pool = engine.evidence_pool
    assert len(pool) == 1, f"expected 1 in-range item; got pool={pool}"
    entry = next(iter(pool.values()))
    assert entry.published_at.startswith("2024-06")


@pytest.mark.asyncio
async def test_evidence_pool_keeps_no_date_items_when_no_constraint(monkeypatch):
    """retrieval item 無 datePublished + state.time_constraint None → 正常入庫，published_at=None。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    import json

    no_date_item = (
        "https://news.example.com/no-date",
        json.dumps({"description": "未知日期文章"}),  # 無 datePublished
        "未知日期", "news.example.com",
    )

    async def fake_retriever_search(**kwargs):
        return [no_date_item]

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="x", target_topic_id="t1", rationale="r", source_strategy="internal",
    )
    await engine._execute_search([seed])

    pool = engine.evidence_pool
    assert len(pool) == 1
    entry = next(iter(pool.values()))
    assert entry.published_at is None


@pytest.mark.asyncio
async def test_evidence_pool_keeps_no_date_items_when_constraint_set(monkeypatch):
    """retrieval item 無 datePublished + state.time_constraint 有設 → 仍入庫 (N-2 fallback 不過濾)。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import TimeRange
    import json

    no_date_item = (
        "https://x.com/a", json.dumps({"description": "x"}),
        "x", "x.com",
    )

    async def fake_retriever_search(**kwargs):
        return [no_date_item]

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    state.time_constraint = TimeRange(
        start_date="2024-01-01", raw_phrase="2024 後", user_selected=True,
    )

    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="x", target_topic_id="t1", rationale="r", source_strategy="internal",
    )
    await engine._execute_search([seed])

    # 仍入庫（fallback：published_at 缺 → 不過濾）
    assert len(engine.evidence_pool) == 1


@pytest.mark.asyncio
async def test_evidence_pool_end_date_filter(monkeypatch):
    """state.time_constraint.end_date 設 → 晚於 end_date 的 evidence 被過濾。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import TimeRange
    import json

    in_range = (
        "https://x.com/a", json.dumps({"description": "x", "datePublished": "2022-06-01"}),
        "2022", "x.com",
    )
    out_of_range = (
        "https://x.com/b", json.dumps({"description": "y", "datePublished": "2025-01-01"}),
        "2025", "x.com",
    )

    async def fake_retriever_search(**kwargs):
        return [in_range, out_of_range]

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever_search,
    )

    state = LiveResearchStageState()
    state.time_constraint = TimeRange(
        end_date="2023-12-31", raw_phrase="2023 之前", user_selected=True,
    )

    engine = _make_e3_engine()
    engine.state = state

    seed = ContextMapSearchSeed(
        query="x", target_topic_id="t1", rationale="r", source_strategy="internal",
    )
    await engine._execute_search([seed])

    assert len(engine.evidence_pool) == 1
    entry = next(iter(engine.evidence_pool.values()))
    assert entry.published_at.startswith("2022")


# ============================================================================
# Track C (LR DR-parity sprint 2026-05-28) — C2 enable_web_search toggle gate
# ============================================================================


def _make_track_c_engine(enable_web_search=False):
    """Helper: BABLoopEngine fixture for Track C toggle / fallback tests."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    handler = MagicMock(
        query_params={}, site="all", enable_web_search=enable_web_search,
    )
    engine = BABLoopEngine(
        associator=MagicMock(), handler=handler, max_iterations=1,
    )
    return engine


@pytest.mark.asyncio
async def test_execute_search_web_path_blocked_when_toggle_off(monkeypatch):
    """enable_web_search=False 時，即使 seed.source_strategy='web' 也不打 GoogleSearchClient (Track C C2)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=False)
    seed = ContextMapSearchSeed(
        query="德國 Energiewende", source_strategy="web",
        target_topic_id="t1", rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == [], "Web search should not have been called when toggle is off"


@pytest.mark.asyncio
async def test_execute_search_web_path_allowed_when_toggle_on(monkeypatch):
    """enable_web_search=True + seed.source_strategy='web' → 打 GoogleSearchClient (Track C C2)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seed = ContextMapSearchSeed(
        query="德國 Energiewende", source_strategy="web",
        target_topic_id="t1", rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == ["德國 Energiewende"]


# ============================================================================
# Track C C3 — Dual-gate fallback web (F-9 root fix 2026-05-28)
# ============================================================================


@pytest.mark.asyncio
async def test_execute_search_fallback_web_when_internal_empty(monkeypatch):
    """internal 站內回空 + toggle on + seed='internal' + 含國際 keyword → fallback web 觸發一次 (Track C C3)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seed = ContextMapSearchSeed(
        query="德國 Energiewende",
        source_strategy="internal",
        target_topic_id="t1",
        rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == ["德國 Energiewende"], (
        "Fallback web search should fire when internal returns empty + intl keyword"
    )


@pytest.mark.asyncio
async def test_execute_search_no_fallback_when_toggle_off(monkeypatch):
    """toggle off → fallback 不觸發（站內空也不打 web）(Track C C3)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=False)
    seed = ContextMapSearchSeed(
        query="德國 Energiewende", source_strategy="internal",
        target_topic_id="t1", rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == []


@pytest.mark.asyncio
async def test_execute_search_no_fallback_when_seed_already_web(monkeypatch):
    """seed 已經是 'both' (跑過 web) → fallback 不再觸發（避免重複打）(Track C C3)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_calls = []

    async def fake_web_search(self, query):
        web_calls.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seed = ContextMapSearchSeed(
        query="德國 Energiewende", source_strategy="both",
        target_topic_id="t1", rationale="test",
    )
    await engine._execute_search([seed])
    assert len(web_calls) == 1


@pytest.mark.asyncio
async def test_execute_search_no_fallback_when_no_intl_keyword(monkeypatch):
    """F-9 根解：純台灣 query 即使站內空 + toggle on 也不打 web fallback (Track C C3)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seed = ContextMapSearchSeed(
        query="台灣 2024 用電結構",
        source_strategy="internal",
        target_topic_id="t1",
        rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == [], "Fallback web should not fire for pure-Taiwan queries (F-9 root fix)"


@pytest.mark.asyncio
async def test_execute_search_fallback_fires_when_intl_keyword_present(monkeypatch):
    """F-9 根解 positive case：含 intl keyword + 站內空 + toggle on → fallback 觸發 (Track C C3)."""
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seed = ContextMapSearchSeed(
        query="德國 Energiewende 政策", source_strategy="internal",
        target_topic_id="t1", rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == ["德國 Energiewende 政策"]


@pytest.mark.asyncio
async def test_execute_search_fallback_case_insensitive_english_keyword(monkeypatch):
    """C-MIN-1 (Gemini R4): 英文 keyword case-insensitive 比對 (Track C C3).

    user 輸入小寫 'oecd' 應該也能 match 'OECD' keyword。
    """
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        AsyncMock(return_value=[]),
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seed = ContextMapSearchSeed(
        query="oecd 報告 2024", source_strategy="internal",
        target_topic_id="t1", rationale="test",
    )
    await engine._execute_search([seed])
    assert web_called == ["oecd 報告 2024"], (
        "case-insensitive keyword matching should fire fallback for lowercase 'oecd'"
    )


@pytest.mark.asyncio
async def test_execute_search_multi_seed_per_seed_count_invariant(monkeypatch):
    """F-9 per-seed count 紀律：seed N internal=0 不依賴前 seed 累計 (Track C C3).

    構造 3 seeds — seed 1 無 intl keyword internal=2，seed 2 internal=5，
    seed 3 含「德國」internal=0 → 只 seed 3 fire fallback。
    """
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMapSearchSeed
    import json

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    seed1_items = [
        ("https://tw1.com/a", json.dumps({"description": "a"}), "T1", "tw1.com"),
        ("https://tw1.com/b", json.dumps({"description": "b"}), "T2", "tw1.com"),
    ]
    seed2_items = [
        ("https://tw2.com/c", json.dumps({"description": "c"}), "T3", "tw2.com"),
        ("https://tw2.com/d", json.dumps({"description": "d"}), "T4", "tw2.com"),
        ("https://tw2.com/e", json.dumps({"description": "e"}), "T5", "tw2.com"),
        ("https://tw2.com/f", json.dumps({"description": "f"}), "T6", "tw2.com"),
        ("https://tw2.com/g", json.dumps({"description": "g"}), "T7", "tw2.com"),
    ]
    seed3_items = []

    call_count = [0]

    async def fake_retriever(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return seed1_items
        elif call_count[0] == 2:
            return seed2_items
        else:
            return seed3_items

    monkeypatch.setattr(BABLoopEngine, "_execute_web_search", fake_web_search)
    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search",
        fake_retriever,
    )

    engine = _make_track_c_engine(enable_web_search=True)
    seeds = [
        ContextMapSearchSeed(
            query="台灣風電政策", source_strategy="internal",
            target_topic_id="t1", rationale="r",
        ),
        ContextMapSearchSeed(
            query="台灣淨零路徑", source_strategy="internal",
            target_topic_id="t2", rationale="r",
        ),
        ContextMapSearchSeed(
            query="德國 Energiewende", source_strategy="internal",
            target_topic_id="t3", rationale="r",
        ),
    ]
    await engine._execute_search(seeds)
    assert web_called == ["德國 Energiewende"], (
        f"per-seed count invariant violation: expected only seed 3 fallback, got {web_called}"
    )


# ============================================================================
# Track D D1 (sprint 2026-05-28): _run_mini_reasoning KG indexing tests
# ============================================================================


@pytest.mark.asyncio
async def test_run_mini_reasoning_indexes_kg_on_pass_critic(monkeypatch):
    """Critic PASS → analyst_output.knowledge_graph 被 merge 進 state.knowledge_graph."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import (
        ArgumentNode,
        Entity,
        EntityType,
        KnowledgeGraph,
        LogicType,
    )
    from reasoning.schemas_live import ContextMap, EvidencePoolEntry

    fake_kg = KnowledgeGraph(
        entities=[
            Entity(
                name="Cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[1],
            ),
        ],
        relationships=[],
    )
    fake_analyst = MagicMock()
    fake_analyst.draft = "draft text"
    fake_analyst.argument_graph = [
        ArgumentNode(
            claim="c", evidence_ids=[1],
            reasoning_type=LogicType.INDUCTION, confidence="high",
        ),
    ]
    fake_analyst.knowledge_graph = fake_kg
    # 沒有 gap_resolutions（避免觸發 gap routing）
    fake_analyst.gap_resolutions = None

    fake_critic_pass = MagicMock(); fake_critic_pass.status = "PASS"

    class FA:
        async def research(self, **kw):
            # Track D D1: 驗證 enable_kg=True 真的有傳進來
            assert kw.get("enable_kg") is True, (
                "_run_mini_reasoning must pass enable_kg=True to analyst.research()"
            )
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_pass

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    pool1 = {1: EvidencePoolEntry(
        evidence_id=1, title="t1", url="https://example.com/1",
        source_domain="example.com",
    )}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool1,
    )
    engine.state = state
    engine._current_iteration = 1
    engine._current_topic_id = "topic-1"

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[1] x\n")

    # Track D D1 acceptance: KG merged into state
    assert state.knowledge_graph is not None
    assert len(state.knowledge_graph.entities) == 1
    assert state.knowledge_graph.entities[0].name == "Cayenne"


# ── O5a-(2): KG merge 失敗降級旁白（per-run 一次） ──────────────────────────────
async def _assemble_kg_engine(monkeypatch):
    """照抄 test_run_mini_reasoning_indexes_kg_on_pass_critic 的組裝：
    pass-critic + 非空 KG，使流程走進 Track D _merge_knowledge_graph。回 (engine, cm)。
    含 AnalystAgent / CriticAgent 的 monkeypatch.setattr（防打真 LLM）。
    """
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import (
        ArgumentNode,
        Entity,
        EntityType,
        KnowledgeGraph,
        LogicType,
    )
    from reasoning.schemas_live import ContextMap, EvidencePoolEntry

    fake_kg = KnowledgeGraph(
        entities=[
            Entity(
                name="Cayenne",
                entity_type=EntityType.ORGANIZATION,
                evidence_ids=[1],
            ),
        ],
        relationships=[],
    )
    fake_analyst = MagicMock()
    fake_analyst.draft = "draft text"
    fake_analyst.argument_graph = [
        ArgumentNode(
            claim="c", evidence_ids=[1],
            reasoning_type=LogicType.INDUCTION, confidence="high",
        ),
    ]
    fake_analyst.knowledge_graph = fake_kg
    # 沒有 gap_resolutions（避免觸發 gap routing）
    fake_analyst.gap_resolutions = None

    fake_critic_pass = MagicMock(); fake_critic_pass.status = "PASS"

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_pass

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    pool1 = {1: EvidencePoolEntry(
        evidence_id=1, title="t1", url="https://example.com/1",
        source_domain="example.com",
    )}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool1,
    )
    engine.state = state
    engine._current_iteration = 1
    engine._current_topic_id = "topic-1"

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    return engine, cm


@pytest.mark.asyncio
async def test_kg_merge_failure_emits_narration_call_path(monkeypatch):
    """O5a-(2) call-path：KG merge except（[Track D]）觸發時，narration 真的被 emit。

    只測文案常數不夠（常數與 except 接線斷了測試仍綠）；
    這裡讓 _merge_knowledge_graph raise，驗 _run_mini_reasoning 路徑會 narrate。
    """
    engine, cm = await _assemble_kg_engine(monkeypatch)  # 含 AnalystAgent/CriticAgent setattr
    engine._reset_per_run_dedup_flags()  # 確保 flag 初始化（直呼 method 不經 run_loop）

    captured = []
    async def fake_emit(text):
        captured.append(text)
    engine._emit_narration = fake_emit

    def boom(*args, **kwargs):
        raise RuntimeError("KG merge down")
    engine._merge_knowledge_graph = boom

    # 不可 raise（non-fatal）：_run_mini_reasoning 正常 return
    await engine._run_mini_reasoning(cm, "[1] x\n")

    assert captured, "KG merge except 觸發時必須 emit narration"
    assert "知識圖譜" in captured[0] or "圖譜" in captured[0]


@pytest.mark.asyncio
async def test_kg_merge_failure_narration_deduped_per_run(monkeypatch):
    """O5a-(2) per-run dedup：KG merge 持續失敗時，同一 run 內旁白只播一次。

    防 429 貫穿 run 每輪轟炸（原 o5a Task 4 缺此防護，本窄 plan 補上）。
    """
    engine, cm = await _assemble_kg_engine(monkeypatch)
    engine._reset_per_run_dedup_flags()

    captured = []
    async def fake_emit(text):
        captured.append(text)
    engine._emit_narration = fake_emit

    def boom(*args, **kwargs):
        raise RuntimeError("KG merge down")
    engine._merge_knowledge_graph = boom

    # 同一 run 連續兩輪 mini-reasoning，KG merge 都失敗
    await engine._run_mini_reasoning(cm, "[1] x\n")
    await engine._run_mini_reasoning(cm, "[1] y\n")

    assert len(captured) == 1, f"per-run 應只播一次，實得 {len(captured)} 次"


@pytest.mark.asyncio
async def test_kg_merge_dedup_flag_is_reset_by_per_run_reset(monkeypatch):
    """O5a-(2) reset-wiring：_kg_merge_degraded_narrated 必須由 _reset_per_run_dedup_flags
    重置（非僅 __init__）。否則 engine 跨 run 重用時，第二次 run 的 KG 降級旁白被永久靜音。

    這條鎖死本窄 plan 的全部重點：flag 進 reset path（不是只進 __init__）。
    """
    engine, cm = await _assemble_kg_engine(monkeypatch)
    engine._reset_per_run_dedup_flags()

    captured = []
    async def fake_emit(text):
        captured.append(text)
    engine._emit_narration = fake_emit

    def boom(*args, **kwargs):
        raise RuntimeError("KG merge down")
    engine._merge_knowledge_graph = boom

    # 第一次 run：emit 一次後 flag=True
    await engine._run_mini_reasoning(cm, "[1] x\n")
    assert len(captured) == 1
    assert engine._kg_merge_degraded_narrated is True

    # 模擬「新一輪 run 開始」——run_loop 入口會呼叫 _reset_per_run_dedup_flags
    engine._reset_per_run_dedup_flags()
    assert engine._kg_merge_degraded_narrated is False, \
        "flag 未被 _reset_per_run_dedup_flags 重置 — 它沒進 reset path（只放 __init__）"

    # 第二次 run：reset 後必須能再 emit 一次
    await engine._run_mini_reasoning(cm, "[1] y\n")
    assert len(captured) == 2, "reset 後第二次 run 的 KG 降級旁白被永久靜音（reset wiring 斷了）"


# ============================================================================
# 檢索出錯降級旁白（2026-06-20 prod：embedding 雙 provider 同失敗 →
# retriever_search 拋例外 → _execute_search silent 跳過該筆查詢）
# 鏡像 O5a KG merge 旁白 test pattern：call-path / per-run dedup / reset wiring。
# ============================================================================

def _make_retrieval_error_engine(monkeypatch):
    """Helper: retriever_search 持久拋例外的 engine + narration capture。

    query 刻意不含 INTL_KEYWORDS（handler 是 MagicMock，enable_web_search 為
    truthy mock — 含國際 keyword 會誤觸 Track C C3 fallback web 路徑）。
    """
    async def boom_search(**kwargs):
        raise RuntimeError("retrieval backend down")

    monkeypatch.setattr(
        "reasoning.live_research.loop_engine.retriever_search", boom_search,
    )
    engine = _make_e3_engine()
    engine._reset_per_run_dedup_flags()  # 直呼 _execute_search 不經 run_loop
    captured = []

    async def fake_emit(text):
        captured.append(text)
    engine._emit_narration = fake_emit
    return engine, captured


def _retrieval_error_seed():
    return ContextMapSearchSeed(
        query="台灣光電 土地爭議",
        target_topic_id="t1",
        rationale="r",
        source_strategy="internal",
    )


@pytest.mark.asyncio
async def test_retrieval_error_emits_degraded_narration(monkeypatch):
    """call-path：retriever_search 拋例外時 narration 真的被 emit，且 non-fatal
    （_execute_search 正常 return 空結果，不往外炸）。"""
    from reasoning.live_research import lr_copy

    engine, captured = _make_retrieval_error_engine(monkeypatch)

    formatted, source_map = await engine._execute_search([_retrieval_error_seed()])

    assert formatted == "（未找到相關結果）"
    assert source_map == {}
    assert captured == [lr_copy.RETRIEVAL_ERROR_DEGRADED_NARRATION], (
        "檢索例外被 catch 時必須 emit 降級旁白（不可 silent skip）"
    )


@pytest.mark.asyncio
async def test_retrieval_error_narration_deduped_per_run(monkeypatch):
    """per-run dedup：持久性故障（每 seed × 每 iteration 都拋）同一 run 只播一次。"""
    engine, captured = _make_retrieval_error_engine(monkeypatch)

    # 同一 run 內：單次呼叫含兩個 seed + 第二次呼叫（模擬下一輪 iteration）
    await engine._execute_search([_retrieval_error_seed(), _retrieval_error_seed()])
    await engine._execute_search([_retrieval_error_seed()])

    assert len(captured) == 1, f"per-run 應只播一次，實得 {len(captured)} 次"


@pytest.mark.asyncio
async def test_retrieval_error_dedup_flag_is_reset_by_per_run_reset(monkeypatch):
    """reset wiring：flag 必須由 _reset_per_run_dedup_flags 重置（非僅 __init__），
    否則 engine 跨 run 重用時第二次 run 的降級旁白被永久靜音。"""
    engine, captured = _make_retrieval_error_engine(monkeypatch)

    # 第一次 run：emit 一次後 flag=True
    await engine._execute_search([_retrieval_error_seed()])
    assert len(captured) == 1
    assert engine._retrieval_error_degraded_narrated is True

    # 模擬「新一輪 run 開始」——run_loop 入口會呼叫 _reset_per_run_dedup_flags
    engine._reset_per_run_dedup_flags()
    assert engine._retrieval_error_degraded_narrated is False, (
        "flag 未被 _reset_per_run_dedup_flags 重置 — 它沒進 reset path（只放 __init__）"
    )

    # 第二次 run：reset 後必須能再 emit 一次
    await engine._execute_search([_retrieval_error_seed()])
    assert len(captured) == 2, "reset 後第二次 run 的降級旁白被永久靜音（reset wiring 斷了）"


@pytest.mark.asyncio
async def test_run_mini_reasoning_skips_kg_merge_on_reject_critic(monkeypatch):
    """Critic REJECT → KG 不入 state.knowledge_graph (D-AMB-4 LOCKED)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import (
        ArgumentNode,
        Entity,
        EntityType,
        KnowledgeGraph,
        LogicType,
    )
    from reasoning.schemas_live import ContextMap, EvidencePoolEntry

    fake_kg = KnowledgeGraph(
        entities=[
            Entity(
                name="X",
                entity_type=EntityType.CONCEPT,
                evidence_ids=[1],
            ),
        ],
        relationships=[],
    )
    fake_analyst = MagicMock()
    fake_analyst.draft = "draft"
    fake_analyst.argument_graph = [
        ArgumentNode(
            claim="c", evidence_ids=[1],
            reasoning_type=LogicType.INDUCTION, confidence="high",
        ),
    ]
    fake_analyst.knowledge_graph = fake_kg
    fake_analyst.gap_resolutions = None

    fake_critic_reject = MagicMock(); fake_critic_reject.status = "REJECT"

    class FA:
        async def research(self, **kw):
            return fake_analyst

    class FC:
        async def review(self, **kw):
            return fake_critic_reject

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    pool1 = {1: EvidencePoolEntry(
        evidence_id=1, title="t1", url="https://example.com/1",
        source_domain="example.com",
    )}
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=1,
        seed_evidence_pool=pool1,
    )
    engine.state = state
    engine._current_iteration = 1
    engine._current_topic_id = "topic-1"

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[1] x\n")

    # D-AMB-4 LOCKED: REJECT → 不 merge (KG 不入庫)
    assert state.knowledge_graph is None


@pytest.mark.asyncio
async def test_run_mini_reasoning_kg_cross_iteration_dedup(monkeypatch):
    """跨 BAB iteration → 同名 entity 合併 + evidence_ids set union (D-AMB-2)."""
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_enhanced import (
        ArgumentNode,
        Entity,
        EntityType,
        KnowledgeGraph,
        LogicType,
    )
    from reasoning.schemas_live import ContextMap, EvidencePoolEntry

    # Iter 1: Cayenne evidence_ids=[1]
    kg_iter1 = KnowledgeGraph(
        entities=[
            Entity(name="Cayenne", entity_type=EntityType.ORGANIZATION, evidence_ids=[1]),
        ],
        relationships=[],
    )
    # Iter 2: 同名 Cayenne (大小寫不同) evidence_ids=[2] + 新 entity Toyota
    kg_iter2 = KnowledgeGraph(
        entities=[
            Entity(name="cayenne", entity_type=EntityType.ORGANIZATION, evidence_ids=[2]),
            Entity(name="Toyota", entity_type=EntityType.ORGANIZATION, evidence_ids=[3]),
        ],
        relationships=[],
    )

    call_counter = {"i": 0}

    def make_analyst(kg):
        a = MagicMock()
        a.draft = "draft"
        a.argument_graph = [
            ArgumentNode(
                claim="c", evidence_ids=[1],
                reasoning_type=LogicType.INDUCTION, confidence="high",
            ),
        ]
        a.knowledge_graph = kg
        a.gap_resolutions = None
        return a

    class FA:
        async def research(self, **kw):
            call_counter["i"] += 1
            if call_counter["i"] == 1:
                return make_analyst(kg_iter1)
            return make_analyst(kg_iter2)

    fake_critic_pass = MagicMock(); fake_critic_pass.status = "PASS"

    class FC:
        async def review(self, **kw):
            return fake_critic_pass

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FA())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: FC())

    state = LiveResearchStageState()
    pool = {
        eid: EvidencePoolEntry(
            evidence_id=eid, title=f"t{eid}", url=f"https://example.com/{eid}",
            source_domain="example.com",
        ) for eid in (1, 2, 3)
    }
    engine = BABLoopEngine(
        associator=MagicMock(), handler=MagicMock(), max_iterations=2,
        seed_evidence_pool=pool,
    )
    engine.state = state
    engine._current_iteration = 1
    engine._current_topic_id = "topic-1"

    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])

    # Iter 1
    await engine._run_mini_reasoning(cm, "[1] x\n")
    assert len(state.knowledge_graph.entities) == 1
    assert state.knowledge_graph.entities[0].name == "Cayenne"

    # Iter 2 (跨 iteration merge)
    engine._current_iteration = 2
    await engine._run_mini_reasoning(cm, "[2] y\n")

    # D-AMB-2: 跨 iteration name-based dedup
    assert len(state.knowledge_graph.entities) == 2  # Cayenne dedup, Toyota new
    cayenne = next(e for e in state.knowledge_graph.entities if e.name.lower() == "cayenne")
    assert sorted(cayenne.evidence_ids) == [1, 2]  # evidence_ids set union
    toyota = next(e for e in state.knowledge_graph.entities if e.name == "Toyota")
    assert toyota.evidence_ids == [3]


# ============================================================================
# Track F (sprint 2026-05-28) — F2 Consistency Monitor drift log persistence
# ============================================================================

class TestTrackFConsistencyDriftLog:
    """F2: 每輪 BAB iteration 後 append ConsistencyDriftEntry 到 state.consistency_drift_log。"""

    def test_consistency_drift_entry_schema(self):
        from reasoning.schemas_live import ConsistencyDriftEntry
        e = ConsistencyDriftEntry(
            stage="stage_2",
            iteration=2,
            topic_id="t1",
            drift_level="moderate",
            drift_description="偏離研究問題",
            recommended_action="pause_confirm",
        )
        assert e.iteration == 2
        assert e.stage == "stage_2"
        assert e.drift_level == "moderate"

    def test_consistency_drift_entry_minimal(self):
        from reasoning.schemas_live import ConsistencyDriftEntry
        e = ConsistencyDriftEntry(iteration=1, drift_level="none", recommended_action="continue")
        assert e.stage == "stage_1"  # default
        assert e.topic_id == ""
        assert e.drift_description == ""

    def test_consistency_drift_entry_stage_audit_unique_key(self):
        """I-3: (stage, topic_id, iteration) 三元組才是 audit unique key
        （Stage 1 / Stage 2 per-topic invoke 各自有 max_iterations 內部循環，
        單看 iteration 會 overlap）。"""
        from reasoning.schemas_live import ConsistencyDriftEntry
        # Stage 1 iter=1 + Stage 2 topic-A iter=1 + Stage 2 topic-B iter=1
        # 三者 iteration 都 = 1 但屬於不同 audit context
        e1 = ConsistencyDriftEntry(
            stage="stage_1", iteration=1, topic_id="",
            drift_level="none", recommended_action="continue",
        )
        e2 = ConsistencyDriftEntry(
            stage="stage_2", iteration=1, topic_id="A",
            drift_level="none", recommended_action="continue",
        )
        e3 = ConsistencyDriftEntry(
            stage="stage_2", iteration=1, topic_id="B",
            drift_level="none", recommended_action="continue",
        )
        keys = {(e.stage, e.topic_id, e.iteration) for e in [e1, e2, e3]}
        assert len(keys) == 3, "三元組應全 unique"


def _make_handler_for_run_loop():
    """Helper: make MagicMock handler that won't trip soft_interrupt / connection alive checks."""
    handler = MagicMock()
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=True)
    handler.http_handler = MagicMock()
    handler.http_handler.connection_alive = True
    handler._soft_interrupt_event = None  # disable soft interrupt
    handler.query_params = {}
    handler.site = 'all'
    return handler


@pytest.mark.asyncio
async def test_consistency_drift_log_appended_per_iteration():
    """F2: 每輪 BAB iteration 後（_run_consistency_check 跑完）append 一筆 entry to state."""
    from reasoning.live_research.stage_state import LiveResearchStageState

    state = LiveResearchStageState()

    associator = MagicMock()
    associator.build_context_map = AsyncMock(return_value=_make_build_output())
    associator.derive_search_plan = AsyncMock(return_value=_make_derive_output())
    associator.refine_context_map = AsyncMock(
        return_value=_make_refine_output(is_stable=True, version=1)
    )

    engine = BABLoopEngine(
        associator=associator,
        handler=_make_handler_for_run_loop(),
        max_iterations=1,
        dry_run=True,  # skip search & mini-reasoning
    )
    engine.state = state

    # Stub _run_consistency_check 回固定 ConsistencyReview（避免真實 LLM call）
    async def fake_consistency_check(current_map, initial_map):
        return ConsistencyReview(
            drift_level="none",
            drift_description="",
            recommended_action="continue",
            affected_topics=[],
            dubao_voice_message="",
        )
    engine._run_consistency_check = fake_consistency_check

    await engine.run_loop(query="台灣綠能", focus_topic_ids=[])

    # 1 iteration 跑完 → state 應 append 1 筆 entry
    assert len(state.consistency_drift_log) == 1, (
        f"expected 1 entry, got {len(state.consistency_drift_log)}"
    )
    entry = state.consistency_drift_log[0]
    assert entry["iteration"] == 1
    # I-3: entry 必須帶 stage 欄位
    assert entry["stage"] in ("stage_1", "stage_2")
    assert entry["drift_level"] in ("none", "minor", "moderate", "major")
    assert entry["recommended_action"] in ("continue", "pause_confirm")
    assert "timestamp" in entry


@pytest.mark.asyncio
async def test_consistency_drift_log_no_state_skip_no_crash():
    """engine.state=None（legacy caller / dry-run test）→ 不 append、不 crash。"""
    associator = MagicMock()
    associator.build_context_map = AsyncMock(return_value=_make_build_output())
    associator.derive_search_plan = AsyncMock(return_value=_make_derive_output())
    associator.refine_context_map = AsyncMock(
        return_value=_make_refine_output(is_stable=True, version=1)
    )

    engine = BABLoopEngine(
        associator=associator,
        handler=_make_handler_for_run_loop(),
        max_iterations=1,
        dry_run=True,
    )
    # engine.state 維持 None（legacy）

    async def fake_consistency_check(current_map, initial_map):
        return ConsistencyReview(
            drift_level="none",
            drift_description="",
            recommended_action="continue",
            affected_topics=[],
            dubao_voice_message="",
        )
    engine._run_consistency_check = fake_consistency_check

    # 不 raise
    result = await engine.run_loop(query="x", focus_topic_ids=[])
    assert result is not None


@pytest.mark.asyncio
async def test_consistency_drift_log_stage_inject_from_caller():
    """I-3 caller 紀律：caller (orchestrator) set engine._current_stage → entry.stage 對齊。"""
    from reasoning.live_research.stage_state import LiveResearchStageState

    state = LiveResearchStageState()
    associator = MagicMock()
    associator.build_context_map = AsyncMock(return_value=_make_build_output())
    associator.derive_search_plan = AsyncMock(return_value=_make_derive_output())
    associator.refine_context_map = AsyncMock(
        return_value=_make_refine_output(is_stable=True, version=1)
    )

    engine = BABLoopEngine(
        associator=associator,
        handler=_make_handler_for_run_loop(),
        max_iterations=1,
        dry_run=True,
    )
    engine.state = state
    # I-3: caller 模擬 Stage 2 per-topic invoke
    engine._current_stage = "stage_2"
    engine._current_topic_id = "topic-A"

    async def fake_consistency_check(current_map, initial_map):
        return ConsistencyReview(
            drift_level="minor",
            drift_description="輕微偏移",
            recommended_action="continue",
            affected_topics=[],
            dubao_voice_message="",
        )
    engine._run_consistency_check = fake_consistency_check

    await engine.run_loop(query="x", focus_topic_ids=[])

    assert len(state.consistency_drift_log) == 1
    entry = state.consistency_drift_log[0]
    assert entry["stage"] == "stage_2"
    assert entry["topic_id"] == "topic-A"
    assert entry["drift_level"] == "minor"


# ============================================================================
# O5-A: Consistency monitor degraded narration (Tasks 2, 3, 4)
# ============================================================================

class TestConsistencyDegradedNarration:
    """O5-A: BAB loop 一致性監控 LLM 失敗降級時，user 端必有明確訊息。"""

    @pytest.fixture
    def mock_associator(self):
        agent = AsyncMock()
        agent.build_context_map = AsyncMock(return_value=_make_build_output())
        agent.derive_search_plan = AsyncMock(return_value=_make_derive_output())
        agent.refine_context_map = AsyncMock(return_value=_make_refine_output(is_stable=True))
        return agent

    @pytest.fixture
    def mock_handler(self):
        handler = MagicMock()
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        handler.connection_alive_event = MagicMock()
        handler.connection_alive_event.is_set = MagicMock(return_value=True)
        handler.http_handler = MagicMock()
        handler.http_handler.connection_alive = True
        handler._soft_interrupt_event = None
        return handler

    @pytest.fixture
    def engine(self, mock_associator, mock_handler):
        engine = BABLoopEngine(
            associator=mock_associator,
            handler=mock_handler,
            max_iterations=3,
        )
        engine._execute_search = AsyncMock(return_value=("formatted results", {"1": {}}))
        engine._run_mini_reasoning = AsyncMock(return_value=None)
        engine._run_consistency_check = AsyncMock(return_value=_make_consistency_ok())
        return engine

    @pytest.mark.asyncio
    async def test_consistency_check_fallback_marks_degraded(self, engine, monkeypatch):
        """一致性檢查 LLM 失敗時，fallback 回傳 monitor_degraded=True 且 message 為空。

        NOTE: 必須直呼類別方法 BABLoopEngine._run_consistency_check(engine, ...)，
        不可用 engine._run_consistency_check(...)。
        原因：engine fixture (test_loop_engine.py line 101) 已把 instance method
        換成 AsyncMock，直呼 instance 版會繞過真實 except fallback，造成假綠。
        """
        from reasoning.live_research.loop_engine import BABLoopEngine

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated LLM failure")

        # monkeypatch 模組層 ask_llm，讓 _run_consistency_check 內部 import 到假版本
        monkeypatch.setattr("core.llm.ask_llm", boom)

        from reasoning.schemas_live import ContextMap
        # round-2 review Critical：ContextMap 的 research_question 是必填，
        # ContextMap(version=0) 會在 fixture 建構就拋 ValidationError（錯誤原因 fail，破 TDD）。
        cm = ContextMap(research_question="台灣高鐵測試", version=0, topics=[])
        # 直呼類別方法，bypass instance-level AsyncMock
        review = await BABLoopEngine._run_consistency_check(engine, cm, cm)

        assert review.monitor_degraded is True
        assert review.dubao_voice_message == ""
        assert review.recommended_action == "continue"

    @pytest.mark.asyncio
    async def test_degraded_consistency_emits_user_narration(self, engine):
        """降級（monitor_degraded=True 且 message 為空）時，user 收到明確降級旁白。"""
        from reasoning.schemas_live import ConsistencyReview

        engine.enable_consistency_monitor = True
        engine._run_consistency_check = AsyncMock(return_value=ConsistencyReview(
            drift_level="none",
            drift_description="一致性檢查失敗，預設為無漂移",
            dubao_voice_message="",
            recommended_action="continue",
            monitor_degraded=True,
        ))

        emitted = []

        async def capture(text):
            emitted.append(text)

        engine._emit_narration = capture

        await engine.run_loop(query="台灣高鐵")

        # 必須有一條提到「一致性監控暫時無法使用 / 降級」語意的旁白
        assert any("一致性" in t and ("暫時" in t or "降級" in t) for t in emitted), \
            f"degraded 時應 emit 降級旁白，實際 emitted={emitted}"

    @pytest.mark.asyncio
    async def test_degraded_consistency_narration_deduped_per_run(self, engine):
        """round-3：持久降級（每輪都失敗）時，user-facing 降級旁白每次 run 只 emit
        一次（防高頻迴圈訊息轟炸）。audit log 不受此 flag 影響（Task 4 每輪照記）。"""
        from reasoning.schemas_live import ConsistencyReview

        engine.enable_consistency_monitor = True
        engine.max_iterations = 3  # 確保多輪，才能驗 dedupe（單輪無法區分）
        engine._run_consistency_check = AsyncMock(return_value=ConsistencyReview(
            drift_level="none",
            drift_description="一致性檢查失敗，預設為無漂移",
            dubao_voice_message="",
            recommended_action="continue",   # 不 pause，跑滿迴圈
            monitor_degraded=True,
        ))

        emitted = []
        async def capture(text):
            emitted.append(text)
        engine._emit_narration = capture

        await engine.run_loop(query="台灣高鐵")

        degraded_msgs = [t for t in emitted if "一致性" in t and "暫時" in t]
        assert len(degraded_msgs) == 1, \
            f"降級旁白應 per-run 只 emit 一次（防轟炸），實際 {len(degraded_msgs)} 次：{degraded_msgs}"

    @pytest.mark.asyncio
    async def test_degraded_consistency_drift_entry_has_flag(self, engine, mock_associator):
        """降級時，consistency_drift_log 中的 entry 有 monitor_degraded=True。

        注意：engine fixture 已把 _run_consistency_check mock 成回傳 OK review；
        本 test 覆寫為回傳降級 review，然後驗 drift_log entry 有記錄旗標。
        """
        from reasoning.schemas_live import ConsistencyReview
        # round-2 review Critical：bab_state.py / BABLoopState 不存在，
        # 正確型別是 stage_state.LiveResearchStageState（既有 drift log test 也用此）。
        from reasoning.live_research.stage_state import LiveResearchStageState

        engine.enable_consistency_monitor = True
        engine.state = LiveResearchStageState()  # 確保 state 存在
        engine._run_consistency_check = AsyncMock(return_value=ConsistencyReview(
            drift_level="none",
            drift_description="一致性檢查失敗，預設為無漂移",
            dubao_voice_message="",
            recommended_action="continue",
            monitor_degraded=True,
        ))

        await engine.run_loop(query="台灣高鐵")

        assert len(engine.state.consistency_drift_log) >= 1, \
            "至少應有 1 筆 drift log entry"
        entry = engine.state.consistency_drift_log[-1]
        assert entry.get("monitor_degraded") is True, \
            f"drift log entry 應有 monitor_degraded=True，實際 entry={entry}"


# ============================================================================
# 模塊5 Task 1: _execute_web_search reads LR-only max_results_lr from config
# ============================================================================


@pytest.mark.asyncio
async def test_execute_web_search_respects_config_num_results(monkeypatch):
    """_execute_web_search 應讀 LR 專屬 config key tier_6.web_search.max_results_lr
    （default 8，不 fallback 到共用 max_results），不應 hard-code 3。CEO 決策③：
    max_results_lr 設為 8，預期傳給 search_all_sites 的 num_results == 8。
    DR 維持 max_results=5 不受影響（兩鍵互不 fallback）。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    captured = {}

    async def fake_search_all_sites(self_client, query, num_results=5, **kwargs):
        captured["num_results"] = num_results
        return []

    monkeypatch.setattr(
        "retrieval_providers.google_search_client.GoogleSearchClient.search_all_sites",
        fake_search_all_sites,
    )

    # BABLoopEngine.__init__ signature 不接 emit_fn（emit 走 handler.message_sender，
    # 見 _emit_narration）。建構只傳真實參數，handler 給 MagicMock 即可。
    handler = MagicMock()
    engine = BABLoopEngine(
        associator=MagicMock(),
        handler=handler,
    )
    await engine._execute_web_search("test query")

    assert captured.get("num_results") == 8, (
        f"Expected num_results=8 (CEO decision③, from LR-only config key max_results_lr), "
        f"got {captured.get('num_results')}"
    )


# ============================================================================
# 模塊5 Task 2: evidence-sufficiency narration after BAB (channel A — SSE)
# ============================================================================


@pytest.mark.asyncio
async def test_evidence_sufficiency_narration_emitted_after_bab(monkeypatch):
    """BAB 完成後，當 evidence pool 佔理論最大數的比例低於 thin/critical ratio 時，
    應 emit 一條 narration SSE event（reviewer R-G2：比例制判斷）。

    narration 走 BABLoopEngine._emit_narration → handler.message_sender.send_message(
        {"message_type": "live_research_narration", "text": ...})。
    因此捕捉點是 handler.message_sender.send_message 收到的 payload，
    不是建構參數 emit_fn（__init__ 沒有此參數）。

    本測試放 2 筆 evidence；理論最大 = max_results_lr(8) × BAB_QUERIES_PER_RUN(3) = 24，
    ratio = 2/24 ≈ 0.083 < EVIDENCE_CRITICAL_RATIO(0.10) → 走 critical 分支。
    兩個分支的 narration text 都含「資料」，斷言對兩者皆成立。
    """
    from reasoning.live_research.loop_engine import BABLoopEngine

    sent_payloads = []

    async def fake_send_message(payload):
        sent_payloads.append(payload)

    handler = MagicMock()
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = fake_send_message
    handler.site = "all"
    handler.query_params = {}

    engine = BABLoopEngine(
        associator=MagicMock(),
        handler=handler,
    )
    # 模擬 evidence pool 只有 2 筆。ratio = 2/24 ≈ 0.083 < critical ratio 0.10 → 走 critical 分支
    from reasoning.schemas_live import EvidencePoolEntry
    for i in range(1, 3):
        engine.evidence_pool[i] = EvidencePoolEntry(
            evidence_id=i,
            title=f"Article {i}",
            url=f"https://example.com/{i}",
            source_domain="example.com",
            snippet="snippet",
            iteration_origin=1,
        )

    await engine.emit_evidence_sufficiency_narration()

    narrations = [
        p for p in sent_payloads
        if p.get("message_type") == "live_research_narration"
    ]
    assert len(narrations) >= 1, (
        f"Expected a thin-evidence narration payload, got: {sent_payloads}"
    )
    text = narrations[0].get("text", "")
    assert "資料" in text, f"narration text 應提及資料量充分度，got: {text!r}"


# ============================================================================
# O5-B: mini-reasoning 整段失敗時的降級 narration + per-run dedup
# ============================================================================


def _make_engine_with_failing_analyst(monkeypatch):
    """O5-B fixture：Analyst.research 一進 outer try 即 raise → 觸發 outer except。
    照 _make_engine_with_pool 既有 pattern（整類替換 Analyst + Critic）。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    class FailingAnalyst:
        async def research(self, **kw):
            raise RuntimeError("simulated LLM 429")

    class BenignCritic:
        async def review(self, **kw):
            raise AssertionError("Critic 不應被呼叫（Analyst 已先 raise）")

    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: FailingAnalyst())
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: BenignCritic())

    handler = MagicMock()
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()

    engine = BABLoopEngine(
        associator=MagicMock(), handler=handler, max_iterations=1,
    )
    return engine, handler


def _o5b_narrations(handler):
    """從 send_message 的 await 紀錄撈出 narration payloads。"""
    return [
        c.args[0]
        for c in handler.message_sender.send_message.await_args_list
        if c.args and isinstance(c.args[0], dict)
        and c.args[0].get("message_type") == "live_research_narration"
    ]


@pytest.mark.asyncio
async def test_mini_reasoning_failure_emits_degradation_narration(monkeypatch):
    """O5-B: mini-reasoning 整段非致命失敗時，user 端必須收到降級 narration，
    不可只 logger.warning（CLAUDE.md：降級必有 user-facing 訊息）。"""
    from reasoning.schemas_live import ContextMap

    engine, handler = _make_engine_with_failing_analyst(monkeypatch)

    # formatted_results 非空，避開函式開頭的 early-return（現 L947-949 skip 分支）
    cm = ContextMap(research_question="台灣高鐵測試", version=0, topics=[], relations=[])
    await engine._run_mini_reasoning(cm, "[1] 任意非空 evidence 文字\n")

    narrations = _o5b_narrations(handler)
    assert narrations, "mini-reasoning 失敗後沒有送出任何 narration（silent fail）"
    text = narrations[-1]["text"]
    # 降級訊息語意：要讓 user 知道「這步推理出狀況、已略過該輪、可能影響部分內容」
    assert "推理" in text or "分析" in text, f"narration 未提及推理/分析降級: {text!r}"
    assert text.strip() != "", "narration text 不可為空（會被 _emit_narration 吞掉）"


@pytest.mark.asyncio
async def test_mini_reasoning_degradation_narration_deduped_per_run(monkeypatch):
    """O5-B（s3-3 三家收斂 should-fix）：持續性失敗（如 429 貫穿 run）時，
    降級旁白 per-run 只 emit 一次（防高頻迴圈訊息轟炸）。
    對照同檔先例 test_degraded_consistency_narration_deduped_per_run（約 L1853）。
    log 不受 flag 影響（每輪照記）。"""
    from reasoning.schemas_live import ContextMap

    engine, handler = _make_engine_with_failing_analyst(monkeypatch)

    cm = ContextMap(research_question="台灣高鐵測試", version=0, topics=[], relations=[])
    # 模擬同一 run 內連續三輪失敗（run_loop 每輪呼叫一次，現 L218）
    await engine._run_mini_reasoning(cm, "[1] x\n")
    await engine._run_mini_reasoning(cm, "[1] x\n")
    await engine._run_mini_reasoning(cm, "[1] x\n")

    narrations = _o5b_narrations(handler)
    assert len(narrations) == 1, \
        f"降級旁白應 per-run 只 emit 一次（防轟炸），實際 {len(narrations)} 次：{narrations}"


def test_analyst_research_kwargs_builder():
    """Task 15 (behavior-preserving refactor): 兩處 analyst.research(...) kwargs
    完全相同，抽 _build_analyst_research_kwargs 共用。本測試鎖定 builder 產出的
    kwargs 與兩 call site 原本傳的一致。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    eng = BABLoopEngine.__new__(BABLoopEngine)

    class H:
        enable_web_search = True

    eng.handler = H()
    kw = eng._build_analyst_research_kwargs(
        research_question="Q", enriched_context="CTX", cm_summary="SUM")
    assert kw["query"] == "Q"
    assert kw["formatted_context"] == "CTX"
    assert kw["mode"] == "discovery"
    assert kw["enable_live_research"] is True
    assert kw["context_map_summary"] == "SUM"
    assert kw["enable_kg"] is True
    assert kw["enable_web_search"] is True


import pytest


@pytest.mark.asyncio
async def test_narrate_once_emits_only_first_time(monkeypatch):
    from reasoning.live_research.loop_engine import BABLoopEngine
    eng = BABLoopEngine.__new__(BABLoopEngine)
    emitted = []
    async def fake_emit(text): emitted.append(text)
    eng._emit_narration = fake_emit
    # 旗標屬 per-run dedup 旗標族；裸 instance 預設視為未發過（getattr default False）
    await eng._narrate_once("_test_flag_narrated", "降級訊息")
    await eng._narrate_once("_test_flag_narrated", "降級訊息")  # 第二次不應再 emit
    assert emitted == ["降級訊息"]


def test_reset_per_run_dedup_flags_covers_narrate_once_flags():
    """_narrate_once 每個 call-site 實際傳入的 flag 名都必須由 _reset_per_run_dedup_flags
    重置（per-run 語意，防 engine 池化重用時第二次 run 被永久靜音 — F2）。"""
    import ast
    import inspect
    from reasoning.live_research.loop_engine import BABLoopEngine

    loop_src = inspect.getsource(BABLoopEngine)
    reset_src = inspect.getsource(BABLoopEngine._reset_per_run_dedup_flags)

    tree = ast.parse(loop_src)
    narrate_once_flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_narrate_once"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            narrate_once_flags.add(node.args[0].value)

    assert narrate_once_flags, "未從 source 枚舉到任何 _narrate_once call-site flag 名"

    for flag in sorted(narrate_once_flags):
        assert flag in reset_src, (
            f"{flag} 是 _narrate_once 的 call-site flag，但未在 _reset_per_run_dedup_flags "
            f"登記 → engine 池化重用時第二次 run 該降級旁白被永久靜音（F2 silent fail）"
        )
