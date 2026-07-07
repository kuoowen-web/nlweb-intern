"""B1（AR round-4 根解）— keepalive flag 啟動時凍結（startup-only freeze）。

CEO 拍板：openai_keepalive_timeout 為 **startup-only** flag，運行中改變需重啟 server。

AR round-4 三家一致抓到 round-3「client 建立時記 flag + call site 比對 + 不一致 raise」
有兩個 blocker：(1) raise 的 RuntimeError 被上層 broad except 吞成 silent fallback；
(2) flag 比對與分支選擇間有 TOCTOU race。

根解：flag 在啟動時讀一次就凍結（core.openai_http.keepalive_timeout_enabled 套 lru_cache），
之後所有呼叫回同一個值 → 不可能 mismatch → 兩個 blocker 整個消失。不再需要 fail-loud、
不需要比對、不需要專屬 exception。

本檔驗新契約：
- flag 凍結後，即使 CONFIG 改變，keepalive_timeout_enabled() 仍回啟動時的值。
- client 建立 + 重複取用，CONFIG 在運行中翻動也**不 raise**（凍結值與 call site 一致）。
- 不再有 _client_keepalive_flag / _instructor_client_keepalive_flag 欄位與 assert 機制。

⚠ lru_cache 紀律：每個 test setup/teardown 必須 keepalive_timeout_enabled.cache_clear()，
否則 cache 在 test 之間污染。

零真實 API call。
"""
import httpx
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pydantic import BaseModel

import core.openai_http as openai_http
import reasoning.agents.base as base_mod
from llm_providers.openai import OpenAIProvider
from core.openai_http import keepalive_timeout_enabled


class _Schema(BaseModel):
    foo: str


def _fake_cfg():
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    cfg.llm_endpoints = {"openai": ep}
    return cfg


def _fake_http_config(flag_value: bool):
    """為 core.openai_http.CONFIG 建一個回傳指定 flag 的 fake config。"""
    cfg = MagicMock()
    cfg.reasoning_params = {"features": {"openai_keepalive_timeout": flag_value}}
    return cfg


@pytest.fixture(autouse=True)
def _reset_state():
    # lru_cache 紀律：每個 test 前後清 cache，避免凍結值在 test 間污染。
    keepalive_timeout_enabled.cache_clear()
    OpenAIProvider._client = None
    base_mod._instructor_client = None
    yield
    keepalive_timeout_enabled.cache_clear()
    OpenAIProvider._client = None
    base_mod._instructor_client = None


# ---------------------------------------------------------------------------
# 凍結契約（核心）：startup 讀一次後凍結，運行中改 CONFIG 不生效
# ---------------------------------------------------------------------------
def test_flag_frozen_at_startup_off_stays_off():
    """啟動讀到 OFF → 之後 CONFIG 翻 ON，函式仍回 OFF（凍結）。"""
    with patch("core.openai_http.CONFIG", _fake_http_config(False)):
        assert keepalive_timeout_enabled() is False  # 第一次讀 = 凍結點
    # CONFIG 翻成 ON，但凍結值不變
    with patch("core.openai_http.CONFIG", _fake_http_config(True)):
        assert keepalive_timeout_enabled() is False


def test_flag_frozen_at_startup_on_stays_on():
    """啟動讀到 ON → 之後 CONFIG 翻 OFF，函式仍回 ON（凍結）。"""
    with patch("core.openai_http.CONFIG", _fake_http_config(True)):
        assert keepalive_timeout_enabled() is True
    with patch("core.openai_http.CONFIG", _fake_http_config(False)):
        assert keepalive_timeout_enabled() is True


def test_cache_clear_allows_refreeze():
    """cache_clear 後可重新凍結（test 紀律 + restart 語義模擬）。"""
    with patch("core.openai_http.CONFIG", _fake_http_config(False)):
        assert keepalive_timeout_enabled() is False
    keepalive_timeout_enabled.cache_clear()  # 模擬重啟
    with patch("core.openai_http.CONFIG", _fake_http_config(True)):
        assert keepalive_timeout_enabled() is True


# ---------------------------------------------------------------------------
# fail-loud 機制已移除：欄位 / assert 方法不再存在
# ---------------------------------------------------------------------------
def test_provider_no_lock_in_field():
    """round-3 的 _client_keepalive_flag 欄位已移除。"""
    assert not hasattr(OpenAIProvider, "_client_keepalive_flag")


def test_provider_no_assert_method():
    """round-3 的 _assert_keepalive_flag_unchanged 方法已移除。"""
    assert not hasattr(OpenAIProvider, "_assert_keepalive_flag_unchanged")


def test_instructor_no_lock_in_global():
    """round-3 的 _instructor_client_keepalive_flag module-global 已移除。"""
    assert not hasattr(base_mod, "_instructor_client_keepalive_flag")


def test_instructor_no_assert_func():
    """round-3 的 _assert_instructor_keepalive_flag_unchanged function 已移除。"""
    assert not hasattr(base_mod, "_assert_instructor_keepalive_flag_unchanged")


# ---------------------------------------------------------------------------
# Provider：CONFIG 運行中翻動不再 raise（凍結值與 call site 一致）
# ---------------------------------------------------------------------------
def test_provider_get_client_does_not_raise_when_config_changes():
    """client 以 frozen OFF 建立後，CONFIG 翻 ON，再取用 client **不 raise**（凍結）。"""
    # 凍結為 OFF + 建 client
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.openai_http.CONFIG", _fake_http_config(False)):
        c1 = OpenAIProvider.get_client()
    # CONFIG 翻 ON：再取用不該 raise，且回同一 singleton（凍結值仍 OFF）
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.openai_http.CONFIG", _fake_http_config(True)):
        c2 = OpenAIProvider.get_client()
    assert c1 is c2


def test_provider_idempotent_get_client():
    """正常情況（CONFIG 不變）：多次取用回同一 client、不 raise。"""
    with patch("llm_providers.openai.CONFIG", _fake_cfg()), \
         patch("core.openai_http.CONFIG", _fake_http_config(True)):
        c1 = OpenAIProvider.get_client()
        c2 = OpenAIProvider.get_client()
    assert c1 is c2


# ---------------------------------------------------------------------------
# instructor client（base.py）：CONFIG 運行中翻動不再 raise
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_instructor_get_client_does_not_raise_when_config_changes():
    """instructor client 以 frozen OFF 建立後，CONFIG 翻 ON，再取用 **不 raise**。"""
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "AsyncOpenAI", lambda **k: MagicMock()), \
         patch.object(base_mod.instructor, "from_openai", lambda c, mode=None: c), \
         patch("core.openai_http.CONFIG", _fake_http_config(False)):
        c1 = await base_mod._get_instructor_client()
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch("core.openai_http.CONFIG", _fake_http_config(True)):
        c2 = await base_mod._get_instructor_client()
    assert c1 is c2


@pytest.mark.asyncio
async def test_instructor_idempotent_get_client():
    """instructor 正常情況（CONFIG 不變）：多次取用回同一 client、不 raise。"""
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "AsyncOpenAI", lambda **k: MagicMock()), \
         patch.object(base_mod.instructor, "from_openai", lambda c, mode=None: c), \
         patch("core.openai_http.CONFIG", _fake_http_config(True)):
        c1 = await base_mod._get_instructor_client()
        c2 = await base_mod._get_instructor_client()
    assert c1 is c2
