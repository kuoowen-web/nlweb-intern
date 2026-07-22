"""BAB loop _check_connection cooperative-stop 語義（plan: lr-disconnect-midstage-persist）。

client offline（純斷線）→ 回 "offline"（不 raise，讓 run_loop 有序收場、保住已累積 evidence）。
soft-interrupt（使用者主動打斷研究）→ 仍 raise ResearchCancelledError（使用者要立刻停）。
在線 → 回 None（照常跑）。

R1 AR 追加（三家獨立 blocker 消化，D-7）：offline cooperative break 必須設
engine.stopped_early = True，讓 caller（_run_stage_2）分辨「正常收斂 return」
vs「topic 執行到一半被打斷的 return」——兩者對 caller 而言函式簽名上都是
「正常 return，沒 raise」，唯一分辨依據就是這個訊號。
"""
from unittest.mock import MagicMock

import pytest

from reasoning.live_research.loop_engine import BABLoopEngine
from reasoning.orchestrator_base import ResearchCancelledError


def _make_engine(*, alive=True, soft=False):
    handler = MagicMock()
    # http_handler.connection_alive: wrapper flag（Signal 1）
    handler.http_handler = MagicMock()
    handler.http_handler.connection_alive = alive
    # connection_alive_event（Signal 2）
    evt = MagicMock()
    evt.is_set.return_value = alive
    handler.connection_alive_event = evt
    # _soft_interrupt_event（Signal 3）
    soft_evt = MagicMock()
    soft_evt.is_set.return_value = soft
    handler._soft_interrupt_event = soft_evt if soft else None
    engine = BABLoopEngine(associator=MagicMock(), handler=handler, max_iterations=2)
    return engine


def test_check_connection_online_returns_none():
    engine = _make_engine(alive=True)
    assert engine._check_connection() is None


def test_check_connection_offline_returns_offline_no_raise():
    """純斷線：回 'offline'，不 raise（不殺刻意背景跑的 task）。"""
    engine = _make_engine(alive=False)
    result = engine._check_connection()
    assert result == "offline"  # NOT raised


def test_check_connection_soft_interrupt_still_raises():
    """使用者主動打斷：仍 raise（要立刻停）。"""
    engine = _make_engine(alive=True, soft=True)
    with pytest.raises(ResearchCancelledError):
        engine._check_connection()


def test_check_connection_offline_and_soft_interrupt_both_set_raises():
    """R1 AR SHOULD-FIX 5：alive=False（斷線）+ soft=True（使用者主動打斷）同時成立。

    兩個訊號同時觸發時，soft-interrupt 優先（使用者主動要停，語義上比純斷線更
    急迫、更明確）——_check_connection 內 Signal 3（soft）判斷放在 Signal 1/2
    （offline）之前，此 test 鎖住這個優先序不被日後改動打亂。
    """
    engine = _make_engine(alive=False, soft=True)
    with pytest.raises(ResearchCancelledError):
        engine._check_connection()


def test_engine_stopped_early_defaults_false():
    """R1 AR D-7：stopped_early 預設 False（未曾 offline break 的正常狀態）。"""
    engine = _make_engine(alive=True)
    assert engine.stopped_early is False


def _make_context_map():
    """最小合法 ContextMap（topics 非空），供 run_loop() 呼叫使用。"""
    from reasoning.schemas_live import ContextMap, ContextMapTopic

    return ContextMap(
        research_question="q",
        working_hypothesis="h",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="T1", domain="d", relevance="core"
            ),
        ],
        version=1,
    )


@pytest.mark.asyncio
async def test_evidence_pool_readable_after_offline_break():
    """cooperative break 後，engine.evidence_pool 保有已累積 evidence（caller 撈得到）。

    R2 AR SHOULD-FIX 2（in-house + Codex 獨立同抓）追加：這是唯一一個對**真實**
    BABLoopEngine.run_loop() 在 offline 情境下跑完後做斷言的測試——本檔其餘
    stopped_early 相關 test（見 Task 1 Step 1）都是直接呼叫 engine._check_connection()
    或檢查 __init__ 預設值，沒有一個測「run_loop() 執行到 offline cooperative break
    之後，engine.stopped_early 這個訊號真的被正確**設定**為 True」——Task 3 的
    test_stage2_midtopic_offline_break_does_not_mark_completed 用 FakeEngineInterrupted
    在 __init__ 手動設 stopped_early，只驗證 _run_stage_2 正確**消費**這個訊號，
    不證明真實 run_loop 會正確**產生**這個訊號。本測試複用既有的「真實 engine +
    真實 run_loop() 呼叫」設置，補這一道端到端鎖，銜接訊號產生（Task 1）與訊號
    消費（Task 3）之間原本沒有測試覆蓋的縫隙。

    用 @pytest.mark.asyncio（而非 asyncio.get_event_loop().run_until_complete）
    驅動——與本檔其餘寫法一致，避免在整個 test suite 一起跑時因為其他測試已
    關閉/替換 event loop 導致 'no current event loop in thread' 的 test-isolation
    脆弱性（單檔跑不會暴露，suite 全跑才會因執行順序而間歇性出現）。
    """
    engine = _make_engine(alive=False)
    # 模擬迴圈前已累積 2 筆 evidence（seed）
    from reasoning.schemas_live import EvidencePoolEntry
    engine.evidence_pool = {
        1: EvidencePoolEntry(evidence_id=1, url="https://a", title="A", snippet="s1", source="web"),
        2: EvidencePoolEntry(evidence_id=2, url="https://b", title="B", snippet="s2", source="web"),
    }
    # run_loop 一進迴圈 _check_connection 就回 "offline" → break → return。
    # evidence_pool 不因 break 被清空。
    result = await engine.run_loop(query="q", existing_context_map=_make_context_map())
    assert len(engine.evidence_pool) == 2  # 保住
    # R2 SHOULD-FIX 2：真實 run_loop() 在 offline 情境下跑完後，engine.stopped_early
    # 必須是 True（訊號真的被 run_loop 設定，不是只有 FakeEngine 手動模擬）。
    assert engine.stopped_early is True
