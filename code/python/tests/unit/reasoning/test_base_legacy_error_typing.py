"""base.py legacy 路徑對 LLMError sentinel 分型：timeout 不誤標 empty、不 retry。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from core.llm import LLMError
from reasoning.agents.base import BaseReasoningAgent


class _Schema(BaseModel):
    foo: str


def _agent():
    handler = MagicMock()
    handler.query_params = {}
    # 直接實例化 BaseReasoningAgent 的最小可用子類
    a = BaseReasoningAgent(handler=handler, agent_name="t", timeout=30, max_retries=3)
    # 強制走 legacy 路徑（TypeAgent disabled）
    a._is_typeagent_enabled = MagicMock(return_value=False)
    return a


@pytest.mark.asyncio
async def test_legacy_timeout_sentinel_raises_timeouterror_not_empty():
    """ask_llm 回 LLMError('timeout') → raise TimeoutError，不誤標 'empty response'。"""
    agent = _agent()
    with patch("reasoning.agents.base.ask_llm",
               new=AsyncMock(return_value=LLMError("timeout", "timed out"))):
        with pytest.raises(TimeoutError):
            await agent._legacy_call_llm_validated("p", _Schema, level="low")


@pytest.mark.asyncio
async def test_legacy_timeout_sentinel_no_retry():
    """timeout sentinel 不該觸發 retry（max_retries 次呼叫應為 1）。"""
    agent = _agent()
    mock = AsyncMock(return_value=LLMError("timeout", "timed out"))
    with patch("reasoning.agents.base.ask_llm", new=mock):
        with pytest.raises(TimeoutError):
            await agent._legacy_call_llm_validated("p", _Schema, level="low")
    assert mock.await_count == 1, f"timeout 不應 retry，實際呼叫 {mock.await_count} 次"


@pytest.mark.asyncio
async def test_legacy_provider_error_sentinel_raises_typed_not_empty():
    """ask_llm 回 LLMError('provider_error') → raise 帶型別訊息，不誤標 'empty response'。"""
    agent = _agent()
    with patch("reasoning.agents.base.ask_llm",
               new=AsyncMock(return_value=LLMError("provider_error", "boom"))):
        with pytest.raises(Exception) as exc_info:
            await agent._legacy_call_llm_validated("p", _Schema, level="low")
    assert "empty response" not in str(exc_info.value).lower(), \
        "provider_error 不可被誤標為 empty response"


@pytest.mark.asyncio
async def test_legacy_genuine_empty_still_raises_empty():
    """真 None / 空 dict（非 LLMError）→ 維持 'empty response' 語意。"""
    agent = _agent()
    with patch("reasoning.agents.base.ask_llm", new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError) as exc_info:
            await agent._legacy_call_llm_validated("p", _Schema, level="low")
    assert "empty response" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_legacy_flag_on_passes_use_sdk_retry_and_no_outer_wait_for():
    """flag-ON：layer1a 不包外層 wait_for + 以 _use_sdk_retry=True 呼叫 ask_llm。
    ask_llm 回 LLMError(timeout) → 既有 sentinel 分型 → raise TimeoutError（不誤標 empty）。"""
    agent = _agent()
    captured = {}
    async def _fake_ask_llm(*a, **k):
        captured.update(k)
        return LLMError("timeout", "transport timeout")
    with patch("reasoning.agents.base.ask_llm", new=_fake_ask_llm), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        with pytest.raises(TimeoutError):
            await agent._legacy_call_llm_validated("p", _Schema, level="low")
    assert captured.get("_use_sdk_retry") is True, "high-tier 必須帶 _use_sdk_retry=True"


@pytest.mark.asyncio
async def test_legacy_flag_off_unchanged_inner_timeout_path():
    """flag-OFF：維持 RSN-2 inner_timeout 舊路徑（不傳 _use_sdk_retry，包外層 wait_for）。"""
    agent = _agent()
    captured = {}
    async def _fake_ask_llm(*a, **k):
        captured.update(k)
        return LLMError("timeout", "timed out")
    with patch("reasoning.agents.base.ask_llm", new=_fake_ask_llm), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=False):
        with pytest.raises(TimeoutError):
            await agent._legacy_call_llm_validated("p", _Schema, level="low")
    # flag-OFF：仍傳 inner timeout（self.timeout-10），不帶 _use_sdk_retry
    assert captured.get("_use_sdk_retry") in (None, False)
    assert "timeout" in captured  # 舊路徑傳 inner_timeout
