"""fix/llm-bool-compare 點位 6：PromptGuardrails fallback 路徑 verdict 大小寫穩健化。

背景：_run_llm_detection() 的 PromptRunner fallback 路徑（instructor 不可用時的
JSON-parse 備援）用 `verdict_str == 'malicious'` 精確比對。LLM 回 'Malicious' 或
'MALICIOUS' 時比對恆 False → 判為 safe，惡意查詢漏擋（規則要求 fail-open 只用於
「LLM 完全失敗」情境，不該因為大小寫落入同樣的 fail-open 缺口）。

範圍：只動 fallback 區塊（run_prompt JSON-parse，:322 一帶）。instructor/Literal
型別安全的主路徑（:298，result.verdict 為 pydantic Literal 型別、非自由格式字串）
按任務規則不碰——本檔不測那段。

測試切面：直接呼叫 _run_llm_detection()，mock self.run_prompt 讓 instructor 路徑
不可用（monkeypatch reasoning.agents.base._instructor_available = False），逼進
fallback 分支。
"""

import types

import pytest

from core.query_analysis.prompt_guardrails import PromptGuardrails, InjectionVerdict


def _make_handler():
    handler = types.SimpleNamespace()
    handler.query = "測試查詢"
    return handler


@pytest.fixture(autouse=True)
def _force_fallback_path(monkeypatch):
    """讓 instructor 路徑不可用，強制走 PromptRunner JSON-parse fallback。"""
    import reasoning.agents.base as agents_base
    monkeypatch.setattr(agents_base, "_instructor_available", False, raising=False)
    yield


def _make_guardrails():
    handler = _make_handler()
    # 繞過 __init__ 的 state.start_precheck_step（不需要完整 handler.state）
    g = object.__new__(PromptGuardrails)
    g.handler = handler
    return g


@pytest.mark.asyncio
async def test_fallback_mixed_case_malicious_still_blocks():
    """點位 6：fallback 路徑回 'Malicious'（混合大小寫）應仍判為 MALICIOUS。
    修復前：'Malicious' == 'malicious' 為 False → 誤判 safe，漏擋惡意查詢。"""
    g = _make_guardrails()

    async def fake_run_prompt(*a, **k):
        return {"verdict": "Malicious", "reason": "偵測到覆寫系統指示嘗試"}

    g.run_prompt = fake_run_prompt

    verdict, reason = await g._run_llm_detection("測試查詢")
    assert verdict == InjectionVerdict.MALICIOUS
    assert reason == "偵測到覆寫系統指示嘗試"


@pytest.mark.asyncio
async def test_fallback_uppercase_malicious_still_blocks():
    """點位 6：fallback 路徑回 'MALICIOUS'（全大寫）同判定為 MALICIOUS。"""
    g = _make_guardrails()

    async def fake_run_prompt(*a, **k):
        return {"verdict": "MALICIOUS", "reason": "偵測到 jailbreak"}

    g.run_prompt = fake_run_prompt

    verdict, reason = await g._run_llm_detection("測試查詢")
    assert verdict == InjectionVerdict.MALICIOUS


@pytest.mark.asyncio
async def test_fallback_lowercase_malicious_unaffected_by_fix():
    """既有行為維持：原本就小寫的 'malicious' 修復後仍判為 MALICIOUS（回歸鎖）。"""
    g = _make_guardrails()

    async def fake_run_prompt(*a, **k):
        return {"verdict": "malicious", "reason": "原本就小寫"}

    g.run_prompt = fake_run_prompt

    verdict, reason = await g._run_llm_detection("測試查詢")
    assert verdict == InjectionVerdict.MALICIOUS


@pytest.mark.asyncio
async def test_fallback_safe_verdict_unaffected():
    """既有 fail 方向維持：'safe' 判定不受影響。"""
    g = _make_guardrails()

    async def fake_run_prompt(*a, **k):
        return {"verdict": "safe", "reason": "正常查詢"}

    g.run_prompt = fake_run_prompt

    verdict, reason = await g._run_llm_detection("測試查詢")
    assert verdict == InjectionVerdict.SAFE


@pytest.mark.asyncio
async def test_fallback_suspicious_verdict_unaffected():
    """既有行為維持：'suspicious' 判定不受本修法影響（本修法只動 'malicious' 比對）。"""
    g = _make_guardrails()

    async def fake_run_prompt(*a, **k):
        return {"verdict": "suspicious", "reason": "可疑但非確定"}

    g.run_prompt = fake_run_prompt

    verdict, reason = await g._run_llm_detection("測試查詢")
    assert verdict == InjectionVerdict.SUSPICIOUS


@pytest.mark.asyncio
async def test_fallback_run_prompt_none_response_fail_open():
    """既有 fail-open 行為維持：run_prompt 回 None/失敗時仍 fail-open 為 SAFE。"""
    g = _make_guardrails()

    async def fake_run_prompt(*a, **k):
        return None

    g.run_prompt = fake_run_prompt

    verdict, reason = await g._run_llm_detection("測試查詢")
    assert verdict == InjectionVerdict.SAFE
