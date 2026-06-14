"""
proxy_pool.py - 輕量級 Proxy Pool

免費 proxy 來源抓取、驗證、輪換機制。
用於 IP 被封鎖的爬蟲來源（如 einfo）。

設計：
- 全域 singleton，所有 engine 共享
- Lazy init：第一次 get_proxy() 時才初始化
- 定期從免費來源刷新，httpbin 驗證存活 + 匿名性
"""

import asyncio
import logging
import random
import ssl
import time
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup

from . import settings

logger = logging.getLogger("ProxyPool")

# Global singleton
_pool: Optional['ProxyPool'] = None
_pool_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """Get or create module-level lock (must be called within event loop)."""
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_proxy_pool() -> 'ProxyPool':
    """Get or create global ProxyPool singleton (lazy init)."""
    global _pool
    if _pool is not None:
        return _pool

    lock = _get_lock()
    async with lock:
        if _pool is None:
            pool = ProxyPool()
            await pool.initialize()
            _pool = pool
    return _pool


def remove_from_pool(proxy: str) -> None:
    """Remove a bad proxy from the global pool (sync, safe to call from anywhere)."""
    if _pool is not None:
        _pool.remove_proxy(proxy)


class ProxyPool:
    """輕量級免費 Proxy Pool。

    從 free-proxy-list.net / sslproxies.org 抓取 proxy list，
    用 httpbin.org/ip 驗證存活和匿名性，記憶體內管理，定期刷新。
    """

    SOURCES = [
        "https://free-proxy-list.net/",
        "https://www.sslproxies.org/",
    ]

    def __init__(self):
        self._proxies: List[str] = []
        self._last_refresh: float = 0
        self._lock = asyncio.Lock()
        self._my_ip: Optional[str] = None

    async def initialize(self) -> None:
        """首次填充 pool。"""
        await self._refresh()

    async def get_proxy(self) -> Optional[str]:
        """隨機取一個 proxy。Pool 空或過期時自動刷新。"""
        if not self._proxies or (time.time() - self._last_refresh > settings.PROXY_REFRESH_INTERVAL):
            await self._refresh()
        if not self._proxies:
            logger.warning("No proxies available in pool")
            return None
        return random.choice(self._proxies)

    def remove_proxy(self, proxy: str) -> None:
        """移除失敗的 proxy。"""
        try:
            self._proxies.remove(proxy)
            logger.info(f"Removed bad proxy {proxy}, {len(self._proxies)} remaining")
        except ValueError:
            pass

    def size(self) -> int:
        """目前可用 proxy 數量。"""
        return len(self._proxies)

    async def _refresh(self) -> None:
        """從免費來源抓取並驗證 proxy。"""
        async with self._lock:
            # Prevent refresh storms (min 60s between refreshes)
            if self._proxies and time.time() - self._last_refresh < 60:
                return

            logger.info("Refreshing proxy pool...")
            candidates = set()

            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)

            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                # Detect my real IP first
                if self._my_ip is None:
                    try:
                        async with session.get("https://httpbin.org/ip") as resp:
                            data = await resp.json()
                            self._my_ip = data.get("origin", "").split(",")[0].strip()
                            logger.info(f"My real IP: {self._my_ip}")
                    except Exception as e:
                        logger.warning(f"Failed to detect real IP: {e}")
                        self._my_ip = ""

                # Fetch from each source
                for source_url in self.SOURCES:
                    try:
                        proxies = await self._fetch_from_source(session, source_url)
                        candidates.update(proxies)
                        logger.info(f"Fetched {len(proxies)} candidates from {source_url}")
                    except Exception as e:
                        logger.warning(f"Failed to fetch from {source_url}: {e}")

                if not candidates:
                    logger.warning("No proxy candidates found from any source")
                    self._last_refresh = time.time()
                    return

                logger.info(f"Validating {len(candidates)} candidates...")
                validated = await self._validate_proxies(session, list(candidates))

            self._proxies = validated[:settings.PROXY_MAX_POOL_SIZE]
            self._last_refresh = time.time()
            logger.info(f"Proxy pool refreshed: {len(self._proxies)} validated proxies")

    async def _fetch_from_source(
        self, session: aiohttp.ClientSession, url: str
    ) -> List[str]:
        """從免費 proxy 網站抓取 proxy list (HTML table 解析)。"""
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP {resp.status} from {url}")
                return []
            html = await resp.text()

        soup = BeautifulSoup(html, 'html.parser')
        proxies = []

        # free-proxy-list.net and sslproxies.org use <table class="table ...">
        table = soup.find('table', class_='table')
        if not table:
            table = soup.find('table')

        if not table:
            logger.warning(f"No proxy table found in {url}")
            return []

        for row in table.find_all('tr')[1:]:  # Skip header row
            cols = row.find_all('td')
            if len(cols) >= 2:
                ip = cols[0].text.strip()
                port = cols[1].text.strip()
                if ip and port and port.isdigit():
                    proxies.append(f"http://{ip}:{port}")

        return proxies

    async def _validate_proxies(
        self, session: aiohttp.ClientSession, candidates: List[str]
    ) -> List[str]:
        """用 httpbin.org/ip (HTTPS) 驗證 proxy 可用性和匿名性。

        使用 HTTPS 驗證確保 proxy 支援 CONNECT tunneling，
        因為目標網站（如 einfo）使用 HTTPS。
        """
        validated = []
        timeout = settings.PROXY_VALIDATE_TIMEOUT

        async def _check(proxy: str) -> Optional[str]:
            try:
                async with session.get(
                    "https://httpbin.org/ip",
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        proxy_ip = data.get("origin", "").split(",")[0].strip()
                        # Anonymity check: proxy IP must differ from our real IP
                        if self._my_ip and proxy_ip and proxy_ip != self._my_ip:
                            return proxy
            except Exception:
                pass
            return None

        # Validate in parallel batches
        batch_size = 50
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            results = await asyncio.gather(*[_check(p) for p in batch])
            for r in results:
                if r:
                    validated.append(r)
                    if len(validated) >= settings.PROXY_MAX_POOL_SIZE:
                        return validated

        return validated
