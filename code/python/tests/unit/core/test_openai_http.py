"""core/openai_http.py helper：keepalive client / sliced timeout / config 讀取。
零真實 OpenAI call，純物件 introspection + mock CONFIG。"""
import socket
import httpx
import pytest
from unittest.mock import patch, MagicMock

from core.openai_http import (
    make_keepalive_async_client,
    make_sliced_timeout,
    keepalive_timeout_enabled,
    get_read_timeout,
    get_write_timeout,
    get_max_retries,
)


def test_make_keepalive_client_is_async_client_with_so_keepalive():
    client = make_keepalive_async_client()
    assert isinstance(client, httpx.AsyncClient)
    # socket_options 存進 transport；至少含 SO_KEEPALIVE on/1
    transport = client._transport  # httpx.AsyncHTTPTransport
    pool = transport._pool
    opts = pool._socket_options
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in opts


def test_make_keepalive_client_platform_guarded_options():
    """平台不支援某 option 時不應 crash（hasattr guard）。"""
    client = make_keepalive_async_client()
    # 不論平台，建構成功即通過（Linux 會多 TCP_KEEPIDLE 等，Windows 只 SO_KEEPALIVE）
    assert isinstance(client, httpx.AsyncClient)


def test_make_keepalive_client_accepts_timeout():
    t = make_sliced_timeout(read=45, write=30)
    client = make_keepalive_async_client(timeout=t)
    assert client.timeout.read == 45
    assert client.timeout.write == 30
    assert client.timeout.connect == 5
    assert client.timeout.pool == 5


def test_make_sliced_timeout_values():
    t = make_sliced_timeout(read=45, write=30)
    assert isinstance(t, httpx.Timeout)
    assert t.read == 45 and t.write == 30 and t.connect == 5 and t.pool == 5


def test_make_sliced_timeout_read_must_be_under_600():
    """read>=600 是設計禁忌（retry×600 爆炸）→ 防呆 raise。"""
    with pytest.raises(ValueError):
        make_sliced_timeout(read=600, write=30)


def _fake_config(features=None, reasoning_extra=None):
    cfg = MagicMock()
    feats = {"openai_keepalive_timeout": False}
    if features:
        feats.update(features)
    params = {"features": feats}
    if reasoning_extra:
        params.update(reasoning_extra)
    cfg.reasoning_params = params
    return cfg


def test_keepalive_timeout_enabled_reads_flag():
    """讀 flag 值正確。startup-only freeze（lru_cache）：每次量測前 cache_clear 模擬重啟。"""
    keepalive_timeout_enabled.cache_clear()
    with patch("core.openai_http.CONFIG", _fake_config(features={"openai_keepalive_timeout": True})):
        assert keepalive_timeout_enabled() is True
    keepalive_timeout_enabled.cache_clear()
    with patch("core.openai_http.CONFIG", _fake_config(features={"openai_keepalive_timeout": False})):
        assert keepalive_timeout_enabled() is False
    keepalive_timeout_enabled.cache_clear()


def test_keepalive_timeout_enabled_frozen_at_startup():
    """startup-only freeze：第一次讀凍結，之後 CONFIG 改變不生效（需重啟=cache_clear）。"""
    keepalive_timeout_enabled.cache_clear()
    with patch("core.openai_http.CONFIG", _fake_config(features={"openai_keepalive_timeout": True})):
        assert keepalive_timeout_enabled() is True  # 凍結點
    # CONFIG 翻 OFF，但凍結值不變
    with patch("core.openai_http.CONFIG", _fake_config(features={"openai_keepalive_timeout": False})):
        assert keepalive_timeout_enabled() is True
    keepalive_timeout_enabled.cache_clear()


def test_config_numeric_getters_have_defaults():
    with patch("core.openai_http.CONFIG", _fake_config()):
        assert get_read_timeout() == 45    # default placeholder
        assert get_write_timeout() == 30
        assert get_max_retries() == 2
    with patch("core.openai_http.CONFIG", _fake_config(
            reasoning_extra={"openai_read_timeout": 30, "openai_write_timeout": 20, "openai_max_retries": 3})):
        assert get_read_timeout() == 30
        assert get_write_timeout() == 20
        assert get_max_retries() == 3
