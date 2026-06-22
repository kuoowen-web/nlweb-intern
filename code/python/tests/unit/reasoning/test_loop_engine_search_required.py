"""Task 2 (DR-parity): Analyst SEARCH_REQUIRED → _execute_search 補搜 → 重跑 Analyst.

LLM-safe: 全 mock，不打真 LLM。

可引用性（M-1b / CEO 2026-06-12 拍板）：補搜到的新 evidence 經 _execute_search
side-effect 寫入 self.evidence_pool（loop_engine.py:540），BAB 結束後 orchestrator
以 serialize_evidence_pool(engine.evidence_pool) 持久化進 state.evidence_pool_json
（orchestrator.py:938/1668），outline planner 再 deserialize 全 pool 做 per-chapter
planned_evidence_ids 分配（orchestrator.py:3471），render_grounded_narrative 以
chapter_eids=planned_evidence_ids 餵 writer findings → 補搜 eid 天然 writer 可引用。
test_secondary_search_eid_reaches_evidence_pool 驗此橋接的第一段（eid 進 pool）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_engine():
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.live_research.stage_state import LiveResearchStageState
    handler = MagicMock(query_params={}, site="all", enable_web_search=False)
    engine = BABLoopEngine(associator=MagicMock(), handler=handler, max_iterations=1)
    engine._current_iteration = 1
    engine._current_topic_id = "topic_x"
    engine.state = LiveResearchStageState()
    engine.evidence_pool = {}
    return engine


def _analyst_out(status, draft="", new_queries=None):
    out = MagicMock()
    out.status = status
    out.draft = draft
    out.new_queries = new_queries or []
    out.gap_resolutions = []
    out.knowledge_graph = None
    out.argument_graph = []
    return out


@pytest.mark.asyncio
async def test_search_required_triggers_secondary_search_and_rerun(monkeypatch):
    """第一次 Analyst 回 SEARCH_REQUIRED → _execute_search 被叫 → 第二次 Analyst 回 DRAFT_READY。"""
    engine = _make_engine()
    # _execute_search mock：回 (formatted, source_map)
    engine._execute_search = AsyncMock(return_value=("補到的新資料文字", {}))

    first = _analyst_out("SEARCH_REQUIRED", draft="", new_queries=["缺口查詢A"])
    second = _analyst_out("DRAFT_READY", draft="x" * 150)
    fake_analyst = MagicMock()
    fake_analyst.research = AsyncMock(side_effect=[first, second])
    fake_critic = MagicMock()
    fake_critic.review = AsyncMock(return_value=MagicMock(status="PASS"))
    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: fake_analyst)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: fake_critic)

    cm = MagicMock(); cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "原始 formatted results")

    engine._execute_search.assert_awaited_once()  # 補搜跑了一次
    assert fake_analyst.research.await_count == 2  # Analyst 重跑一次


@pytest.mark.asyncio
async def test_search_required_no_results_emits_narration(monkeypatch):
    """補搜無結果 → 降級旁白（lr_copy 常數）+ 不重跑 Analyst。"""
    from reasoning.live_research import lr_copy
    engine = _make_engine()
    engine._execute_search = AsyncMock(return_value=("（未找到相關結果）", {}))
    emitted = []
    engine._emit_narration = AsyncMock(side_effect=lambda m: emitted.append(m))
    first = _analyst_out("SEARCH_REQUIRED", draft="", new_queries=["缺口查詢A"])
    fake_analyst = MagicMock()
    fake_analyst.research = AsyncMock(side_effect=[first])
    fake_critic = MagicMock()
    fake_critic.review = AsyncMock(return_value=MagicMock(status="PASS"))
    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: fake_analyst)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: fake_critic)
    cm = MagicMock(); cm.research_question = "Q"
    await engine._run_mini_reasoning(cm, "原始 results")
    assert fake_analyst.research.await_count == 1  # 無補搜結果 → 不重跑
    assert lr_copy.SEARCH_REQUIRED_DEGRADED_NARRATION in emitted


@pytest.mark.asyncio
async def test_secondary_search_eid_reaches_evidence_pool(monkeypatch):
    """可引用性橋接：補搜（走真 _execute_search）寫入的新 eid 進 engine.evidence_pool。

    用真 _execute_search（不 mock）+ mock retriever，驗 _execute_search 的 side-effect
    確實把補到的新來源寫進 self.evidence_pool（→ 後續 serialize → outline planner 可分配）。
    """
    engine = _make_engine()

    # mock retriever_search：回一筆新 result，讓 _execute_search 寫 pool
    async def _fake_retriever(query, **kwargs):
        return [
            {
                "url": "https://news.example/secondary-A",
                "title": "補搜新聞A",
                "snippet": "補搜到的相關內容片段。",
                "source_domain": "news.example",
            }
        ]

    # _execute_search 內部呼叫 retriever — patch 它的來源。先親驗 _execute_search 用的
    # retriever entrypoint，這裡 patch engine._retriever_search 若存在，否則 patch 模組層。
    import reasoning.live_research.loop_engine as le_mod
    monkeypatch.setattr(le_mod, "retriever_search", _fake_retriever, raising=False)

    first = _analyst_out("SEARCH_REQUIRED", draft="", new_queries=["補搜查詢"])
    second = _analyst_out("DRAFT_READY", draft="y" * 150)
    fake_analyst = MagicMock()
    fake_analyst.research = AsyncMock(side_effect=[first, second])
    fake_critic = MagicMock()
    fake_critic.review = AsyncMock(return_value=MagicMock(status="PASS"))
    monkeypatch.setattr("reasoning.agents.analyst.AnalystAgent", lambda h, timeout=None: fake_analyst)
    monkeypatch.setattr("reasoning.agents.critic.CriticAgent", lambda h, timeout=None: fake_critic)

    cm = MagicMock(); cm.research_question = "Q"
    pool_before = set(engine.evidence_pool.keys())
    await engine._run_mini_reasoning(cm, "原始 results")
    pool_after = set(engine.evidence_pool.keys())

    # 補搜新增了至少一個 eid 進 pool（→ 持久化進 state.evidence_pool_json → 可引用）
    new_eids = pool_after - pool_before
    assert new_eids, "補搜未寫入任何新 eid 進 evidence_pool（可引用性橋接斷裂）"
    # 新 eid 對應的 entry url 是補搜來源
    new_entry = engine.evidence_pool[next(iter(new_eids))]
    assert "secondary-A" in (getattr(new_entry, "url", "") or "")
