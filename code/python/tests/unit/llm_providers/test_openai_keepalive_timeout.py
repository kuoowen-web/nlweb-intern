"""provider client wiring（keepalive http_client）+ get_completion flag-ON/OFF 行為
+ error taxonomy（APITimeoutError → LLMError(timeout)，不再 return {}）。零真實 API call。"""
import httpx
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import openai
from core.llm import LLMError, ERROR_KIND_TIMEOUT, ERROR_KIND_PROVIDER_ERROR
from llm_providers.openai import OpenAIProvider


@pytest.fixture(autouse=True)
def _reset_singleton():
    OpenAIProvider._client = None
    yield
    OpenAIProvider._client = None


def _fake_cfg():
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    cfg.llm_endpoints = {"openai": ep}
    return cfg


def test_get_client_has_keepalive_http_client_unconditionally():
    """keepalive 無條件套（flag-OFF 也要有 keepalive http_client）。"""
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.openai_http.keepalive_timeout_enabled", return_value=False):
        client = OpenAIProvider.get_client()
    assert isinstance(client._client, httpx.AsyncClient) or client._client is not None
    # SDK 把傳入的 http_client 存於 client._client（openai 2.43.0）
    assert isinstance(client._client, httpx.AsyncClient)


@pytest.mark.asyncio
async def test_get_completion_apitimeout_returns_llmerror_timeout_flag_on():
    """flag-ON：responses.create raise APITimeoutError → 回 LLMError(timeout)，不是 {}。"""
    prov = OpenAIProvider()
    fake_client = MagicMock()
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    fake_client.responses.create = AsyncMock(side_effect=openai.APITimeoutError(request=req))
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True):
        result = await prov.get_completion("p", {}, model="gpt-5.1")
    assert isinstance(result, LLMError)
    assert result.error_kind == ERROR_KIND_TIMEOUT
    assert result == {} or not result  # falsy 相容（但是 LLMError 非裸 dict）
    assert isinstance(result, LLMError)


@pytest.mark.asyncio
async def test_get_completion_apiconnection_returns_llmerror_timeout_flag_on():
    """flag-ON：APIConnectionError（NAT drop 類）→ LLMError(timeout)。"""
    prov = OpenAIProvider()
    fake_client = MagicMock()
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    fake_client.responses.create = AsyncMock(side_effect=openai.APIConnectionError(request=req))
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True):
        result = await prov.get_completion("p", {}, model="gpt-5.1")
    assert isinstance(result, LLMError)
    assert result.error_kind == ERROR_KIND_TIMEOUT


@pytest.mark.asyncio
async def test_get_completion_other_exception_returns_llmerror_provider_flag_on():
    """flag-ON：其他 exception → LLMError(provider_error)，不再 return {}。"""
    prov = OpenAIProvider()
    fake_client = MagicMock()
    fake_client.responses.create = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True):
        result = await prov.get_completion("p", {}, model="gpt-5.1")
    assert isinstance(result, LLMError)
    assert result.error_kind == ERROR_KIND_PROVIDER_ERROR


@pytest.mark.asyncio
async def test_get_completion_flag_off_keeps_legacy_asyncio_timeout_returns_empty():
    """flag-OFF：行為等價現狀（asyncio.TimeoutError → return {}，舊路徑原封）。"""
    prov = OpenAIProvider()
    fake_client = MagicMock()
    async def _hang(*a, **k):
        import asyncio
        raise asyncio.TimeoutError()
    fake_client.responses.create = AsyncMock(side_effect=_hang)
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=False):
        result = await prov.get_completion("p", {}, model="gpt-5.1", timeout=1)
    assert result == {}
    assert not isinstance(result, LLMError)  # flag-OFF 維持裸 {} 現狀


@pytest.mark.asyncio
async def test_get_completion_flag_on_forwards_timeout_to_create():
    """B2（AR round-3 blocker）：flag-ON 路徑的 responses.create 必須補傳 timeout=（caller
    per-agent wall-clock），否則只剩 client-level httpx read=45，所有 agent 被壓成 read=45，
    遺失 _legacy_call_llm_validated 傳的 self.timeout（writer 90 / critic 120 / analyst 300）。
    OpenAI SDK request-level timeout 覆蓋 client-level httpx timeout。"""
    prov = OpenAIProvider()
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.output_text = '{"ok": true}'
    fake_client.responses.create = AsyncMock(return_value=fake_resp)
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=True):
        result = await prov.get_completion("p", {}, model="gpt-5.1", timeout=300)
    assert result == {"ok": True}
    _, kwargs = fake_client.responses.create.call_args
    assert kwargs.get("timeout") == 300, (
        "B2：flag-ON 必須補傳 timeout= 給 responses.create（覆蓋 client-level httpx read），"
        "否則 caller 的 per-agent wall-clock 被丟掉"
    )


@pytest.mark.asyncio
async def test_get_completion_flag_off_does_not_pass_timeout_to_create():
    """flag-OFF：舊路徑用 asyncio.wait_for 控 timeout，create() 本身不傳 timeout=（原封不動）。"""
    prov = OpenAIProvider()
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.output_text = '{"ok": true}'
    fake_client.responses.create = AsyncMock(return_value=fake_resp)
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch.object(OpenAIProvider, "get_client", return_value=fake_client), \
         patch("llm_providers.openai.keepalive_timeout_enabled", return_value=False):
        result = await prov.get_completion("p", {}, model="gpt-5.1", timeout=300)
    assert result == {"ok": True}
    _, kwargs = fake_client.responses.create.call_args
    assert "timeout" not in kwargs, "flag-OFF：create() 不該收 timeout=（由外層 wait_for 控）"
