"""收斂行為鏈 test（AR Critical 補強）：flag-ON 時 transport timeout 透過
provider.get_completion → ask_llm → _legacy_call_llm_validated 完整鏈（legacy path），
表面必須是 TimeoutError（源自 ERROR_KIND_TIMEOUT），不是 'empty response' ValueError /
provider_error。並含 low-tier negative assertion（拆層後不變）。
C2 補強：末尾兩個 test 走 instructor PRIMARY path（generate_structured），驗 instructor
把 transport timeout 包成 InstructorRetryException 後仍正確分型成 TimeoutError。零真實 API call。"""
import asyncio
import httpx
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pydantic import BaseModel

import openai
from instructor.core import InstructorRetryException  # 親驗：頂層 `from instructor import` 會 ImportError
from core.llm import LLMError, ERROR_KIND_TIMEOUT, ask_llm
from llm_providers.openai import OpenAIProvider
import reasoning.agents.base as base_mod
from reasoning.agents.base import BaseReasoningAgent, generate_structured


class _Schema(BaseModel):
    foo: str


def _fake_cfg():
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    ep.models.low = "gpt-4o-mini"
    ep.llm_type = "openai"
    cfg.llm_endpoints = {"openai": ep}
    cfg.get_llm_provider = lambda n: ep
    cfg.preferred_llm_endpoint = "openai"
    cfg.is_development_mode = lambda: False
    return cfg


def _agent():
    handler = MagicMock()
    handler.query_params = {}
    a = BaseReasoningAgent(handler=handler, agent_name="t", timeout=30, max_retries=1)
    a._is_typeagent_enabled = MagicMock(return_value=False)
    return a


@pytest.mark.asyncio
async def test_full_chain_transport_timeout_surfaces_as_timeouterror_not_empty():
    """flag-ON：responses.create raise APITimeoutError → 全鏈 → raise TimeoutError，
    訊息不含 'empty response'。這正是舊 plan 抓不到的 behavioral regression。"""
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    fake_client = MagicMock()
    fake_client.responses.create = AsyncMock(side_effect=openai.APITimeoutError(request=req))
    OpenAIProvider._client = None
    prov = OpenAIProvider()

    with patch("core.llm.CONFIG", _fake_cfg()), \
         patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.llm._get_provider", return_value=prov), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True), \
         patch("core.llm.keepalive_timeout_enabled", return_value=True), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        agent = _agent()
        with pytest.raises(TimeoutError) as exc:
            await agent._legacy_call_llm_validated("p", _Schema, level="high")
    msg = str(exc.value).lower()
    assert "empty response" not in msg, "transport timeout 不可被誤標成 empty response（舊 plan bug）"


@pytest.mark.asyncio
async def test_full_chain_flag_on_no_double_wait_for_lets_retry_budget_run():
    """flag-ON high-tier：get_completion 不包 wait_for（讓 SDK retry 在內進行）。
    驗 responses.create 被呼叫（SDK 層可 retry），而非被外層 asyncio 提早砍。"""
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    fake_client = MagicMock()
    fake_client.responses.create = AsyncMock(side_effect=openai.APITimeoutError(request=req))
    OpenAIProvider._client = None
    prov = OpenAIProvider()
    with patch("core.llm.CONFIG", _fake_cfg()), \
         patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.llm._get_provider", return_value=prov), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True), \
         patch("core.llm.keepalive_timeout_enabled", return_value=True):
        result = await ask_llm("p", {}, provider="openai", timeout=60, _use_sdk_retry=True)
    assert isinstance(result, LLMError)
    assert result.error_kind == ERROR_KIND_TIMEOUT
    assert fake_client.responses.create.await_count >= 1


@pytest.mark.asyncio
async def test_low_tier_negative_assertion_flag_on_keeps_asyncio_safety_net():
    """low-tier negative assertion（拆層後不變）：直接 ask_llm（不傳 _use_sdk_retry）
    flag-ON 時仍有外層 asyncio.wait_for 安全網 → 真 hang 仍被 timeout 接住成 LLMError(timeout)。"""
    async def _hang(*a, **k):
        await asyncio.sleep(10)
        return {"foo": "bar"}
    fake_client = MagicMock()
    fake_client.responses.create = AsyncMock(side_effect=_hang)
    OpenAIProvider._client = None
    prov = OpenAIProvider()
    with patch("core.llm.CONFIG", _fake_cfg()), \
         patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.llm._get_provider", return_value=prov), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True), \
         patch("core.llm.keepalive_timeout_enabled", return_value=True):
        result = await ask_llm("p", {}, provider="openai", timeout=1)  # low-tier：不傳 _use_sdk_retry
    assert isinstance(result, LLMError)
    assert result.error_kind == ERROR_KIND_TIMEOUT  # 安全網 fire，行為等價現狀


