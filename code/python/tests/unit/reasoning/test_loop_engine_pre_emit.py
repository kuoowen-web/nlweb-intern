"""#8 deploy-env-hardening: BAB 長 LLM call 前必須先 emit 進度 narration,
避免 prod 89s 黑屏 + 助長 SSE idle 斷。本地驗呼叫順序;真實黑屏為 prod-only。
"""
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_derive_search_plan_has_pre_call_narration(monkeypatch):
    """derive_search_plan(LLM call) 之前必須先有一次 _emit_narration。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    engine = BABLoopEngine.__new__(BABLoopEngine)  # 不跑 __init__,直接注入需要的屬性

    call_order = []

    async def fake_emit(text):
        call_order.append(("emit", text))

    async def fake_derive(**kwargs):
        call_order.append(("derive_llm_call", None))
        out = MagicMock()
        out.narration = "derive 完成"
        out.search_seeds = []
        return out

    engine._emit_narration = fake_emit
    associator = MagicMock()
    associator.derive_search_plan = fake_derive
    engine.associator = associator

    # 🟡 外部 review GPT #2:**禁止**在 test 裡手動 replay
    #   `await engine._emit_narration(...); await associator.derive_search_plan(...)`
    #   —— 那是測試測自己(emit 順序是 test 寫死的,不是 production code),
    #   未來有人把 derive 搬位置 / 刪掉 pre-emit,test 仍 PASS,prod 黑屏照回來。
    # 必須驅動 *真實 production code path*。採 GPT 選項 A:把「pre-emit + derive」
    #   抽成可單測 helper `_run_derive_phase()`(見 Step 3 改法),test 呼叫真 helper:
    await engine._run_derive_phase(context_map=None, executed_searches=[])

    emit_idx = next(i for i, c in enumerate(call_order) if c[0] == "emit")
    derive_idx = next(i for i, c in enumerate(call_order) if c[0] == "derive_llm_call")
    assert emit_idx < derive_idx, "derive LLM call 前必須先 emit narration(由真實 helper 保證)"


def _phase3_engine_fixture(search_result, mini_ok):
    """s5-4：共用 fixture — 回傳 (engine, call_order)，驅動真實 run_loop（GPT #2 反 replay）。

    search_result: _execute_search 回傳的 formatted_results（控制 early-skip gate）。
    mini_ok: fake _run_mini_reasoning 的回傳值（True=成功輪 / False=O5-B 降級輪）。
    """
    from unittest.mock import AsyncMock, MagicMock
    from reasoning.live_research.loop_engine import BABLoopEngine
    from reasoning.schemas_live import ContextMap

    call_order = []

    # 真實 handler：message_sender.send_message 記錄每個 SSE event
    handler = MagicMock()

    async def record_send(payload):
        call_order.append(("send", payload.get("message_type"), payload.get("phase"), payload.get("status")))

    handler.message_sender = MagicMock()
    handler.message_sender.send_message = record_send
    handler.connection_alive_event = MagicMock()
    handler.connection_alive_event.is_set = MagicMock(return_value=True)
    handler.http_handler = MagicMock()
    handler.http_handler.connection_alive = True
    handler._soft_interrupt_event = None
    handler.query_params = {}
    handler.site = 'all'

    # associator：build / derive / refine 都用 AsyncMock，refine is_stable=True 跑 1 輪即停
    cm = ContextMap(research_question="q", version=0, topics=[], relations=[])

    build_out = MagicMock()
    build_out.context_map = cm
    build_out.narration = "build done"

    derive_out = MagicMock()
    derive_out.narration = "derive done"
    derive_out.search_seeds = []

    refine_out = MagicMock()
    refine_out.updated_context_map = cm
    refine_out.narration = "refine done"
    refine_out.is_stable = True

    associator = MagicMock()
    associator.build_context_map = AsyncMock(return_value=build_out)
    associator.derive_search_plan = AsyncMock(return_value=derive_out)
    associator.refine_context_map = AsyncMock(return_value=refine_out)

    engine = BABLoopEngine(
        associator=associator,
        handler=handler,
        max_iterations=1,
        enable_consistency_monitor=False,  # 隔離：不跑 consistency LLM
    )

    async def fake_search(seeds):
        return (search_result, {1: {"url": "https://example.com/1"}})
    engine._execute_search = fake_search

    # 記錄 mini-reasoning 何時被呼叫（用於斷言 emit 包夾）+ 回傳成敗狀態
    async def fake_mini(context_map, formatted_results):
        call_order.append(("mini_reasoning_call", None, None, None))
        return mini_ok
    engine._run_mini_reasoning = fake_mini

    return engine, call_order


def _phase_evt(c, phase, status):
    return c[0] == "send" and c[1] == "research_phase" and c[2] == phase and c[3] == status


def _idx_of(call_order, pred):
    return next(i for i, c in enumerate(call_order) if pred(c))


@pytest.mark.asyncio
async def test_mini_reasoning_emits_bab_phase3_around_call():
    """成功輪：mini 呼叫前 emit bab_phase3 started + narration、後 emit completed；
    整段在 bab_phase2 completed 之後、bab_phase4 started 之前（Codex SF2 順序加固）。
    """
    engine, call_order = _phase3_engine_fixture(
        search_result="[1] doc\nbody\nURL: https://example.com/1\n", mini_ok=True,
    )
    await engine.run_loop(query="台灣綠能")

    mini_idx = _idx_of(call_order, lambda c: c[0] == "mini_reasoning_call")
    p2_completed_idx = _idx_of(call_order, lambda c: _phase_evt(c, "bab_phase2", "completed"))
    p3_started_idx = _idx_of(call_order, lambda c: _phase_evt(c, "bab_phase3", "started"))
    p3_completed_idx = _idx_of(call_order, lambda c: _phase_evt(c, "bab_phase3", "completed"))
    p4_started_idx = _idx_of(call_order, lambda c: _phase_evt(c, "bab_phase4", "started"))
    # mini-reasoning 前必有一句 narration（緊鄰 started 之後、mini call 之前）
    narration_before_mini = any(
        c[0] == "send" and c[1] == "live_research_narration"
        for c in call_order[p3_started_idx:mini_idx]
    )

    assert p2_completed_idx < p3_started_idx, "bab_phase3 started 必須在 bab_phase2 completed 之後"
    assert p3_started_idx < mini_idx, "bab_phase3 started 必須在 mini-reasoning 呼叫之前"
    assert narration_before_mini, "mini-reasoning 呼叫前必須先 emit 一句 narration（預期管理）"
    assert mini_idx < p3_completed_idx, "bab_phase3 completed 必須在 mini-reasoning 呼叫之後"
    assert p3_completed_idx < p4_started_idx, "bab_phase3 completed 必須在 bab_phase4 started 之前"


@pytest.mark.asyncio
async def test_mini_reasoning_failure_round_skips_completed_emit():
    """O5-B 降級輪（mini 回傳 False）：不 emit bab_phase3 completed —
    降級旁白「已先略過」與「完成」並列自相矛盾（s5-4 耦合判定 1）。
    started 照 emit、phase4 照常。
    """
    engine, call_order = _phase3_engine_fixture(
        search_result="[1] doc\nbody\nURL: https://example.com/1\n", mini_ok=False,
    )
    await engine.run_loop(query="台灣綠能")

    assert any(_phase_evt(c, "bab_phase3", "started") for c in call_order), \
        "失敗輪 started 照 emit（失敗發生在 mini 內部，started 時尚未知）"
    assert not any(_phase_evt(c, "bab_phase3", "completed") for c in call_order), \
        "失敗輪不可 emit bab_phase3 completed（與降級旁白『已先略過』矛盾）"
    assert any(_phase_evt(c, "bab_phase4", "started") for c in call_order), "phase4 不受影響"


@pytest.mark.asyncio
async def test_mini_reasoning_early_skip_round_emits_no_phase3():
    """early-skip 輪（檢索空手，sentinel「（未找到相關結果）」）：完全不 emit bab_phase3
    事件與預呼叫 narration — 沒有資料可分析，不對 user 謊稱在分析（s5-4 耦合判定 3）。
    _run_mini_reasoning 仍被呼叫（行為等價：內部 early return + 既有 log）。
    """
    engine, call_order = _phase3_engine_fixture(
        search_result="（未找到相關結果）", mini_ok=False,
    )
    await engine.run_loop(query="台灣綠能")

    assert not any(
        c[0] == "send" and c[1] == "research_phase" and c[2] == "bab_phase3"
        for c in call_order
    ), "early-skip 輪不可 emit 任何 bab_phase3 事件"
    assert any(c[0] == "mini_reasoning_call" for c in call_order), \
        "_run_mini_reasoning 仍須被呼叫（內部 early return，行為等價）"
    assert any(_phase_evt(c, "bab_phase4", "started") for c in call_order), "phase4 不受影響"
