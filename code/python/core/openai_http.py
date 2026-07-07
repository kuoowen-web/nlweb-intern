"""共用 OpenAI httpx client helper：TCP keepalive + 分項 timeout + config 讀取。

治 OpenAI gpt-5.1 Responses API 偶發 hang（Sentry 確診 NAT 靜默丟閒置連線 +
四層 timeout 疊床架屋）。keepalive 無條件套（零行為改變的純防護）；timeout 收斂
flag-gated（openai_keepalive_timeout，預設關）。
"""
import functools
import socket
from typing import Optional

import httpx

from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("openai_http")

# default placeholder 數值（待 CEO 校準，見 config_reasoning.yaml 註解）。
_DEFAULT_READ = 45
_DEFAULT_WRITE = 30
_DEFAULT_CONNECT = 5
_DEFAULT_POOL = 5
_DEFAULT_MAX_RETRIES = 2


def _keepalive_socket_options():
    """建 TCP keepalive socket options，平台特定鍵用 hasattr guard（不 silent fail）。

    Linux: SO_KEEPALIVE + TCP_KEEPIDLE/TCP_KEEPINTVL/TCP_KEEPCNT
    macOS: SO_KEEPALIVE + TCP_KEEPALIVE（= idle，等同 Linux TCP_KEEPIDLE）
    Windows: 只 SO_KEEPALIVE（無 per-socket idle 鍵，靠 OS 預設）
    """
    opts = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    # idle 秒數：NAT idle ~120s（GCP Cloud NAT），設 60 確保 keepalive 在被丟前先動。
    if hasattr(socket, "TCP_KEEPIDLE"):           # Linux
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60))
    elif hasattr(socket, "TCP_KEEPALIVE"):        # macOS（同義 idle）
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 60))
    else:
        logger.info("openai_http: no per-socket TCP keepidle option on this platform; "
                    "SO_KEEPALIVE only (OS default idle).")
    if hasattr(socket, "TCP_KEEPINTVL"):          # Linux/macOS
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 15))
    if hasattr(socket, "TCP_KEEPCNT"):            # Linux/macOS
        opts.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 4))
    return opts


def make_keepalive_async_client(timeout: Optional[httpx.Timeout] = None) -> httpx.AsyncClient:
    """建 keepalive-enabled httpx.AsyncClient（純加法，零既有行為改變）。

    timeout=None → 不設 timeout（SDK 會用它預設值；keepalive 仍生效）。
    """
    transport = httpx.AsyncHTTPTransport(socket_options=_keepalive_socket_options())
    if timeout is not None:
        return httpx.AsyncClient(transport=transport, timeout=timeout)
    return httpx.AsyncClient(transport=transport)


def make_sliced_timeout(read: float, write: float,
                        connect: float = _DEFAULT_CONNECT,
                        pool: float = _DEFAULT_POOL) -> httpx.Timeout:
    """建分項 httpx.Timeout。read 必須 < 600（否則 retry×600 爆炸，openai-python #809）。"""
    if read >= 600:
        raise ValueError(f"read timeout {read} >= 600s 會讓 SDK retry×read 爆炸；必須設短")
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


def _features() -> dict:
    return (CONFIG.reasoning_params or {}).get("features", {}) or {}


@functools.lru_cache(maxsize=1)
def keepalive_timeout_enabled() -> bool:
    """timeout 收斂 + 拆層 flag（預設關，**startup-only**）。

    啟動時讀一次後凍結（lru_cache）：運行中改 CONFIG 不生效，需重啟 server 才套新值。
    這是刻意的 startup-only 契約 —— 凍結後所有呼叫回同一個值，「client 建立時讀的 flag」與
    「call site 讀的 flag」永遠一致，不可能 mismatch（AR round-4 根解，取代 round-3 的
    fail-loud 偵測機制：那是在偵測動態多次讀造成的不一致，根因消除後不再需要）。

    keepalive 本身不受此 flag 影響、永遠套。

    ⚠ test 紀律：lru_cache 在 test 之間會污染凍結值，setup/teardown 須
    keepalive_timeout_enabled.cache_clear()。
    """
    return bool(_features().get("openai_keepalive_timeout", False))


def get_read_timeout() -> float:
    return float((CONFIG.reasoning_params or {}).get("openai_read_timeout", _DEFAULT_READ))


def get_write_timeout() -> float:
    return float((CONFIG.reasoning_params or {}).get("openai_write_timeout", _DEFAULT_WRITE))


def get_max_retries() -> int:
    return int((CONFIG.reasoning_params or {}).get("openai_max_retries", _DEFAULT_MAX_RETRIES))
