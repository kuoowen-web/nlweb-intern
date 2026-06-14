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
