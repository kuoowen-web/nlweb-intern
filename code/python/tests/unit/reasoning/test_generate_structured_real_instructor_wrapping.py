"""should-fix 2（AR round-3）— 真 instructor-wrapped integration-style unit test。

現有 C2 test 手動構造 InstructorRetryException(__cause__=APITimeoutError)，沒驗
「instructor RESPONSES_TOOLS 真的會 forward timeout= 並把 transport timeout 包成
InstructorRetryException」。本檔用一個**真的過 instructor 包裝**的 fake 路徑
（讓 instructor 自己包，而非手動構造 exception），證明：
  (a) timeout= 真的被 forward 進 instructor → 下傳到底層 responses.create；
  (b) transport timeout（APITimeoutError）真的被 instructor 包成 InstructorRetryException
      並被 C2 unwrap 成 TimeoutError。

關鍵（親驗 instructor 1.15.3）：
  - instructor.from_openai(base, mode=Mode.RESPONSES_TOOLS) 走 **Responses API** →
    真正被呼叫的底層是 `base.responses.create`（**不是** chat.completions.create）。
    若誤 patch chat.completions.create，instructor 仍會打真 responses.create → 真實
    網路 401（親驗踩過）。故必須 patch `base.responses.create`。
  - instructor v2 retry loop（.venv/.../instructor/v2/core/retry.py:158）只在
    kwargs["timeout"] 是 int/float 時把 stop_after_delay(timeout) OR 進 stop_condition；
    且 patch wrapper 不 pop timeout → 原樣下傳 responses.create。本 test 驗收到的 kwargs 含 timeout。
  - APITimeoutError 非 _RETRYABLE_PARSE_ERRORS → tenacity 立即 reraise → 被 instructor 外層
    try 包成 InstructorRetryException(__cause__=APITimeoutError)（retry.py:261-287）。

零真實 API call（patch 掉底層 responses.create + 哨兵擋 chat.completions.create）。
"""
import asyncio
import httpx
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pydantic import BaseModel

import openai
import instructor
from instructor import Mode
from instructor.core import InstructorRetryException
import reasoning.agents.base as base_mod


class _Schema(BaseModel):
    foo: str


def _fake_cfg():
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    cfg.llm_endpoints = {"openai": ep}
    return cfg


def _build_real_instructor_client_with_timeout_capture(captured: dict, exc: Exception):
    """建一個**真**的 instructor-wrapped client：真 AsyncOpenAI + 真 instructor.from_openai，
    只把底層 Responses API 的 transport 換成會 raise `exc` 的 fake（並擷取它收到的 kwargs）。
    擋 chat.completions.create 以偵測誤打網路路徑（RESPONSES_TOOLS 應走 responses.create）。"""
    base = openai.AsyncOpenAI(api_key="sk-test")

    async def _fake_responses_create(*args, **kwargs):
        captured.update(kwargs)
        raise exc

    async def _boom_chat(*args, **kwargs):
        raise AssertionError(
            "instructor 走了 chat.completions.create（應走 responses.create）—— "
            "RESPONSES_TOOLS mode 假設被打破，會打真網路"
        )

    base.responses.create = _fake_responses_create
    base.chat.completions.create = _boom_chat
    # 真 instructor 包裝（非 mock）：它自己跑 retry loop + 包 exception。
    return instructor.from_openai(base, mode=Mode.RESPONSES_TOOLS)


@pytest.mark.asyncio
async def test_real_instructor_wrapping_forwards_timeout_and_unwraps_to_timeouterror():
    """核心 should-fix 2：真 instructor 包裝下，
    (a) timeout= 被 forward 進底層 responses.create；
    (b) APITimeoutError 被 instructor 真實包成 InstructorRetryException 並被 C2 unwrap 成 TimeoutError。
    全程零真實 API call。"""
    captured: dict = {}
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    wrapped = _build_real_instructor_client_with_timeout_capture(
        captured, openai.APITimeoutError(request=req)
    )
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=wrapped)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        with pytest.raises(TimeoutError) as exc_info:
            await base_mod.generate_structured("p", _Schema, max_retries=2, timeout=37)

    # (a) timeout= 真的被 forward 進 instructor → 下傳到底層 responses.create
    assert "timeout" in captured, "C1：timeout= 必須被 instructor forward 到底層 responses.create"
    assert captured["timeout"] == 37, f"forward 的 timeout 值錯：{captured.get('timeout')}"
    # (b) C2：unwrap 成 TimeoutError（非裸 InstructorRetryException）
    assert not isinstance(exc_info.value, InstructorRetryException), \
        "C2：transport timeout 須 unwrap 分型成 TimeoutError，不可原樣冒 InstructorRetryException"
    assert "transport timeout" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_real_instructor_wrapping_actually_produces_instructor_retry_exception():
    """佐證（負向確認本 test 真的過 instructor 包裝層而非手動構造）：
    若把 C2 的 unwrap 暫時想像成不存在，generate_structured 內部從 client 收到的**就是**
    instructor 真實產生的 InstructorRetryException(__cause__=APITimeoutError)。
    這裡直接呼叫 wrapped client 驗 instructor 真的包了（不經 generate_structured）。"""
    captured: dict = {}
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    wrapped = _build_real_instructor_client_with_timeout_capture(
        captured, openai.APITimeoutError(request=req)
    )
    with pytest.raises(InstructorRetryException) as exc_info:
        await wrapped.chat.completions.create(
            model="gpt-5.1",
            response_model=_Schema,
            messages=[{"role": "user", "content": "p"}],
            max_retries=2,
            timeout=11,
        )
    # instructor 真的把 transport timeout 放進 __cause__
    assert isinstance(exc_info.value.__cause__, openai.APITimeoutError), \
        "instructor 應把原始 APITimeoutError 放進 InstructorRetryException.__cause__"
    # 且 timeout= 真的被 forward 下傳
    assert captured.get("timeout") == 11, "timeout= 必須被 instructor forward 到底層 responses.create"
