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
from unittest.mock import AsyncMock

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
    handler.send_message = AsyncMock()
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


# ===========================================================================
# fix/llm-bool-compare：LLM 布林回應大小寫/型別穩健化（== "True" 家族 6 點位）
#
# 背景：這些點位直接拿 LLM 回應值與 "True" 做精確字串比對。LLM 回傳 "true"、
# "TRUE" 或 JSON 布林 True 時比對恆 False → 功能靜默 fail-open。修法對齊既有
# str(x).lower() == "true" 寫法（core/query_analysis/query_understanding.py:314）。
#
# 這裡涵蓋點位 1-3（decontextualize / relevance_detection / memory）。點位 4
# （required_info）與 5-6（prompt_guardrails fallback）另檔測試。
# ===========================================================================

@pytest.mark.asyncio
async def test_decon_lowercase_true_string_still_triggers():
    """點位 1：LLM 回 'true'（小寫）目前應仍視為需要 decontextualization。
    修復前：'true' == 'True' 為 False → 誤判不需要 decon（fail-open 到錯誤方向：
    漏做該做的 decon，而非「保守/安全」的 fail-open）。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def fake_run_prompt(*a, **k):
        return {"requires_decontextualization": "true", "decontextualized_query": "改寫後問題"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.requires_decontextualization is True
    assert handler.decontextualized_query == "改寫後問題"


@pytest.mark.asyncio
async def test_decon_uppercase_true_string_still_triggers():
    """點位 1：LLM 回 'TRUE'（全大寫）同判定為需要 decon。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def fake_run_prompt(*a, **k):
        return {"requires_decontextualization": "TRUE", "decontextualized_query": "改寫後問題"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.requires_decontextualization is True
    assert handler.decontextualized_query == "改寫後問題"


@pytest.mark.asyncio
async def test_decon_boolean_true_still_triggers():
    """點位 1：LLM 回 JSON 布林 True（非字串）同判定為需要 decon。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def fake_run_prompt(*a, **k):
        return {"requires_decontextualization": True, "decontextualized_query": "改寫後問題"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.requires_decontextualization is True
    assert handler.decontextualized_query == "改寫後問題"


@pytest.mark.asyncio
async def test_decon_false_string_does_not_trigger():
    """點位 1 既有 fail 方向維持：'False' 不觸發 decon。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def fake_run_prompt(*a, **k):
        return {"requires_decontextualization": "False"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.requires_decontextualization is False
    assert handler.decontextualized_query == "原始問題"  # 保留原 query


@pytest.mark.asyncio
async def test_decon_boolean_false_does_not_trigger():
    """點位 1：JSON 布林 False 同樣不觸發。"""
    from core.query_analysis.decontextualize import PrevQueryDecontextualizer

    handler = _make_handler()
    det = PrevQueryDecontextualizer(handler)

    async def fake_run_prompt(*a, **k):
        return {"requires_decontextualization": False}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.requires_decontextualization is False


@pytest.mark.asyncio
async def test_relevance_lowercase_true_string_still_enforces(monkeypatch):
    """點位 2：LLM 回 'true'（小寫）在 enforce 模式下仍應判為不相關並攔截。
    修復前：'true' == 'True' 為 False → 永遠判為相關，enforce 模式形同虛設。"""
    from core.query_analysis import relevance_detection as rd

    monkeypatch.setattr(rd, "RELEVANCE_DETECTION_MODE", "enforce")

    handler = _make_handler()
    det = rd.RelevanceDetection(handler)

    async def fake_run_prompt(*a, **k):
        return {"site_is_irrelevant_to_query": "true", "explanation_for_irrelevance": "不相關"}

    det.run_prompt = fake_run_prompt
    handler.send_message = AsyncMock()

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.query_is_irrelevant is True
    assert handler.query_done is True


@pytest.mark.asyncio
async def test_relevance_boolean_true_still_enforces(monkeypatch):
    """點位 2：LLM 回 JSON 布林 True 在 enforce 模式下同樣攔截。"""
    from core.query_analysis import relevance_detection as rd

    monkeypatch.setattr(rd, "RELEVANCE_DETECTION_MODE", "enforce")

    handler = _make_handler()
    det = rd.RelevanceDetection(handler)

    async def fake_run_prompt(*a, **k):
        return {"site_is_irrelevant_to_query": True, "explanation_for_irrelevance": "不相關"}

    det.run_prompt = fake_run_prompt
    handler.send_message = AsyncMock()

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.query_is_irrelevant is True
    assert handler.query_done is True


@pytest.mark.asyncio
async def test_relevance_false_string_stays_relevant(monkeypatch):
    """點位 2 既有 fail 方向維持：'False' 判為相關，不攔截。"""
    from core.query_analysis import relevance_detection as rd

    monkeypatch.setattr(rd, "RELEVANCE_DETECTION_MODE", "enforce")

    handler = _make_handler()
    det = rd.RelevanceDetection(handler)

    async def fake_run_prompt(*a, **k):
        return {"site_is_irrelevant_to_query": "False"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.query_is_irrelevant is False
    assert handler.query_done is False


@pytest.mark.asyncio
async def test_memory_lowercase_true_string_still_writes(monkeypatch):
    """點位 3：LLM 回 'true'（小寫）應仍觸發「記住」訊息。
    修復前：'true' == 'True' 為 False → remember 訊息永遠不送出。"""
    from core.config import CONFIG
    monkeypatch.setattr(CONFIG, "is_memory_enabled", lambda: True, raising=False)
    from core.query_analysis import memory as mem

    handler = _make_handler()
    det = mem.Memory(handler)

    async def fake_run_prompt(*a, **k):
        return {"is_memory_request": "true", "memory_request": "記住我喜歡貓"}

    det.run_prompt = fake_run_prompt
    handler.send_message = AsyncMock()

    await asyncio.wait_for(det.do(), timeout=2.0)
    handler.send_message.assert_awaited_once()
    payload = handler.send_message.await_args.args[0]
    assert payload["message_type"] == "remember"
    assert payload["item_to_remember"] == "記住我喜歡貓"


@pytest.mark.asyncio
async def test_memory_boolean_true_still_writes(monkeypatch):
    """點位 3：LLM 回 JSON 布林 True 同樣觸發「記住」訊息。"""
    from core.config import CONFIG
    monkeypatch.setattr(CONFIG, "is_memory_enabled", lambda: True, raising=False)
    from core.query_analysis import memory as mem

    handler = _make_handler()
    det = mem.Memory(handler)

    async def fake_run_prompt(*a, **k):
        return {"is_memory_request": True, "memory_request": "記住我喜歡貓"}

    det.run_prompt = fake_run_prompt
    handler.send_message = AsyncMock()

    await asyncio.wait_for(det.do(), timeout=2.0)
    handler.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_false_string_does_not_write(monkeypatch):
    """點位 3 既有 fail 方向維持：'False' 不觸發記住。"""
    from core.config import CONFIG
    monkeypatch.setattr(CONFIG, "is_memory_enabled", lambda: True, raising=False)
    from core.query_analysis import memory as mem

    handler = _make_handler()
    det = mem.Memory(handler)

    async def fake_run_prompt(*a, **k):
        return {"is_memory_request": "False", "memory_request": ""}

    det.run_prompt = fake_run_prompt
    handler.send_message = AsyncMock()

    await asyncio.wait_for(det.do(), timeout=2.0)
    handler.send_message.assert_not_awaited()
