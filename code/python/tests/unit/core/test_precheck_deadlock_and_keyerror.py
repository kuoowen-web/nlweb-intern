"""CORE-4 + CORE-5 (full-scan 批7) 回歸測試。

CORE-5：Decon do() 拋錯 / LLM 回缺 key 時，precheck_step_done("Decon") 必須仍被
        呼叫（→ _decon_event set），否則 wait_for_decontextualization() 永久死鎖。
CORE-4：detector do() 對 LLM 回非空但缺 key 的 dict 不裸取 KeyError，而是 fail-open
        + log warning。

測試切面：mock `run_prompt`（LLM 呼叫邊界，貴的資料蒐集點），驗 do() 的狀態機/防護
邏輯——不打真 LLM。用真的 NLWebHandlerState 驗死鎖不變式（非 mock 掉 state）。
"""

import asyncio
import types

import pytest

from core.state import NLWebHandlerState


def _make_handler():
    """最小 handler stub，帶真的 NLWebHandlerState。"""
    handler = types.SimpleNamespace()
    handler.query = "原始問題"
    handler.decontextualized_query = None
    handler.requires_decontextualization = None
    handler.context_url = ""
    handler.context_description = ""
    handler.query_params = {}
    handler.site = "example.com"
    handler.query_is_irrelevant = None
    handler.query_done = False
    handler.pre_checks_done_event = asyncio.Event()
    handler.state = NLWebHandlerState(handler)
    return handler


# ── 讓 CONFIG.is_decontextualize_enabled() 為真，才會走到 run_prompt 路徑 ──
@pytest.fixture(autouse=True)
def _enable_decon(monkeypatch):
    from core.config import CONFIG
    monkeypatch.setattr(CONFIG, "is_decontextualize_enabled", lambda: True, raising=False)
    monkeypatch.setattr(CONFIG, "should_raise_exceptions", lambda: False, raising=False)
    yield


# ===========================================================================
# CORE-5：Decon do() 例外 / 缺 key 不死鎖
# ===========================================================================

@pytest.mark.asyncio
async def test_decon_missing_key_sets_decon_event_no_deadlock():
    """LLM 回 requires_decontextualization=True 但缺 decontextualized_query →
    不 KeyError、fail-open 保留原 query、_decon_event 仍被 set（不死鎖）。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def fake_run_prompt(*a, **k):
        # 缺 decontextualized_query 這個 key（CORE-4 裸取原會 KeyError）
        return {"requires_decontextualization": "True"}

    det.run_prompt = fake_run_prompt

    # 若死鎖，wait_for_decontextualization() 永不返回 → wait_for timeout 會 raise
    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.state._decon_event.is_set(), "缺 key 後 _decon_event 必須被 set，否則死鎖"
    # fail-open：保留原 query，不因缺 key 而崩
    assert handler.decontextualized_query == "原始問題"

    ok = await asyncio.wait_for(handler.state.wait_for_decontextualization(), timeout=2.0)
    assert ok is True  # Decon step 已標 DONE


@pytest.mark.asyncio
async def test_decon_run_prompt_raises_still_sets_event():
    """run_prompt 拋非預期例外 → finally 仍保證 precheck_step_done("Decon") → 不死鎖。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def boom(*a, **k):
        raise RuntimeError("LLM provider exploded")

    det.run_prompt = boom

    # 例外會往外傳（production gather 會 return_exceptions 吞），但 finally 必先 set event
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.state._decon_event.is_set(), "do() 拋錯前 finally 必須 set _decon_event"


@pytest.mark.asyncio
async def test_decon_normal_path_not_double_marked():
    """正常路徑：finally 的 is_precheck_step_done 守衛避免重複呼叫 precheck_step_done。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    call_count = {"n": 0}
    orig = handler.state.precheck_step_done

    async def counting(step_name):
        call_count["n"] += 1
        await orig(step_name)

    handler.state.precheck_step_done = counting

    async def fake_run_prompt(*a, **k):
        return {"requires_decontextualization": "True", "decontextualized_query": "改寫後問題"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.decontextualized_query == "改寫後問題"
    assert handler.state._decon_event.is_set()
    assert call_count["n"] == 1, "正常路徑 precheck_step_done 只該呼叫一次（finally 不重複）"


# ===========================================================================
# CORE-5 belt：prepare 尾端 set_pre_checks_done() 也 set _decon_event
# ===========================================================================

def test_set_pre_checks_done_also_sets_decon_event():
    """set_pre_checks_done()（prepare finally 呼叫）除 pre_checks_done_event 外，
    也 set _decon_event——即使 Decon do() 完全沒跑到，waiter 也不永久阻塞。"""
    handler = _make_handler()
    state = handler.state
    assert not state._decon_event.is_set()
    state.set_pre_checks_done()
    assert handler.pre_checks_done_event.is_set()
    assert state._decon_event.is_set()


# ===========================================================================
# CORE-4：relevance / memory 缺 key fail-open 不炸、不死鎖
# ===========================================================================

@pytest.mark.asyncio
async def test_relevance_missing_key_fail_open(monkeypatch):
    """RelevanceDetection 回缺 site_is_irrelevant_to_query → fail-open（不擋 query）+
    precheck_step_done("Relevance") 仍被呼叫。"""
    from core.query_analysis import relevance_detection as rd

    handler = _make_handler()
    det = rd.RelevanceDetection(handler)

    async def fake_run_prompt(*a, **k):
        return {"unexpected": "shape"}  # 缺 key

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.query_is_irrelevant is False  # fail-open：不判定不相關
    assert handler.state.is_precheck_step_done("Relevance")


@pytest.mark.asyncio
async def test_memory_missing_key_fail_open(monkeypatch):
    """Memory 回缺 is_memory_request → fail-open + precheck_step_done("Memory") 仍呼叫。"""
    from core.config import CONFIG
    monkeypatch.setattr(CONFIG, "is_memory_enabled", lambda: True, raising=False)
    from core.query_analysis import memory as mem

    handler = _make_handler()
    det = mem.Memory(handler)

    async def fake_run_prompt(*a, **k):
        return {"nope": "no key here"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.state.is_precheck_step_done("Memory")


@pytest.mark.asyncio
async def test_relevance_run_prompt_raises_still_marks_done():
    """RelevanceDetection.run_prompt 拋錯 → finally 仍標 precheck_step_done。"""
    from core.query_analysis import relevance_detection as rd

    handler = _make_handler()
    det = rd.RelevanceDetection(handler)

    async def boom(*a, **k):
        raise RuntimeError("boom")

    det.run_prompt = boom

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.state.is_precheck_step_done("Relevance")
