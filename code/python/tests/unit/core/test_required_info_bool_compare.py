"""fix/llm-bool-compare 點位 4：RequiredInfo 對 LLM 回應布林值大小寫/型別穩健化。

背景：required_info.py 原本用 `_required_info_raw == "True"` 精確字串比對。LLM 回
'true'（小寫）、'TRUE' 或 JSON 布林 True 時比對恆 False → required_info_found 誤判
False → 誤觸發「反覆追問使用者」（query_done=True + 不繼續查詢），即使 LLM 其實已
回報「資訊足夠」。修法對齊 str(x).lower() == "true"。

測試切面：mock run_prompt（LLM 呼叫邊界），驗 do() 對 required_info_found 的判定。
"""

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from core.state import NLWebHandlerState


def _make_handler():
    handler = types.SimpleNamespace()
    handler.query = "原始問題"
    handler.query_params = {}
    handler.site = "example.com"
    handler.required_info_found = None
    handler.user_question = None
    handler.query_done = False
    handler.pre_checks_done_event = asyncio.Event()
    handler.state = NLWebHandlerState(handler)
    handler.send_message = AsyncMock()
    return handler


@pytest.fixture(autouse=True)
def _enable_required_info(monkeypatch):
    from core.config import CONFIG
    monkeypatch.setattr(CONFIG, "is_required_info_enabled", lambda: True, raising=False)
    yield


@pytest.mark.asyncio
async def test_required_info_lowercase_true_string_found():
    """點位 4：LLM 回 'true'（小寫）應判定為「已有必要資訊」。
    修復前：'true' == 'True' 為 False → 誤判缺資訊，query_done=True 誤觸發反覆追問。"""
    from core.query_analysis.required_info import RequiredInfo

    handler = _make_handler()
    det = RequiredInfo(handler)

    async def fake_run_prompt(*a, **k):
        return {"required_info_found": "true"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.required_info_found is True
    assert handler.query_done is False


@pytest.mark.asyncio
async def test_required_info_uppercase_true_string_found():
    """點位 4：LLM 回 'TRUE'（全大寫）同判定為已有必要資訊。"""
    from core.query_analysis.required_info import RequiredInfo

    handler = _make_handler()
    det = RequiredInfo(handler)

    async def fake_run_prompt(*a, **k):
        return {"required_info_found": "TRUE"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.required_info_found is True
    assert handler.query_done is False


@pytest.mark.asyncio
async def test_required_info_boolean_true_found():
    """點位 4：LLM 回 JSON 布林 True（非字串）同判定為已有必要資訊。"""
    from core.query_analysis.required_info import RequiredInfo

    handler = _make_handler()
    det = RequiredInfo(handler)

    async def fake_run_prompt(*a, **k):
        return {"required_info_found": True}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.required_info_found is True
    assert handler.query_done is False


@pytest.mark.asyncio
async def test_required_info_false_string_not_found():
    """既有 fail 方向維持：'False' 判定缺資訊，觸發追問（query_done=True）。"""
    from core.query_analysis.required_info import RequiredInfo

    handler = _make_handler()
    det = RequiredInfo(handler)

    async def fake_run_prompt(*a, **k):
        return {"required_info_found": "False"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.required_info_found is False
    assert handler.query_done is True


@pytest.mark.asyncio
async def test_required_info_boolean_false_not_found():
    """既有 fail 方向維持：JSON 布林 False 同樣判定缺資訊。"""
    from core.query_analysis.required_info import RequiredInfo

    handler = _make_handler()
    det = RequiredInfo(handler)

    async def fake_run_prompt(*a, **k):
        return {"required_info_found": False}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.required_info_found is False
    assert handler.query_done is True


@pytest.mark.asyncio
async def test_required_info_missing_key_fail_open_unaffected():
    """既有缺 key fail-open 行為不受本修法影響：缺 key 仍視為「已有必要資訊」。"""
    from core.query_analysis.required_info import RequiredInfo

    handler = _make_handler()
    det = RequiredInfo(handler)

    async def fake_run_prompt(*a, **k):
        return {"unexpected": "shape"}

    det.run_prompt = fake_run_prompt

    await asyncio.wait_for(det.do(), timeout=2.0)
    assert handler.required_info_found is True
    assert handler.query_done is False
