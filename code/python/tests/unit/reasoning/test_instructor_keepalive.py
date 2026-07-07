"""instructor client 帶 keepalive http_client（無條件）+ flag-gated timeout/retry。
零真實 API call：patch AsyncOpenAI + instructor.from_openai，驗構造 kwargs。"""
import httpx
import pytest
from unittest.mock import patch, MagicMock

import reasoning.agents.base as base_mod


@pytest.fixture(autouse=True)
def _reset_singleton():
    base_mod._instructor_client = None
    yield
    base_mod._instructor_client = None


def _fake_cfg():
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    cfg.llm_endpoints = {"openai": ep}  # 真 dict 原生有 .get（base.py:117 用 .get("openai")）
    return cfg


@pytest.mark.asyncio
async def test_instructor_client_gets_keepalive_http_client_unconditionally():
    """keepalive 無條件套（flag-OFF 也要傳 keepalive http_client 給 AsyncOpenAI）。"""
    captured = {}
    def _fake_async_openai(**kwargs):
        captured.update(kwargs)
        return MagicMock()
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "AsyncOpenAI", _fake_async_openai), \
         patch.object(base_mod.instructor, "from_openai", lambda c, mode=None: c), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=False):
        await base_mod._get_instructor_client()
    assert "http_client" in captured
    assert isinstance(captured["http_client"], httpx.AsyncClient)
    # flag-OFF：純 keepalive，不設分項 timeout（http_client 用 httpx 預設 5s，非收斂的 sliced timeout）。
    # 收斂 timeout 是套在 http_client 上（非 AsyncOpenAI(timeout=) kwarg），flag-OFF 時 read 應為預設 5。
    assert captured["http_client"].timeout.read == 5  # httpx 預設，非收斂 read=45


@pytest.mark.asyncio
async def test_instructor_client_flag_on_sets_timeout_and_retries():
    """flag-ON：keepalive http_client 帶分項 timeout(httpx.Timeout, read=45/write=30) + max_retries。

    STOP-and-adjust（plan Task 5 Step 1 原 test）：收斂 timeout 是套在 http_client 上
    （make_keepalive_async_client(timeout=...)），**不是** AsyncOpenAI(timeout=) 獨立 kwarg
    （見 Task 5 Step 3 code）。故斷言改驗 http_client.timeout，這才是 load-bearing 行為。"""
    captured = {}
    def _fake_async_openai(**kwargs):
        captured.update(kwargs)
        return MagicMock()
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "AsyncOpenAI", _fake_async_openai), \
         patch.object(base_mod.instructor, "from_openai", lambda c, mode=None: c), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        await base_mod._get_instructor_client()
    assert "http_client" in captured
    http_client = captured["http_client"]
    assert isinstance(http_client.timeout, httpx.Timeout)
    assert http_client.timeout.read == 45    # 收斂 read placeholder
    assert http_client.timeout.write == 30   # 收斂 write placeholder
    assert isinstance(captured.get("max_retries"), int)