# ── C2 覆蓋：instructor PRIMARY path（上面三個都走 legacy path，測不到 instructor 包裝問題）──

def _fake_cfg_instructor():
    """generate_structured 用的 cfg。llm_endpoints 用真 dict，base.py:117 的 .get("openai") 走原生 .get。"""
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    cfg.llm_endpoints = {"openai": ep}  # 真 dict 原生有 .get（base.py:117 用 .get("openai")）
    return cfg


def _instructor_wrapped_timeout(req):
    """模擬 instructor 1.15.3 包裝後的實際例外：transport timeout 永遠被 instructor 外層 try
    包成 InstructorRetryException，原始 APITimeoutError 在 __cause__（親驗 retry.py:418-532 +
    親驗 AsyncMock(side_effect=exc) 保留預設好的 __cause__）。"""
    try:
        raise openai.APITimeoutError(request=req)
    except openai.APITimeoutError as cause:
        exc = InstructorRetryException("timeout", n_attempts=1, total_usage=0)
        exc.__cause__ = cause
        return exc


@pytest.mark.asyncio
async def test_instructor_primary_path_wrapped_timeout_surfaces_as_timeouterror_not_generic():
    """flag-ON instructor PRIMARY path（C2 行為鏈）：instructor client.create() 拋
    InstructorRetryException(__cause__=APITimeoutError)（instructor 包裝後的真實 exception 型別）→
    generate_structured 必須 unwrap __cause__ 分型成 TimeoutError，**不可**落 generic except Exception。

    這正是上面三個 legacy-path test 抓不到的 C2：legacy 走 ask_llm→get_completion 不經 instructor，
    沒有 InstructorRetryException 包裝層；instructor 包裝問題只在 primary path 出現。
    若實作仍直接 catch 裸 APITimeoutError（C2 修補前的 bug），這個 InstructorRetryException 會 miss
    → 被當 unexpected generic error re-raise（非 TimeoutError）→ 本 test fail。零真實 API call。"""
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_instructor_wrapped_timeout(req))
    with patch.object(base_mod, "CONFIG", _fake_cfg_instructor()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        with pytest.raises(TimeoutError) as exc:
            await generate_structured("p", _Schema, max_retries=3, timeout=90)
    # 負向斷言：不可被當 generic unexpected error（型別必須是 TimeoutError，非裸 InstructorRetryException）
    assert not isinstance(exc.value, InstructorRetryException), \
        "transport timeout 不可原樣冒出 InstructorRetryException（C2：須 unwrap 分型成 TimeoutError）"


@pytest.mark.asyncio
async def test_instructor_primary_path_non_timeout_retry_exhaust_not_mislabeled_timeout():
    """flag-ON instructor PRIMARY path 負向不變量：instructor retry 因 schema validation 連續失敗耗盡
    （__cause__ 是 ValidationError 非 transport timeout）時，**不可**被誤標成 TimeoutError。
    確認 C2 的 unwrap 只在 __cause__ 是 APITimeoutError/APIConnectionError 才分型成 timeout，
    其餘維持原 generic re-raise（不假裝成 timeout）。"""
    # 構造一個 __cause__ 為非 transport 例外（模擬 schema validation 連續失敗耗盡）的 InstructorRetryException
    exc = InstructorRetryException("schema retries exhausted", n_attempts=3, total_usage=0)
    exc.__cause__ = ValueError("validation failed")  # 非 APITimeoutError
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=exc)
    with patch.object(base_mod, "CONFIG", _fake_cfg_instructor()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        with pytest.raises(Exception) as exc_info:
            await generate_structured("p", _Schema, max_retries=3, timeout=90)
    assert not isinstance(exc_info.value, TimeoutError), \
        "非 transport 的 retry 耗盡不可被誤標成 TimeoutError（C2 unwrap 只認 APITimeout/APIConnection）"
