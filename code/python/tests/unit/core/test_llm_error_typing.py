"""ask_llm 失敗時回帶型別 sentinel（LLMError），不再吞成裸 None。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from core.llm import ask_llm, LLMError


def _mk_provider(side_effect):
    prov = AsyncMock()
    prov.get_completion = AsyncMock(side_effect=side_effect)
    return prov


@pytest.mark.asyncio
async def test_ask_llm_timeout_returns_typed_error():
    """provider timeout → LLMError(error_kind='timeout')，且 falsy + 是 dict。"""
    async def _timeout(*a, **k):
        raise asyncio.TimeoutError()

    with patch("core.llm._get_provider", return_value=_mk_provider(_timeout)):
        resp = await ask_llm("p", {}, provider="openai", timeout=1)

    assert isinstance(resp, LLMError)
    assert isinstance(resp, dict)          # 相容 (resp or {}).get / isinstance(resp, dict)
    assert not resp                         # falsy：相容 `if not response:`
    assert resp.error_kind == "timeout"
    assert resp.get("anything", "x") == "x"  # 相容 .get（#7 deep_research）


@pytest.mark.asyncio
async def test_ask_llm_provider_exception_returns_typed_error():
    """provider 一般 exception → LLMError(error_kind='provider_error')。"""
    async def _boom(*a, **k):
        raise RuntimeError("boom")

    with patch("core.llm._get_provider", return_value=_mk_provider(_boom)):
        resp = await ask_llm("p", {}, provider="openai", timeout=5)

    assert isinstance(resp, LLMError)
    assert resp.error_kind == "provider_error"
    assert not resp


@pytest.mark.asyncio
async def test_ask_llm_success_returns_plain_result():
    """成功時回原始 result，不包 LLMError。"""
    async def _ok(*a, **k):
        return {"foo": "bar"}

    with patch("core.llm._get_provider", return_value=_mk_provider(_ok)):
        resp = await ask_llm("p", {}, provider="openai", timeout=5)

    assert resp == {"foo": "bar"}
    assert not isinstance(resp, LLMError)


# ── AR round 1（Codex #1）：LLMError sentinel 不變量 contract test（不打真 LLM）──

def test_llmerror_falsy_contract():
    """釘死 27-caller 相容的 falsy 不變量：bool / .get / isinstance×2 / 即使有 item 仍 falsy。"""
    e = LLMError("timeout", "x")
    # (1) falsy：相容 `if not response:`
    assert bool(e) is False
    assert not e
    # (2) 是 dict：相容 `(resp or {}).get` / `isinstance(resp, dict)`
    assert isinstance(e, dict)
    # (3) 是 LLMError：base.py 用 isinstance(x, LLMError) 偵測分型
    assert isinstance(e, LLMError)
    # (4) .get 安全（#7 deep_research 直接 .get）
    assert e.get("anything", "default") == "default"
    # (5) error_kind 是 attribute 非 dict item（避免污染 caller 的 dict 判斷）
    assert "error_kind" not in e
    assert e.error_kind == "timeout"
    assert e is not None
    # (6) 防禦：即使有人誤塞 dict item（len>0），__bool__ 仍釘 False
    e["leaked"] = 1
    assert bool(e) is False, "__bool__ 必須無視 dict 內容恆 False（防誤存 item 翻 True）"


# ── 收斂路徑：flag-ON 時 layer2 拆 wait_for + low-tier 安全網不變量 ──

@pytest.mark.asyncio
async def test_ask_llm_flag_on_high_tier_no_outer_wait_for():
    """flag-ON + _use_sdk_retry=True（high-tier）：不包 asyncio.wait_for，
    get_completion 回的 LLMError(timeout) 直接上傳（不被外層砍/改）。"""
    async def _timeout_sentinel(*a, **k):
        return LLMError("timeout", "transport timeout")
    with patch("core.llm._get_provider", return_value=_mk_provider(_timeout_sentinel)), \
         patch("core.llm.keepalive_timeout_enabled", return_value=True):
        resp = await ask_llm("p", {}, provider="openai", timeout=60, _use_sdk_retry=True)
    assert isinstance(resp, LLMError)
    assert resp.error_kind == "timeout"


@pytest.mark.asyncio
async def test_ask_llm_flag_on_low_tier_keeps_asyncio_safety_net():
    """flag-ON + 不傳 _use_sdk_retry（low-tier）：仍保留外層 asyncio.wait_for 安全網（60s）。
    用一個真的 hang 的 get_completion 驗 wait_for 仍會 fire → LLMError(timeout)。"""
    async def _hang_forever(*a, **k):
        import asyncio
        await asyncio.sleep(10)
        return {"foo": "bar"}
    with patch("core.llm._get_provider", return_value=_mk_provider(_hang_forever)), \
         patch("core.llm.keepalive_timeout_enabled", return_value=True):
        resp = await ask_llm("p", {}, provider="openai", timeout=1)  # 1s 安全網 fire
    assert isinstance(resp, LLMError)
    assert resp.error_kind == "timeout"


@pytest.mark.asyncio
async def test_ask_llm_flag_off_low_tier_unchanged():
    """flag-OFF：low-tier 行為逐字等價現狀（舊 wait_for 路徑）。"""
    async def _hang_forever(*a, **k):
        import asyncio
        await asyncio.sleep(10)
        return {"foo": "bar"}
    with patch("core.llm._get_provider", return_value=_mk_provider(_hang_forever)), \
         patch("core.llm.keepalive_timeout_enabled", return_value=False):
        resp = await ask_llm("p", {}, provider="openai", timeout=1)
    assert isinstance(resp, LLMError)
    assert resp.error_kind == "timeout"
