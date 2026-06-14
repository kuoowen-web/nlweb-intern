"""
engine.py - 通用爬蟲引擎

核心爬蟲引擎，負責：
- 全範圍掃描：run_full_scan(start_id, end_id) — 掃描完整 ID 範圍
- 自動爬取：run_auto(count) — 從最新 ID 往回爬
- 列表爬取：run_list_page() — 從列表頁爬取
- 併發控制、重試機制、去重機制
"""

import asyncio
import aiohttp
import calendar
import logging
import random
import re
import ssl
import time
from typing import Dict, List, Optional, Any, Set, Union, Callable
from datetime import datetime, timedelta
from enum import Enum

from charset_normalizer import from_bytes
from htmldate import find_date
import trafilatura

from . import settings
from .settings import DEFAULT_HEADERS
from .interfaces import BaseParser, SessionType
from .pipeline import Pipeline
from .crawled_registry import get_registry, CrawledRegistry


def _run_parse_in_thread(parser, html, url):
    """在 thread pool 中執行 parser.parse()，避免 BeautifulSoup 阻塞 event loop。

    所有 parser.parse() 都是 async def 但內部純 sync（BeautifulSoup 操作）。
    此函式透過手動 exhaust coroutine 來取得回傳值。
    """
    coro = parser.parse(html, url)
    try:
        coro.send(None)
        # Coroutine didn't raise StopIteration — should not happen
        logging.getLogger("CrawlerEngine").warning(
            f"Parser coroutine for {url} did not complete on first send"
        )
        return None
    except StopIteration as e:
        return e.value
    except Exception as e:
        logging.getLogger("CrawlerEngine").error(
            f"Parser exception for {url}: {e}", exc_info=True
        )
        return None
    finally:
        coro.close()


# ==================== Full Scan Configuration ====================
# 用於 run_full_scan() 的來源設定
# type: "sequential" (流水號 ID) 或 "date_based" (YYYYMMDDXXXX 格式)
# default_start_id: UI 預填起始 ID（僅供參考，不作 fallback）
# max_suffix: 日期型 ID 每天的初始上限（會被 per-day adaptive scanning 自動擴展）

FULL_SCAN_CONFIG = {
    "udn":  {"type": "sequential", "default_start_id": 7_800_000},
    "ltn":  {"type": "sequential", "default_start_id": 4_550_000},
    "einfo": {"type": "sequential", "default_start_id": 230_000},
    "cna":  {"type": "date_based", "max_suffix": 6000},
    "esg_businesstoday": {"type": "date_based", "max_suffix": 600, "date_scan_miss_limit": 150},
    "chinatimes": {"type": "date_based", "max_suffix": 6000, "suffix_digits": 6, "date_scan_miss_limit": 700},
    "moea": {"type": "sequential", "default_start_id": 110_000},
}

# 嘗試引入 curl_cffi
try:
    from curl_cffi.requests import AsyncSession as CurlSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    CurlSession = None


class CrawlStatus(Enum):
    """爬取狀態列舉"""
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    BLOCKED = "BLOCKED"
    FETCH_ERROR = "FETCH_ERROR"  # Timeout/network error (not 403/429 block)


class CrawlerEngine:
    """
    通用爬蟲引擎

    設計原則：
    1. 依賴注入：透過 BaseParser 介面與具體網站解耦
    2. 關注點分離：只負責爬取流程，解析邏輯委託給 Parser
    3. 可重用：適用於所有實作 BaseParser 的網站
    """

    def __init__(
        self,
        parser: BaseParser,
        session: Optional[Union[aiohttp.ClientSession, 'CurlSession']] = None,
        auto_save: bool = True,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        chunk_size: int = 0,
        chunk_by_month: bool = False,
        task_id: Optional[str] = None,
        stop_check: Optional[Callable[[], bool]] = None
    ):
        """
        初始化爬蟲引擎

        Args:
            parser: BaseParser 實例（必須）
            session: HTTP Session 實例（可選）
            auto_save: 是否自動儲存爬取結果（預設 True）
            progress_callback: 進度回調函數，接收 stats dict
            chunk_size: 每個檔案的最大文章數（0 表示不限制）
            chunk_by_month: 是否按文章發布月份分檔
        """
        self.parser = parser
        self.session = session
        self.auto_save = auto_save
        self.progress_callback = progress_callback
        self.chunk_size = chunk_size
        self.chunk_by_month = chunk_by_month
        self.task_id = task_id
        self.stop_check = stop_check

        # 載入來源專屬設定
        self._load_source_config()

        # 請求 timeout（可被 full scan override 覆蓋）
        self.request_timeout = settings.REQUEST_TIMEOUT

        # 判斷 Session 類型
        if session is not None:
            if CURL_CFFI_AVAILABLE and isinstance(session, CurlSession):
                self.session_type = SessionType.CURL_CFFI
            else:
                self.session_type = SessionType.AIOHTTP
        else:
            if parser.source_name in settings.CURL_CFFI_SOURCES:
                self.session_type = SessionType.CURL_CFFI
            else:
                self.session_type = SessionType.AIOHTTP

        # Proxy mode for IP-blocked sources
        self._use_proxy = parser.source_name in settings.PROXY_SOURCES

        # 設定日誌
        self.logger = logging.getLogger(f"CrawlerEngine_{parser.source_name}")
        self._setup_logger()

        self.logger.info(f"Engine initialized with session type: {self.session_type.value}")
        self.logger.info(f"   Concurrent limit: {self.concurrent_limit}")
        self.logger.info(f"   Delay range: {self.min_delay:.1f}s - {self.max_delay:.1f}s")
        if self._use_proxy:
            self.logger.info(f"   Proxy mode: ENABLED")
        if chunk_size > 0:
            self.logger.info(f"   Chunk size: {chunk_size} articles per file")
        if chunk_by_month:
            self.logger.info(f"   Chunk by month: enabled")

        # 初始化 Pipeline
        if self.auto_save:
            self.pipeline = Pipeline(
                source_name=parser.source_name,
                chunk_size=chunk_size,
                chunk_by_month=chunk_by_month
            )

        # 初始化 SQLite Registry
        self.registry: CrawledRegistry = get_registry()

        # 載入歷史記錄（去重）- URL 查詢委託給 SQLite，僅保留數字 ID 快取
        self.crawled_numeric_ids: Set[int] = set()  # 數字 ID 去重（跨 URL pattern）
        self._load_history()

        # 統計資訊
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'not_found': 0,
            'blocked': 0,
        }

        # Full scan mode flags
        self._full_scan_mode = False
        self._max_candidate_urls = None  # None = unlimited
        self._not_found_ids: Set[int] = set()  # Known 404 article IDs (loaded for full scan)
        self._blocked_ids: Set[int] = set()  # IDs that failed with 429 (need retry, not skip)
        self._blocked_dates: Set[str] = set()  # Dates (YYYY-MM-DD) with blocked URLs (date-based)
        self._watermark_id: Optional[int] = None   # Previous scan progress (sequential)
        self._watermark_date: Optional[str] = None  # Previous scan progress (date-based)

        # 智能跳躍狀態
        self.consecutive_failures = 0
        self.smart_jump_count = 0

        # 429 降速狀態
        self.rate_limit_hit = False
        self.rate_limit_cooldown_until = 0

        # 進度更新節流（避免太頻繁）
        self._last_progress_update = 0
        self._progress_update_interval = 1.0  # 最多每秒更新一次

        # Response latency tracking (rolling window for AutoThrottle)
        self._latencies: list[float] = []
        self._latency_window = 50  # keep last N latency samples
        self._avg_latency: float = 0.0
        self._current_delay: float = (self.min_delay + self.max_delay) / 2.0

    def _load_source_config(self) -> None:
        """載入來源專屬設定"""
        source_name = self.parser.source_name

        if source_name in settings.NEWS_SOURCES:
            source_config = settings.NEWS_SOURCES[source_name]
            self.concurrent_limit = source_config.get(
                'concurrent_limit', settings.CONCURRENT_REQUESTS
            )
            delay_range = source_config.get(
                'delay_range', (settings.MIN_DELAY, settings.MAX_DELAY)
            )
            self.min_delay, self.max_delay = delay_range
            # Per-source blocked tolerance (for sources with frequent 429)
            self._source_blocked_limit = source_config.get('blocked_limit')
            self._source_blocked_cooldown = source_config.get('blocked_cooldown')
            self._source_rate_limit_cooldown = source_config.get('rate_limit_cooldown')
        else:
            self.concurrent_limit = settings.CONCURRENT_REQUESTS
            self.min_delay = settings.MIN_DELAY
            self.max_delay = settings.MAX_DELAY
            self._source_blocked_limit = None
            self._source_blocked_cooldown = None
            self._source_rate_limit_cooldown = None

    def _setup_logger(self) -> None:
        """設置日誌處理器"""
        if self.logger.handlers:
            return

        # 防止日誌重複：不傳播到 root logger
        self.logger.propagate = False

        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

        log_file = settings.LOG_DIR / f"engine_{self.parser.source_name}_{time.strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        console_handler = logging.StreamHandler()

        formatter = logging.Formatter(
            settings.LOG_FORMAT,
            datefmt=settings.LOG_DATE_FORMAT
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.setLevel(settings.LOG_LEVEL)

    def _load_history(self) -> int:
        """
        載入歷史已爬取的 URL 記錄。

        優先使用 SQLite Registry，若發現舊的 txt 檔案則自動遷移。
        URL 查詢委託給 SQLite（on-demand），僅將數字 ID 載入記憶體快取。
        """
        try:
            source_name = self.parser.source_name

            # Check for old txt file and migrate if exists
            old_txt_file = settings.CRAWLED_IDS_DIR / f"{source_name}.txt"
            if old_txt_file.exists():
                migrated = self.registry.migrate_from_txt(source_name, old_txt_file)
                if migrated > 0:
                    self.logger.info(f"Migrated {migrated:,} URLs from txt to SQLite")
                    # Rename old file to .txt.bak
                    backup_path = old_txt_file.with_suffix('.txt.bak')
                    old_txt_file.rename(backup_path)
                    self.logger.info(f"Renamed old file to {backup_path.name}")

            # Load URLs from SQLite to build numeric ID index only (not kept in memory)
            all_urls = self.registry.load_urls_for_source(source_name)

            # Build numeric ID set for cross-URL-pattern dedup
            # (e.g., chinatimes article crawled as realtimenews/XXX-260405
            #  should be detected when full_scan checks realtimenews/XXX-260402)
            numeric_count = 0
            for url in all_urls:
                nid = self.parser.extract_id_from_url(url)
                if nid is not None:
                    self.crawled_numeric_ids.add(nid)
                    numeric_count += 1

            count = len(all_urls)
            self.logger.info(f"Loaded {count:,} crawled URLs from SQLite registry (on-demand lookup)")
            if numeric_count > 0:
                unique_ids = len(self.crawled_numeric_ids)
                self.logger.info(f"  Numeric ID index: {unique_ids:,} unique IDs (from {numeric_count:,} URLs)")
            return count

        except Exception as e:
            self.logger.error(f"Error loading history: {str(e)}")
            return 0

    def _is_crawled(self, url: str) -> bool:
        """檢查 URL 是否已爬取（on-demand SQLite query）"""
        return self.registry.is_crawled(url)

    def _is_any_url_crawled(self, article_id: int) -> bool:
        """Check if article has been crawled under any URL variant.

        Uses numeric ID dedup first (fast, catches cross-URL-pattern duplicates),
        then falls back to exact URL matching.
        """
        # Fast path: check by numeric ID (catches all URL variants)
        if article_id in self.crawled_numeric_ids:
            return True
        # Fallback: exact URL matching (for sources without numeric ID extraction)
        primary = self.parser.get_url(article_id)
        if self._is_crawled(primary):
            return True
        for url in self.parser.get_candidate_urls(article_id):
            if self._is_crawled(url):
                return True
        return False

    def _mark_as_crawled(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        標記 URL 為已爬取，更新 SQLite Registry 和數字 ID 快取。

        Args:
            url: 文章 URL
            data: 文章解析資料（包含 datePublished, dateModified, articleBody 等）
        """
        # Update numeric ID index (in-memory cache for cross-URL dedup)
        nid = self.parser.extract_id_from_url(url)
        if nid is not None:
            self.crawled_numeric_ids.add(nid)

        self.registry.mark_crawled(
            url=url,
            source_id=self.parser.source_name,
            date_published=data.get('datePublished') if data else None,
            date_modified=data.get('dateModified') if data else None,
            content=data.get('articleBody', '') if data else None,
            task_id=self.task_id,
        )

    async def _create_session(self) -> Union[aiohttp.ClientSession, 'CurlSession']:
        """創建 Session"""
        if self.session_type == SessionType.CURL_CFFI:
            if not CURL_CFFI_AVAILABLE:
                raise RuntimeError(
                    f"curl_cffi is required for {self.parser.source_name} but not installed. "
                    f"Install it with: pip install curl_cffi"
                )
            self.logger.info("Creating curl_cffi session")
            return CurlSession(
                headers=DEFAULT_HEADERS,
                timeout=self.request_timeout,
                impersonate="chrome110"
            )

        self.logger.info("Creating aiohttp session")
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        connector = aiohttp.TCPConnector(ssl=ssl_context)

        return aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self.request_timeout),
            headers=DEFAULT_HEADERS
        )

    def _get_headers(self) -> Dict[str, str]:
        """獲取請求標頭（支援動態 User-Agent 輪換）"""
        headers = DEFAULT_HEADERS.copy()
        headers['User-Agent'] = random.choice(settings.USER_AGENTS)
        return headers

    def _record_latency(self, latency: float) -> None:
        """Record a request latency sample and update rolling average."""
        self._latencies.append(latency)
        if len(self._latencies) > self._latency_window:
            self._latencies = self._latencies[-self._latency_window:]
        self._avg_latency = sum(self._latencies) / len(self._latencies)

    async def _handle_rate_limit(self) -> None:
        """處理 429 Rate Limit 錯誤"""
        self.rate_limit_hit = True
        cooldown = self._source_rate_limit_cooldown or settings.RATE_LIMIT_COOLDOWN

        self.logger.warning(f"Rate limit detected (429), cooling down for {cooldown}s...")
        self.rate_limit_cooldown_until = time.time() + cooldown
        self._throttle_backoff()

        await asyncio.sleep(cooldown)

        self.rate_limit_hit = False
        self.logger.info(f"Cooldown completed, resuming...")

    async def _get_proxy(self) -> Optional[str]:
        """Get a proxy from the global ProxyPool (lazy init on first call)."""
        from .proxy_pool import get_proxy_pool
        pool = await get_proxy_pool()
        return await pool.get_proxy()

    def _remove_bad_proxy(self, proxy_url: str) -> None:
        """Remove a failed proxy from the global pool."""
        from .proxy_pool import remove_from_pool
        remove_from_pool(proxy_url)

    async def _fetch(
        self,
        url: str,
        session: Union[aiohttp.ClientSession, 'CurlSession']
    ) -> tuple[Optional[str], CrawlStatus]:
        """獲取 URL 內容，包含重試機制"""
        if self.rate_limit_hit:
            wait_time = self.rate_limit_cooldown_until - time.time()
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        retry_count = 0
        max_retries = settings.PROXY_MAX_RETRIES if self._use_proxy else settings.MAX_RETRIES
        got_blocked_response = False

        while retry_count <= max_retries:
            proxy_url = None
            proxy_failed = False
            try:
                headers = self._get_headers()
                t0 = time.monotonic()

                # Per-request proxy for IP-blocked sources
                if self._use_proxy:
                    proxy_url = await self._get_proxy()
                proxy_kw = {'proxy': proxy_url} if proxy_url else {}

                if self.session_type == SessionType.CURL_CFFI:
                    response = await session.get(url, headers=headers, **proxy_kw)
                    self._record_latency(time.monotonic() - t0)
                    status = response.status_code

                    if status == 200:
                        # 偵測靜默 redirect（如 ESG BT 不存在的文章 301→首頁）
                        final_url = str(getattr(response, 'url', url))
                        if final_url != url and hasattr(self.parser, 'is_not_found_redirect'):
                            if self.parser.is_not_found_redirect(url, final_url):
                                return (None, CrawlStatus.NOT_FOUND)
                        # charset_normalizer 自動偵測編碼（取代 response.text 避免 Big5/cp950 炸）
                        detected = from_bytes(response.content).best()
                        if detected is not None:
                            text = str(detected)
                        else:
                            # Try Big5 for Traditional Chinese sites
                            try:
                                response.content.decode('big5')
                                text = response.content.decode('big5')
                            except (UnicodeDecodeError, LookupError):
                                text = response.content.decode('utf-8', errors='replace')
                        return (text, CrawlStatus.SUCCESS)
                    elif status == 404:
                        return (None, CrawlStatus.NOT_FOUND)
                    elif status == 403:
                        proxy_failed = True  # IP banned, remove proxy
                        got_blocked_response = True
                        await self._handle_rate_limit()
                    elif status == 429:
                        # 429 = rate limit, proxy is fine — don't remove it
                        got_blocked_response = True
                        await self._handle_rate_limit()
                    elif status in (500, 502, 503, 504):
                        proxy_failed = True  # 繼續重試
                    else:
                        proxy_failed = True
                        return (None, CrawlStatus.BLOCKED)
                else:
                    async with session.get(
                        url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                        **proxy_kw
                    ) as response:
                        self._record_latency(time.monotonic() - t0)
                        if response.status == 200:
                            # 偵測靜默 redirect（如 ESG BT 不存在的文章 301→首頁）
                            final_url = str(response.url)
                            if final_url != url and hasattr(self.parser, 'is_not_found_redirect'):
                                if self.parser.is_not_found_redirect(url, final_url):
                                    return (None, CrawlStatus.NOT_FOUND)
                            raw = await response.read()
                            detected = from_bytes(raw).best()
                            if detected is not None:
                                text = str(detected)
                            else:
                                # Try Big5 for Traditional Chinese sites
                                try:
                                    raw.decode('big5')
                                    text = raw.decode('big5')
                                except (UnicodeDecodeError, LookupError):
                                    text = raw.decode('utf-8', errors='replace')
                            return (text, CrawlStatus.SUCCESS)
                        elif response.status == 404:
                            return (None, CrawlStatus.NOT_FOUND)
                        elif response.status == 403:
                            proxy_failed = True  # IP banned, remove proxy
                            got_blocked_response = True
                            await self._handle_rate_limit()
                        elif response.status == 429:
                            # 429 = rate limit, proxy is fine — don't remove it
                            got_blocked_response = True
                            await self._handle_rate_limit()
                        elif response.status in (500, 502, 503, 504):
                            proxy_failed = True  # 繼續重試
                        else:
                            proxy_failed = True
                            return (None, CrawlStatus.BLOCKED)

            except asyncio.TimeoutError:
                proxy_failed = True
                self.logger.debug(f"Timeout for {url} (retry {retry_count}/{max_retries})")

            except Exception as e:
                proxy_failed = True
                self.logger.debug(f"Network error fetching {url}: {str(e)}")

            finally:
                if proxy_url and proxy_failed:
                    self._remove_bad_proxy(proxy_url)

            retry_count += 1
            if retry_count <= max_retries:
                wait_time = settings.RETRY_DELAY * (2 ** (retry_count - 1))
                await asyncio.sleep(min(wait_time, settings.MAX_RETRY_DELAY))

        # Distinguish real blocks (403/429) from timeouts/network errors
        if got_blocked_response:
            return (None, CrawlStatus.BLOCKED)
        return (None, CrawlStatus.FETCH_ERROR)

    async def _random_delay(self):
        """隨機延遲（固定模式，AutoThrottle 關閉時使用）"""
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))

    async def _adaptive_delay(self):
        """AutoThrottle: adaptive delay based on server response latency.

        Uses EWMA smoothing: new_delay = (old_delay + target_delay) / 2
        where target_delay = avg_latency / TARGET_CONCURRENCY.
        Falls back to random delay when AutoThrottle is disabled or no samples yet.
        """
        if not settings.AUTOTHROTTLE_ENABLED or not self._latencies:
            await self._random_delay()
            return

        target_delay = self._avg_latency / settings.AUTOTHROTTLE_TARGET_CONCURRENCY
        new_delay = (self._current_delay + target_delay) / 2.0
        # Clamp: respect min_delay, but allow exceeding max_delay if backoff pushed it higher
        effective_max = max(self.max_delay, self._current_delay)
        new_delay = max(self.min_delay, min(new_delay, effective_max))
        self._current_delay = new_delay

        # Add small jitter (±10%) to avoid synchronized bursts
        jitter = new_delay * 0.1
        actual = new_delay + random.uniform(-jitter, jitter)
        actual = max(self.min_delay, actual)

        await asyncio.sleep(actual)

    def _throttle_backoff(self):
        """Increase current delay on error (403/429/5xx) responses.

        Allows delay to exceed max_delay (up to 4x) on repeated 429s.
        The normal max_delay cap prevents adaptive throttle from backing off
        enough for aggressive rate limiters (e.g. MOEA 429s at 4s delay).
        """
        backoff_ceiling = self.max_delay * 4.0
        self._current_delay = min(self._current_delay * 2.0, backoff_ceiling)

    async def _process_article(
        self,
        article_id: int,
        session: Union[aiohttp.ClientSession, 'CurlSession']
    ) -> CrawlStatus:
        """處理單篇文章"""
        url = self.parser.get_url(article_id)

        if not url:
            self.stats['not_found'] += 1
            await self._report_progress()
            return CrawlStatus.NOT_FOUND

        if self._is_any_url_crawled(article_id):
            self.stats['skipped'] += 1
            await self._report_progress()
            return CrawlStatus.SUCCESS

        html, status = await self._fetch(url, session)

        if status == CrawlStatus.NOT_FOUND:
            # Primary URL 404 — try candidate URLs before giving up
            candidate_urls = self.parser.get_candidate_urls(article_id)
            if self._max_candidate_urls is not None:
                candidate_urls = candidate_urls[:self._max_candidate_urls]
            if candidate_urls:
                for candidate_url in candidate_urls:
                    if self._is_crawled(candidate_url):
                        self.stats['skipped'] += 1
                        await self._report_progress()
                        return CrawlStatus.SUCCESS

                    c_html, c_status = await self._fetch(candidate_url, session)
                    if c_status in (CrawlStatus.NOT_FOUND, CrawlStatus.BLOCKED, CrawlStatus.FETCH_ERROR) or c_html is None:
                        continue

                    try:
                        loop = asyncio.get_running_loop()
                        c_data = await loop.run_in_executor(
                            None, _run_parse_in_thread, self.parser, c_html, candidate_url
                        )
                        if c_data is not None:
                            c_data = self._ensure_date(c_data, c_html, candidate_url)
                            if c_data is not None:
                                self.logger.info(f"ID {article_id:,} found via candidate URL (404 fallback): {candidate_url}")
                                return await self._handle_successful_parse(article_id, candidate_url, c_data)
                    except Exception as e:
                        self.logger.debug(f"Error parsing candidate {candidate_url}: {e}")

            self.stats['not_found'] += 1
            self._not_found_ids.add(article_id)
            self.registry.mark_not_found(self.parser.source_name, article_id)
            await self._report_progress()
            return CrawlStatus.NOT_FOUND

        if status == CrawlStatus.BLOCKED:
            self.stats['blocked'] += 1
            self._mark_failed(url, "blocked", "Request blocked (403/429)")
            await self._report_progress()
            return CrawlStatus.BLOCKED

        if status == CrawlStatus.FETCH_ERROR:
            self.stats['failed'] += 1
            self._mark_failed(url, "fetch_error", "Timeout/network error after retries")
            await self._report_progress()
            return CrawlStatus.FETCH_ERROR

        if html is None:
            self.stats['failed'] += 1
            self._mark_failed(url, "fetch_error", "Failed to fetch HTML")
            await self._report_progress()
            return CrawlStatus.FETCH_ERROR

        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(
                None, _run_parse_in_thread, self.parser, html, url
            )
            if data is not None:
                data = self._ensure_date(data, html, url)
                if data is not None:
                    return await self._handle_successful_parse(article_id, url, data)

            # Custom parser failed — try trafilatura fallback before candidate URLs
            tf_data = await loop.run_in_executor(
                None, self._trafilatura_fallback, html, url
            )
            if tf_data is not None:
                tf_data = self._ensure_date(tf_data, html, url)
                if tf_data is not None:
                    self.stats['trafilatura_fallbacks'] = self.stats.get('trafilatura_fallbacks', 0) + 1
                    self.logger.info(f"ID {article_id:,} rescued by trafilatura fallback")
                    return await self._handle_successful_parse(article_id, url, tf_data)

            # Primary URL parse failed — try candidate URLs
            candidate_urls = self.parser.get_candidate_urls(article_id)
            if self._max_candidate_urls is not None:
                candidate_urls = candidate_urls[:self._max_candidate_urls]
            for candidate_url in candidate_urls:
                if self._is_crawled(candidate_url):
                    self.stats['skipped'] += 1
                    await self._report_progress()
                    return CrawlStatus.SUCCESS

                c_html, c_status = await self._fetch(candidate_url, session)
                if c_status != CrawlStatus.SUCCESS or c_html is None:
                    continue

                c_data = await loop.run_in_executor(
                    None, _run_parse_in_thread, self.parser, c_html, candidate_url
                )
                if c_data is not None:
                    c_data = self._ensure_date(c_data, c_html, candidate_url)
                    if c_data is not None:
                        self.logger.info(f"ID {article_id:,} found via candidate URL: {candidate_url}")
                        return await self._handle_successful_parse(article_id, candidate_url, c_data)

            # All candidates failed
            self.stats['failed'] += 1
            self._mark_failed(url, "parse_error", "Parser returned None (all URL variants tried)")
            await self._report_progress()
            return CrawlStatus.NOT_FOUND

        except Exception as e:
            self.logger.error(f"Error parsing {url}: {str(e)}")
            self.stats['failed'] += 1
            self._mark_failed(url, "parse_exception", str(e)[:200])
            await self._report_progress()
            return CrawlStatus.FETCH_ERROR

    def _ensure_date(self, data: Dict[str, Any], html: str, url: str = "") -> Optional[Dict[str, Any]]:
        """Ensure article has a datePublished; use htmldate as fallback.

        Returns the data dict (possibly with datePublished filled in),
        or None if no date could be determined (article should be discarded).
        """
        if data.get('datePublished'):
            return data
        try:
            hd = find_date(html, outputformat='%Y-%m-%d')
        except Exception as e:
            self.logger.debug(f"htmldate error: {e}")
            hd = None
        if hd:
            data['datePublished'] = f"{hd}T00:00:00"
            self.logger.info(f"htmldate fallback filled datePublished: {hd}")
            return data
        self.logger.warning(f"No date found (parser + htmldate), discarding: {url or data.get('url', '?')}")
        return None

    def _trafilatura_fallback(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """Last-resort extraction using trafilatura when custom parser returns None.

        This is a sync method; call via run_in_executor from async context.
        Returns a standard NewsArticle dict or None.
        """
        try:
            result = trafilatura.bare_extraction(
                html, url=url, favor_precision=True, include_comments=False
            )
        except Exception as e:
            self.logger.debug(f"trafilatura fallback error for {url}: {e}")
            return None

        if not result:
            return None

        # trafilatura 2.0+ returns Document object; convert to dict
        if hasattr(result, 'as_dict'):
            result = result.as_dict()

        title = result.get('title')
        body = result.get('text')
        if not title or not body or len(body) < settings.MIN_ARTICLE_LENGTH:
            return None

        # Assemble standard NewsArticle dict
        date_str = result.get('date')  # YYYY-MM-DD format from trafilatura
        date_published = f"{date_str}T00:00:00" if date_str else None

        data = {
            '@context': 'https://schema.org',
            '@type': 'NewsArticle',
            'headline': title,
            'articleBody': body[:settings.MAX_ARTICLE_LENGTH],
            'url': url,
            '_source': 'trafilatura_fallback',
        }
        if date_published:
            data['datePublished'] = date_published
        author = result.get('author')
        if author:
            data['author'] = {'@type': 'Person', 'name': author}

        return data

    async def _handle_successful_parse(
        self,
        article_id: int,
        url: str,
        data: Dict[str, Any]
    ) -> CrawlStatus:
        """Handle a successfully parsed article (shared by primary and candidate URL paths)."""
        if self.auto_save:
            success = await self.pipeline.process_and_save(url, data)
            if not success:
                self._mark_failed(url, "save_error", "Pipeline save failed")
                await self._report_progress()
                return CrawlStatus.FETCH_ERROR

        # Mark as crawled (after successful save, or immediately if no auto-save)
        self._mark_as_crawled(url, data)
        self.registry.remove_failed(url)
        self.logger.info(f"Parsed ID: {article_id:,}")
        self.stats['success'] += 1

        await self._report_progress()
        return CrawlStatus.SUCCESS

    def _mark_failed(self, url: str, error_type: str, error_message: str) -> None:
        """Record a failed URL in the registry."""
        try:
            self.registry.mark_failed(
                url=url,
                source_id=self.parser.source_name,
                error_type=error_type,
                error_message=error_message
            )
        except Exception as e:
            self.logger.warning(f"Failed to record failed URL: {e}")

    async def _report_progress(self) -> None:
        """Report progress via callback (throttled to avoid too frequent updates)."""
        if self.progress_callback is None:
            return

        now = time.time()
        if now - self._last_progress_update < self._progress_update_interval:
            return

        self._last_progress_update = now
        try:
            stats = self.stats.copy()
            stats['avg_latency'] = round(self._avg_latency, 3)
            stats['current_delay'] = round(self._current_delay, 3)
            self.progress_callback(stats)
        except Exception as e:
            self.logger.warning(f"Progress callback error: {e}")

        # Check external stop signal (used by subprocess runner)
        if self.stop_check and self.stop_check():
            self.logger.info("Stop signal detected via stop_check, cancelling...")
            raise asyncio.CancelledError("Stop signal received")

        # Yield to event loop so pending broadcast tasks (from asyncio.create_task
        # in the callback) can actually execute. Without this, tight loops that skip
        # already-crawled articles never yield, blocking all WebSocket broadcasts
        # until the loop ends.
        await asyncio.sleep(0)

    def _reset_stats(self, total: int = 0, **extra) -> None:
        """Reset crawl statistics to zero."""
        self.stats = {
            'total': total,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'not_found': 0,
            'blocked': 0,
            **extra,
        }

    def _evaluate_batch_results(
        self,
        batch_ids: List[int],
        results: list,
    ) -> tuple[int, int]:
        """
        Evaluate batch results and update consecutive_failures counter.

        Returns:
            (hit_count, miss_count) for per-day adaptive scanning.
        """
        hit_count = 0
        miss_count = 0
        for aid, result in zip(batch_ids, results):
            if isinstance(result, BaseException):
                self.logger.warning(f"Exception processing ID {aid}: {result}")
                miss_count += 1
                continue

            _, status = result

            if status == CrawlStatus.SUCCESS:
                self.consecutive_failures = 0
                hit_count += 1
            elif status == CrawlStatus.NOT_FOUND:
                self.consecutive_failures = 0
                miss_count += 1
            elif status == CrawlStatus.BLOCKED:
                self.consecutive_failures += 1
                miss_count += 1
            elif status == CrawlStatus.FETCH_ERROR:
                # Timeout/network error — don't count as blocked
                self.stats['failed'] = self.stats.get('failed', 0) + 1
                miss_count += 1
        return hit_count, miss_count

    def _get_blocked_limit(self) -> int:
        """Get blocked consecutive limit: full_scan > per-source > global default."""
        if self._full_scan_mode:
            return settings.FULL_SCAN_BLOCKED_LIMIT
        if self._source_blocked_limit is not None:
            return self._source_blocked_limit
        return settings.BLOCKED_CONSECUTIVE_LIMIT

    def _get_blocked_cooldown(self) -> float:
        """Get blocked cooldown: full_scan > per-source > global default."""
        if self._full_scan_mode:
            return settings.FULL_SCAN_BLOCKED_COOLDOWN
        if self._source_blocked_cooldown is not None:
            return self._source_blocked_cooldown
        return settings.BLOCKED_COOLDOWN

    def _check_blocked_stop(self) -> bool:
        """Check if we should stop due to consecutive blocked requests. Returns True if stopped."""
        limit = self._get_blocked_limit()
        if self.consecutive_failures >= limit:
            self.logger.warning(f"Stopping: {self.consecutive_failures} consecutive blocked requests (limit={limit})")
            self.stats['early_stopped'] = True
            self.stats['early_stop_reason'] = f"連續 {self.consecutive_failures} 次請求被封鎖"
            return True
        return False

    async def run_auto(
        self,
        count: int = 100,
        stop_after_consecutive_skips: int = settings.AUTO_DEFAULT_STOP_AFTER_SKIPS,
        date_floor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        自動爬取最新文章，連續遇到已爬取的文章時自動停止。
        使用批次並行處理提升效率。

        Args:
            count: 最大爬取數量（上限）
            stop_after_consecutive_skips: 連續遇到幾個已爬取的文章後停止（預設 10）
            date_floor: 日期下界（"YYYY-MM" 格式），ID 低於此日期對應的 ID 時自動停止。
                        防止爬蟲無限向後爬取歷史資料。

        Returns:
            爬取結果統計
        """
        self.logger.info(f"Starting auto crawl: max {count} articles, stop after {stop_after_consecutive_skips} consecutive skips")
        if date_floor:
            self.logger.info(f"Date floor: {date_floor}")

        # 創建會話（提前建立以供 get_latest_id 使用）
        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            latest_id = await self.parser.get_latest_id(session=self.session)
            if latest_id is None:
                self.logger.error("Failed to get latest ID")
                return {'error': 'Failed to get latest ID'}

            self.logger.info(f"Latest ID: {latest_id:,}")

            # 計算日期下界對應的 ID（支援所有日期型 ID 來源）
            floor_id = None
            if date_floor:
                floor_date = self._parse_date_input(date_floor)
                if floor_date:
                    source_name = self.parser.source_name
                    scan_config = FULL_SCAN_CONFIG.get(source_name, {})

                    if scan_config.get("type") == "date_based":
                        suffix_digits = scan_config.get("suffix_digits", 4)
                        floor_id = int(floor_date.strftime('%Y%m%d') + '0' * suffix_digits)

                    if floor_id:
                        self.logger.info(f"Date floor ID: {floor_id:,} (articles below this ID will trigger stop)")

            # 重置統計
            self._reset_stats(early_stopped=False, early_stop_reason=None)

            batch_size = max(self.concurrent_limit * 5, 20)
            semaphore = asyncio.Semaphore(self.concurrent_limit)

            self.logger.info(f"Parallel auto: batch_size={batch_size}, concurrent_limit={self.concurrent_limit}")

            async def _process_one(aid: int) -> tuple:
                async with semaphore:
                    await self._adaptive_delay()
                    status = await self._process_article(aid, self.session)
                    return (aid, status)

            consecutive_skips = 0
            current_id = latest_id
            processed = 0

            while processed < count:
                # Phase 1: Collect batch, pre-filtering already-crawled IDs
                batch_ids = []
                should_stop = False

                while len(batch_ids) < batch_size and processed < count:
                    self.stats['total'] += 1

                    if self._is_any_url_crawled(current_id):
                        self.stats['skipped'] += 1
                        consecutive_skips += 1

                        if consecutive_skips >= stop_after_consecutive_skips:
                            self.logger.info(f"Stopping: {consecutive_skips} consecutive skips reached")
                            self.stats['early_stopped'] = True
                            self.stats['early_stop_reason'] = f"連續 {consecutive_skips} 篇已爬取，自動停止"
                            should_stop = True
                            break
                    else:
                        consecutive_skips = 0
                        batch_ids.append(current_id)

                    processed += 1
                    current_id -= 1

                    # 日期下界檢查
                    if floor_id and current_id < floor_id:
                        self.logger.info(f"Stopping: reached date floor (ID {current_id:,} < floor {floor_id:,})")
                        self.stats['early_stopped'] = True
                        self.stats['early_stop_reason'] = f"已達日期下界 {date_floor}（ID {floor_id:,}）"
                        should_stop = True
                        break

                await self._report_progress()

                if should_stop and not batch_ids:
                    break

                # Phase 2: Process batch in parallel
                if batch_ids:
                    tasks = [_process_one(aid) for aid in batch_ids]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    self._evaluate_batch_results(batch_ids, results)
                    await self._report_progress()

                    if self._check_blocked_stop():
                        break

                    # Cooldown when blocked responses detected
                    if self.consecutive_failures > 0:
                        cooldown = self._get_blocked_cooldown()
                        limit = self._get_blocked_limit()
                        self.logger.warning(
                            f"Blocked: {self.consecutive_failures}/{limit}, "
                            f"cooling down {cooldown}s"
                        )
                        await asyncio.sleep(cooldown)

                if should_stop:
                    break

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    def _apply_full_scan_overrides(self) -> None:
        """套用 full scan 專用設定（更高併發、更短 delay）+ 載入 skip 資料。"""
        source_name = self.parser.source_name

        # Always load skip data (watermark + known 404s) regardless of overrides
        self._not_found_ids = self.registry.load_not_found_ids(source_name)
        self._blocked_ids = self.registry.load_blocked_ids(source_name)
        self._blocked_dates = self.registry.load_blocked_dates(source_name)
        wm = self.registry.get_scan_watermark(source_name)
        self._watermark_id = wm['last_scanned_id'] if wm and wm.get('last_scanned_id') else None
        self._watermark_date = wm['last_scanned_date'] if wm and wm.get('last_scanned_date') else None
        self._full_scan_mode = True

        self.logger.info(
            f"Full scan skip data: "
            f"known_404s={len(self._not_found_ids):,}, "
            f"blocked_ids={len(self._blocked_ids):,}, "
            f"blocked_dates={len(self._blocked_dates):,}, "
            f"watermark_id={self._watermark_id}, watermark_date={self._watermark_date}"
        )

        overrides = settings.FULL_SCAN_OVERRIDES.get(source_name)
        if not overrides:
            return

        old = {
            'concurrent_limit': self.concurrent_limit,
            'delay_range': (self.min_delay, self.max_delay),
            'request_timeout': self.request_timeout,
        }

        self.concurrent_limit = overrides.get('concurrent_limit', self.concurrent_limit)
        delay_range = overrides.get('delay_range', (self.min_delay, self.max_delay))
        self.min_delay, self.max_delay = delay_range
        self.request_timeout = overrides.get('request_timeout', self.request_timeout)
        self._max_candidate_urls = overrides.get('max_candidate_urls')

        self.logger.info(
            f"Full scan overrides applied: "
            f"concurrent {old['concurrent_limit']}→{self.concurrent_limit}, "
            f"delay {old['delay_range']}→{delay_range}, "
            f"timeout {old['request_timeout']}s→{self.request_timeout}s, "
            f"max_candidate_urls={self._max_candidate_urls}"
        )

    async def run_full_scan(
        self,
        start_id: Optional[int] = None,
        end_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        全範圍掃描：掃描指定範圍內的每一個 ID，不做 interpolation，不做 early-stop。

        Args:
            start_id: 起始 ID（流水號型必須提供）
            end_id: 結束 ID（流水號型必須提供）
            start_date: 起始日期 "YYYY-MM-DD" 或 "YYYY-MM"（日期型使用）
            end_date: 結束日期 "YYYY-MM-DD" 或 "YYYY-MM"（日期型使用）

        Returns:
            爬取結果統計
        """
        # 套用 full scan 專用設定（在建立 session 前）
        self._apply_full_scan_overrides()

        source_name = self.parser.source_name
        config = FULL_SCAN_CONFIG.get(source_name)

        if not config:
            self.logger.error(f"No full scan config for source: {source_name}")
            return {'error': f'No full scan config for {source_name}'}

        if config["type"] == "sequential":
            if start_id is None or end_id is None:
                self.logger.error("start_id and end_id are required for sequential full scan")
                return {'error': 'start_id and end_id are required for sequential sources'}

            if start_id > end_id:
                start_id, end_id = end_id, start_id

            return await self._full_scan_sequential(start_id, end_id)

        elif config["type"] == "date_based":
            from_date = self._parse_date_input(start_date or "2024-01-01")
            to_date = self._parse_date_input(end_date or datetime.now().strftime('%Y-%m-%d'), end_of_month=True)

            if not from_date or not to_date:
                return {'error': 'Invalid date format. Use YYYY-MM or YYYY-MM-DD'}

            if from_date > to_date:
                from_date, to_date = to_date, from_date

            max_suffix = config.get("max_suffix", 100)
            suffix_digits = config.get("suffix_digits", 4)
            return await self._full_scan_date_based(from_date, to_date, max_suffix, suffix_digits)

        else:
            return {'error': f'Unknown scan type: {config["type"]}'}

    async def _full_scan_sequential(self, start_id: int, end_id: int) -> Dict[str, Any]:
        """
        流水號型全範圍掃描（ascending: start_id → end_id）。

        掃描每一個 ID，不做 404 early-stop。
        Stop conditions: 只有 BLOCKED_CONSECUTIVE_LIMIT 和到達 end_id。
        404 adaptive throttle: 連續 N 個 404 後倍增 delay，碰到 200 OK 恢復。
        """
        total_range = end_id - start_id + 1
        self.logger.info(f"Full scan sequential: {start_id:,} -> {end_id:,} (total: {total_range:,})")

        self._reset_stats(
            total=total_range,
            progress=0,
            early_stopped=False,
            early_stop_reason=None,
            last_scanned_id=start_id,
            scan_start=start_id,
            scan_end=end_id,
        )

        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            batch_size = max(self.concurrent_limit * 5, 20)
            semaphore = asyncio.Semaphore(self.concurrent_limit)

            self.logger.info(f"Parallel scan: batch_size={batch_size}, concurrent_limit={self.concurrent_limit}")

            async def _process_one(aid: int) -> tuple:
                async with semaphore:
                    await self._adaptive_delay()
                    status = await self._process_article(aid, self.session)
                    return (aid, status)

            current_id = start_id
            while current_id <= end_id:
                # Collect batch, pre-filtering already-crawled IDs
                batch_ids = []
                while current_id <= end_id and len(batch_ids) < batch_size:
                    # Watermark check first (O(1), covers most IDs in re-scans)
                    # BUT: exclude blocked IDs (429) — they were never actually fetched
                    already_scanned = (
                        (self._watermark_id is not None and current_id <= self._watermark_id
                         and current_id not in self._blocked_ids)
                        or self._is_any_url_crawled(current_id)
                        or current_id in self._not_found_ids
                    )
                    if already_scanned:
                        self.stats['skipped'] += 1
                    else:
                        batch_ids.append(current_id)
                    current_id += 1

                self.stats['progress'] = current_id - start_id
                await self._report_progress()

                if not batch_ids:
                    continue

                # Process batch in parallel
                tasks = [_process_one(aid) for aid in batch_ids]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                self._evaluate_batch_results(batch_ids, results)

                # Update checkpoint after batch completes
                self.stats['last_scanned_id'] = current_id - 1
                self.registry.update_scan_watermark(
                    self.parser.source_name,
                    last_scanned_id=current_id - 1
                )
                self.registry.flush_not_found()
                self.stats['progress'] = current_id - start_id
                await self._report_progress()

                if self._check_blocked_stop():
                    break

                # Cooldown when blocked responses detected (prevent escalating to full block)
                if self.consecutive_failures > 0:
                    cooldown = self._get_blocked_cooldown()
                    limit = self._get_blocked_limit()
                    self.logger.warning(
                        f"Blocked: {self.consecutive_failures}/{limit}, "
                        f"cooling down {cooldown}s"
                    )
                    await asyncio.sleep(cooldown)

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    async def _full_scan_date_based(
        self,
        from_date: datetime,
        to_date: datetime,
        max_suffix: int,
        suffix_digits: int = 4
    ) -> Dict[str, Any]:
        """
        日期型 ID 全範圍掃描（ascending: from_date → to_date）。

        Per-day adaptive scanning:
        - 每天從 suffix 1 開始掃描
        - 連續 DATE_SCAN_MISS_LIMIT 個 404 後跳到隔天（省時）
        - 接近上限仍有文章時自動擴展 max_suffix（不漏文章）
        - BLOCKED_CONSECUTIVE_LIMIT 觸發全局停止

        Args:
            suffix_digits: 後綴位數（CNA=4 → 12位ID, chinatimes=6 → 14位ID）
        """
        suffix_multiplier = 10 ** suffix_digits
        total_days = (to_date - from_date).days + 1

        # Per-source miss_limit（chinatimes 密度極低，需要更高的 miss_limit）
        source_config = FULL_SCAN_CONFIG.get(self.parser.source_name, {})
        miss_limit = source_config.get("date_scan_miss_limit", settings.DATE_SCAN_MISS_LIMIT)

        self.logger.info(f"Full scan date-based: {from_date.strftime('%Y-%m-%d')} -> "
                        f"{to_date.strftime('%Y-%m-%d')} ({total_days} days, "
                        f"max_suffix={max_suffix}, suffix_digits={suffix_digits}, "
                        f"miss_limit={miss_limit})")

        self._reset_stats(
            total=total_days,
            progress=0,
            early_stopped=False,
            early_stop_reason=None,
            last_scanned_date=from_date.strftime('%Y-%m-%d'),
            scan_start=from_date.strftime('%Y-%m-%d'),
            scan_end=to_date.strftime('%Y-%m-%d'),
        )

        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            batch_size = max(self.concurrent_limit * 5, 20)
            semaphore = asyncio.Semaphore(self.concurrent_limit)
            days_processed = 0

            async def _process_one(aid: int) -> tuple:
                async with semaphore:
                    await self._adaptive_delay()
                    status = await self._process_article(aid, self.session)
                    return (aid, status)

            current_day = from_date
            while current_day <= to_date:
                date_prefix = int(current_day.strftime('%Y%m%d'))
                current_day_str = current_day.strftime('%Y-%m-%d')

                # Watermark skip: entire day already scanned in previous run
                # BUT: don't skip days that have blocked (429) URLs needing retry
                day_below_watermark = (
                    self._watermark_date is not None
                    and current_day_str <= self._watermark_date
                    and current_day_str not in self._blocked_dates
                )
                if day_below_watermark:
                    self.logger.debug(f"Day {date_prefix}: below watermark {self._watermark_date}, skipping")
                    days_processed += 1
                    self.stats['progress'] = days_processed
                    current_day += timedelta(days=1)
                    continue

                # Per-day adaptive scanning
                effective_max = max_suffix
                day_consecutive_miss = 0

                suffix = 1
                while suffix <= effective_max:
                    batch_ids = []
                    while suffix <= effective_max and len(batch_ids) < batch_size:
                        article_id = date_prefix * suffix_multiplier + suffix
                        suffix += 1

                        if self._is_any_url_crawled(article_id) or article_id in self._not_found_ids:
                            self.stats['skipped'] += 1
                            day_consecutive_miss = 0  # existing article = reset
                        else:
                            batch_ids.append(article_id)

                    await self._report_progress()

                    if not batch_ids:
                        # All skipped (already crawled) — check auto-extend
                        if suffix > effective_max and day_consecutive_miss < miss_limit:
                            old = effective_max
                            effective_max += settings.DATE_SCAN_AUTO_EXTEND_STEP
                            self.logger.warning(
                                f"Day {date_prefix}: articles near ceiling {old}, "
                                f"auto-extending to {effective_max}"
                            )
                        continue

                    tasks = [_process_one(aid) for aid in batch_ids]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    hit_count, miss_count = self._evaluate_batch_results(batch_ids, results)
                    await self._report_progress()

                    if hit_count > 0:
                        day_consecutive_miss = 0
                    else:
                        day_consecutive_miss += miss_count

                    if self._check_blocked_stop():
                        break

                    # Cooldown when blocked responses detected
                    if self.consecutive_failures > 0:
                        cooldown = self._get_blocked_cooldown()
                        limit = self._get_blocked_limit()
                        self.logger.warning(
                            f"Blocked: {self.consecutive_failures}/{limit}, "
                            f"cooling down {cooldown}s"
                        )
                        await asyncio.sleep(cooldown)

                    # Per-day early stop
                    if day_consecutive_miss >= miss_limit:
                        self.logger.debug(
                            f"Day {date_prefix}: {day_consecutive_miss} consecutive misses, "
                            f"skipping remaining suffixes (scanned to {suffix - 1})"
                        )
                        break

                    # Auto-extend: near/past ceiling but still finding articles
                    if suffix > effective_max and day_consecutive_miss < miss_limit:
                        old = effective_max
                        effective_max += settings.DATE_SCAN_AUTO_EXTEND_STEP
                        self.logger.warning(
                            f"Day {date_prefix}: articles found near ceiling {old}, "
                            f"auto-extending to {effective_max}"
                        )

                if self.stats.get('early_stopped'):
                    break

                # Update checkpoint after entire day completes
                days_processed += 1
                self.stats['last_scanned_date'] = current_day.strftime('%Y-%m-%d')
                self.registry.update_scan_watermark(
                    self.parser.source_name,
                    last_scanned_date=current_day.strftime('%Y-%m-%d')
                )
                self.registry.flush_not_found()
                self.stats['progress'] = days_processed

                current_day += timedelta(days=1)

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    async def run_retry(
        self,
        max_retries: int = 3,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Retry failed URLs for this source.

        Uses conservative settings (concurrent=1, doubled delays) when
        retrying blocked URLs to avoid triggering blocks again.

        Args:
            max_retries: Maximum retry attempts (URLs with more retries are skipped)
            limit: Maximum number of URLs to retry in this run

        Returns:
            Crawl statistics
        """
        source_name = self.parser.source_name
        failed_urls = self.registry.get_failed_urls_for_retry(
            source_id=source_name,
            max_retries=max_retries,
            limit=limit
        )

        if not failed_urls:
            self.logger.info(f"No failed URLs to retry for {source_name}")
            return {'total': 0, 'message': 'No failed URLs to retry'}

        # Check if any failed URLs are blocked — use conservative settings
        has_blocked = self.registry.has_blocked_failures(source_name)
        if has_blocked:
            retry_concurrent = 1
            retry_min_delay = self.min_delay * 2
            retry_max_delay = self.max_delay * 2
            self.logger.info(
                f"Blocked URLs detected — conservative mode: "
                f"concurrent=1, delay={retry_min_delay:.1f}s-{retry_max_delay:.1f}s"
            )
        else:
            retry_concurrent = self.concurrent_limit
            retry_min_delay = self.min_delay
            retry_max_delay = self.max_delay

        self.logger.info(f"Retrying {len(failed_urls)} failed URLs for {source_name}")

        # Reset stats
        self._reset_stats(total=len(failed_urls))

        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            semaphore = asyncio.Semaphore(retry_concurrent)

            async def process_url_with_semaphore(url: str):
                async with semaphore:
                    delay = random.uniform(retry_min_delay, retry_max_delay)
                    await asyncio.sleep(delay)
                    return await self._process_url(url, self.session)

            # Process in batches with extra cooldown between batches for blocked URLs
            batch_size = 10
            for i in range(0, len(failed_urls), batch_size):
                batch = failed_urls[i:i + batch_size]
                tasks = [process_url_with_semaphore(url) for url in batch]
                await asyncio.gather(*tasks, return_exceptions=True)

                if has_blocked and i + batch_size < len(failed_urls):
                    self.logger.info("Conservative mode: 5s cooldown between batches")
                    await asyncio.sleep(5.0)

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    async def run_retry_urls(
        self,
        urls: List[str]
    ) -> Dict[str, Any]:
        """
        Retry specific URLs.

        Args:
            urls: List of URLs to retry

        Returns:
            Crawl statistics
        """
        if not urls:
            self.logger.info("No URLs provided for retry")
            return {'total': 0, 'message': 'No URLs provided'}

        self.logger.info(f"Retrying {len(urls)} specific URLs for {self.parser.source_name}")

        # Reset stats
        self._reset_stats(total=len(urls))

        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            semaphore = asyncio.Semaphore(self.concurrent_limit)

            async def process_url_with_semaphore(url: str):
                async with semaphore:
                    await self._adaptive_delay()
                    return await self._process_url(url, self.session)

            tasks = [process_url_with_semaphore(url) for url in urls]
            await asyncio.gather(*tasks, return_exceptions=True)

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    async def _process_url(
        self,
        url: str,
        session: Union[aiohttp.ClientSession, 'CurlSession']
    ) -> CrawlStatus:
        """
        Process a specific URL (for retry mode).

        Similar to _process_article but takes URL directly instead of article_id.
        """
        if self._is_crawled(url):
            self.stats['skipped'] += 1
            # Already crawled, remove from failed list
            self.registry.remove_failed(url)
            await self._report_progress()
            return CrawlStatus.SUCCESS

        html, status = await self._fetch(url, session)

        if status == CrawlStatus.NOT_FOUND:
            self.stats['not_found'] += 1
            await self._report_progress()
            return CrawlStatus.NOT_FOUND

        if status == CrawlStatus.BLOCKED:
            self.stats['blocked'] += 1
            self._mark_failed(url, "blocked", "Request blocked on retry")
            await self._report_progress()
            return CrawlStatus.BLOCKED

        if html is None:
            self.stats['failed'] += 1
            self._mark_failed(url, "fetch_error", "Failed to fetch HTML on retry")
            await self._report_progress()
            return CrawlStatus.BLOCKED

        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(
                None, _run_parse_in_thread, self.parser, html, url
            )
            if data is None:
                self.stats['failed'] += 1
                self._mark_failed(url, "parse_error", "Parser returned None on retry")
                await self._report_progress()
                return CrawlStatus.NOT_FOUND

            # Ensure date exists (matching _process_article behavior)
            data = self._ensure_date(data, html, url)
            if data is None:
                self.stats['failed'] += 1
                self._mark_failed(url, "no_date", "No date found on retry")
                await self._report_progress()
                return CrawlStatus.NOT_FOUND

            # Mark as crawled
            self._mark_as_crawled(url, data)

            # Remove from failed list (successful retry)
            self.registry.remove_failed(url)
            self.logger.info(f"Successfully retried: {url[:80]}...")

            if self.auto_save:
                success = await self.pipeline.process_and_save(url, data)
                if success:
                    self.stats['success'] += 1
                else:
                    self.stats['failed'] += 1
                    self._mark_failed(url, "save_error", "Pipeline save failed on retry")
            else:
                self.stats['success'] += 1

            await self._report_progress()
            return CrawlStatus.SUCCESS

        except Exception as e:
            self.logger.error(f"Error parsing {url} on retry: {str(e)}")
            self.stats['failed'] += 1
            self._mark_failed(url, "parse_exception", str(e)[:200])
            await self._report_progress()
            return CrawlStatus.FETCH_ERROR

    async def run_sitemap(
        self,
        sitemap_index_url: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 0,
        sitemap_offset: int = 0,
        sitemap_count: int = 0,
    ) -> Dict[str, Any]:
        """
        從 Sitemap 爬取文章。

        這是最完整的 backfill 方法，因為 sitemap 包含所有文章的
        正確 URL（包含 category），不需要猜測。

        Args:
            sitemap_index_url: Sitemap URL (optional, will use parser's config if not provided)
            date_from: 起始日期 (YYYYMM 格式，如 "202301")，None 表示不限
            date_to: 結束日期 (YYYYMM 格式，如 "202312")，None 表示不限
            limit: 最大爬取數量，0 表示不限
            sitemap_offset: 從第幾個 sub-sitemap 開始（0-based），0 表示從頭
            sitemap_count: 處理幾個 sub-sitemap，0 表示全部

        Returns:
            爬取結果統計
        """
        # 獲取 parser 的 sitemap 配置
        sitemap_config = self.parser.get_sitemap_config()
        if not sitemap_config and not sitemap_index_url:
            self.logger.error(f"No sitemap config available for {self.parser.source_name}")
            return {'error': f'No sitemap config for {self.parser.source_name}'}

        # 使用提供的 URL 或 parser 配置
        sitemap_url = sitemap_index_url or sitemap_config.get('index_url')
        is_index = sitemap_config.get('is_index', True) if sitemap_config else True
        article_pattern = sitemap_config.get('article_url_pattern') if sitemap_config else None

        self.logger.info(f"Starting sitemap crawl from: {sitemap_url}")
        self.logger.info(f"  Is sitemap index: {is_index}")
        if date_from:
            self.logger.info(f"  Date from: {date_from}")
        if date_to:
            self.logger.info(f"  Date to: {date_to}")
        if limit > 0:
            self.logger.info(f"  Limit: {limit}")
        if sitemap_offset > 0:
            self.logger.info(f"  Sitemap offset: {sitemap_offset}")
        if sitemap_count > 0:
            self.logger.info(f"  Sitemap count: {sitemap_count}")

        # 重置統計
        self._reset_stats(sitemaps_processed=0, early_stopped=False, early_stop_reason=None)

        # 創建 session
        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            semaphore = asyncio.Semaphore(self.concurrent_limit)
            batch_size = 100
            total_crawled = 0

            async def process_with_semaphore(url: str):
                async with semaphore:
                    await self._random_delay()
                    return await self._process_url(url, self.session)

            async def _crawl_url_batch(urls: List[str]) -> int:
                """Crawl a batch of URLs incrementally. Returns number crawled."""
                nonlocal total_crawled
                crawled_in_batch = 0
                for i in range(0, len(urls), batch_size):
                    batch = urls[i:i + batch_size]
                    tasks = [process_with_semaphore(url) for url in batch]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    crawled_in_batch += len(batch)
                    total_crawled += len(batch)
                    self.stats['progress'] = total_crawled
                    self.logger.info(f"Progress: {total_crawled} crawled")

                    if limit > 0 and total_crawled >= limit:
                        return crawled_in_batch
                return crawled_in_batch

            if is_index:
                # Sitemap Index: 獲取所有子 sitemap URLs
                sitemap_urls = await self._fetch_sitemap_index(sitemap_url)
                if not sitemap_urls:
                    self.logger.error("Failed to fetch sitemap index or no sitemaps found")
                    return {'error': 'Failed to fetch sitemap index'}

                self.logger.info(f"Found {len(sitemap_urls)} sitemap files")

                # 過濾日期範圍（sitemap 檔名層級）
                if date_from or date_to:
                    sitemap_urls = self._filter_sitemaps_by_date(sitemap_urls, date_from, date_to)
                    self.logger.info(f"After date filter: {len(sitemap_urls)} sitemap files")

                # 多機分工：offset + count 切片
                if sitemap_offset > 0:
                    sitemap_urls = sitemap_urls[sitemap_offset:]
                    self.logger.info(f"After offset={sitemap_offset}: {len(sitemap_urls)} sitemaps remaining")
                if sitemap_count > 0:
                    sitemap_urls = sitemap_urls[:sitemap_count]
                    self.logger.info(f"Processing {len(sitemap_urls)} sitemaps (count={sitemap_count})")

                # 逐個處理 sitemap — 下載後立即爬取，不累積 URL
                for sub_sitemap_url in sitemap_urls:
                    url_tuples = await self._fetch_sitemap_urls(sub_sitemap_url, article_pattern)
                    if url_tuples:
                        total_in_sitemap = len(url_tuples)

                        # 日期過濾（文章 URL 層級，使用 lastmod）
                        article_urls = self._filter_article_urls_by_date(url_tuples, date_from, date_to)

                        new_urls = [url for url in article_urls if not self._is_crawled(url)]
                        self.stats['skipped'] += len(article_urls) - len(new_urls)
                        self.stats['sitemaps_processed'] += 1
                        self.logger.info(f"Sitemap {self.stats['sitemaps_processed']}/{len(sitemap_urls)}: "
                                       f"{total_in_sitemap} total, {len(article_urls)} in range, {len(new_urls)} new")

                        # 立即爬取這批 URL（不累積到記憶體）
                        if new_urls:
                            await _crawl_url_batch(new_urls)

                    if limit > 0 and total_crawled >= limit:
                        self.logger.info(f"Reached limit of {limit} URLs")
                        break
            else:
                # Single Sitemap: 直接獲取文章 URLs
                url_tuples = await self._fetch_sitemap_urls(sitemap_url, article_pattern)
                if url_tuples:
                    total_before = len(url_tuples)

                    # 日期過濾（文章 URL 層級，使用 lastmod）
                    article_urls = self._filter_article_urls_by_date(url_tuples, date_from, date_to)
                    self.logger.info(f"After date filter: {len(article_urls)}/{total_before} URLs")

                    new_urls = [url for url in article_urls if not self._is_crawled(url)]
                    self.stats['skipped'] += len(article_urls) - len(new_urls)
                    self.stats['sitemaps_processed'] = 1
                    self.logger.info(f"Single sitemap: {total_before} total, {len(article_urls)} in range, {len(new_urls)} new")

                    if limit > 0:
                        new_urls = new_urls[:limit]
                        self.logger.info(f"Applied limit of {limit} URLs")

                    if new_urls:
                        await _crawl_url_batch(new_urls)

            self.logger.info(f"Sitemap crawl complete: {total_crawled} URLs crawled")

            if total_crawled == 0:
                self.logger.info("No new URLs to crawl")

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    async def _fetch_sitemap_index(self, index_url: str) -> List[str]:
        """
        獲取 sitemap index 並解析出所有 sitemap 文件的 URL。

        Args:
            index_url: Sitemap index URL

        Returns:
            List of sitemap file URLs

        Note: Caller must ensure self.session is initialized.
        """
        if self.session is None:
            self.logger.error("Session not initialized")
            return []

        try:
            if self.session_type == SessionType.CURL_CFFI:
                response = await self.session.get(
                    index_url, headers=self._get_headers()
                )
                if response.status_code != 200:
                    self.logger.error(f"Failed to fetch sitemap index: HTTP {response.status_code}")
                    return []
                content = response.text
            else:
                async with self.session.get(
                    index_url,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to fetch sitemap index: HTTP {response.status}")
                        return []
                    content = await response.text()

            # 解析 sitemap URLs
            # 格式: <loc>https://...</loc>
            pattern = r'<loc>(https?://[^<]+\.xml)</loc>'
            sitemap_urls = re.findall(pattern, content)

            return sitemap_urls

        except Exception as e:
            self.logger.error(f"Error fetching sitemap index: {e}")
            return []

    async def _fetch_sitemap_urls(
        self,
        sitemap_url: str,
        article_pattern: Optional[str] = None
    ) -> List[tuple]:
        """
        獲取單個 sitemap 文件並解析出所有文章 URL 及其 lastmod 日期。

        Args:
            sitemap_url: Sitemap file URL
            article_pattern: Regex pattern to extract article URLs (optional)

        Returns:
            List of (url, lastmod_yyyymm) tuples. lastmod_yyyymm 為 None 若無法解析。
        """
        try:
            if self.session_type == SessionType.CURL_CFFI:
                response = await self.session.get(
                    sitemap_url, headers=self._get_headers()
                )
                if response.status_code != 200:
                    self.logger.warning(f"Failed to fetch sitemap {sitemap_url}: HTTP {response.status_code}")
                    return []
                content_bytes = response.content
            else:
                async with self.session.get(
                    sitemap_url,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        self.logger.warning(f"Failed to fetch sitemap {sitemap_url}: HTTP {response.status}")
                        return []
                    content_bytes = await response.read()

            # Handle BOM encoding
            try:
                content = content_bytes.decode('utf-8-sig')
            except UnicodeDecodeError:
                content = content_bytes.decode('utf-8', errors='replace')

            # 解析 <url> 區塊，提取 loc 和 lastmod
            # 格式: <url><loc>...</loc><lastmod>2024-01-07T12:03:01+08:00</lastmod>...</url>
            url_blocks = re.findall(r'<url>(.*?)</url>', content, re.DOTALL)

            results = []
            for block in url_blocks:
                # 提取 loc
                loc_match = re.search(r'<loc>(https?://[^<]+)</loc>', block)
                if not loc_match:
                    continue

                url = loc_match.group(1)

                # 跳過 .xml 結尾（子 sitemap）
                if url.endswith('.xml'):
                    continue

                # 如果有 article_pattern，檢查是否匹配
                if article_pattern and not re.search(article_pattern, url):
                    continue

                # 提取 lastmod 日期 (YYYY-MM 部分)
                lastmod_yyyymm = None
                lastmod_match = re.search(r'<lastmod>(\d{4})-(\d{2})', block)
                if lastmod_match:
                    lastmod_yyyymm = lastmod_match.group(1) + lastmod_match.group(2)

                results.append((url, lastmod_yyyymm))

            return results

        except Exception as e:
            self.logger.warning(f"Error fetching sitemap {sitemap_url}: {e}")
            return []

    def _filter_sitemaps_by_date(
        self,
        sitemap_urls: List[str],
        date_from: Optional[str],
        date_to: Optional[str]
    ) -> List[str]:
        """
        根據日期範圍過濾 sitemap URLs。

        UDN sitemap 命名格式: {TYPE}T{YYYYMM}W{WEEK}.xml
        例如: 2T202312W4.xml

        Args:
            sitemap_urls: List of sitemap URLs
            date_from: 起始日期 (YYYYMM)
            date_to: 結束日期 (YYYYMM)

        Returns:
            Filtered list of sitemap URLs
        """
        if not date_from and not date_to:
            return sitemap_urls

        filtered = []
        pattern = r'T(\d{6})W'  # 匹配 T202312W 中的 202312

        for url in sitemap_urls:
            match = re.search(pattern, url)
            if match:
                date_str = match.group(1)  # e.g., "202312"

                if date_from and date_str < date_from:
                    continue
                if date_to and date_str > date_to:
                    continue

                filtered.append(url)
            else:
                # 無法解析日期的 sitemap 也保留
                filtered.append(url)

        return filtered

    def _filter_article_urls_by_date(
        self,
        url_tuples: List[tuple],
        date_from: Optional[str],
        date_to: Optional[str]
    ) -> List[str]:
        """
        根據日期範圍過濾文章 URLs。

        優先使用 sitemap 的 lastmod 日期，若無則嘗試從 URL 提取 YYYYMMDD。
        無法提取日期的 URL 預設排除（避免爬到範圍外的資料）。

        Args:
            url_tuples: List of (url, lastmod_yyyymm) tuples from _fetch_sitemap_urls
            date_from: 起始日期 (YYYYMM 格式，如 "202401")
            date_to: 結束日期 (YYYYMM 格式，如 "202501")

        Returns:
            Filtered list of URLs (strings only)
        """
        if not date_from and not date_to:
            return [t[0] if isinstance(t, tuple) else t for t in url_tuples]

        filtered = []
        excluded = 0
        no_date = 0

        for item in url_tuples:
            # 支援舊格式 (純 URL 列表) 和新格式 (url, lastmod) tuples
            if isinstance(item, tuple):
                url, lastmod_ym = item
            else:
                url, lastmod_ym = item, None

            # 優先從 URL 提取 YYYYMMDD（實際發布日期，比 lastmod 更可靠）
            # lastmod 可能是 sitemap 重新產生的日期，不代表文章發布日期
            url_ym = None
            matches = re.findall(r'(\d{8,14})', url)
            for m in matches:
                try:
                    year = int(m[:4])
                    month = int(m[4:6])
                    day = int(m[6:8])
                    if 2000 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
                        url_ym = f"{year:04d}{month:02d}"
                        break
                except (ValueError, IndexError):
                    continue

            # 若 URL 無日期，fallback 到 lastmod
            if not url_ym:
                url_ym = lastmod_ym

            # 無法判斷日期的 URL 排除（避免爬到範圍外的資料）
            if not url_ym:
                no_date += 1
                continue

            # 日期範圍過濾
            if date_from and url_ym < date_from:
                excluded += 1
                continue
            if date_to and url_ym > date_to:
                excluded += 1
                continue

            filtered.append(url)

        if excluded > 0 or no_date > 0:
            self.logger.info(f"Date filter: excluded {excluded} outside range, {no_date} without date "
                           f"({date_from or '*'} - {date_to or '*'})")

        return filtered

    async def run_list_page(
        self,
        list_urls: Optional[List[str]] = None,
        limit: int = 0
    ) -> Dict[str, Any]:
        """
        從列表頁爬取文章。

        適用於沒有 sitemap 的網站（如 CNA），從分類列表頁獲取文章 URLs。

        Args:
            list_urls: 列表頁 URLs（可選，會從 parser 的 get_list_page_config() 獲取）
            limit: 最大爬取數量，0 表示不限

        Returns:
            爬取結果統計
        """
        # 獲取 parser 的列表頁配置
        list_config = self.parser.get_list_page_config()
        if not list_config and not list_urls:
            self.logger.error(f"No list page config available for {self.parser.source_name}")
            return {'error': f'No list page config for {self.parser.source_name}'}

        # 使用提供的 URLs 或 parser 配置
        urls_to_scan = list_urls or list_config.get('list_urls', [])
        article_pattern = list_config.get('article_url_pattern') if list_config else None
        base_url = list_config.get('base_url', '') if list_config else ''

        self.logger.info(f"Starting list page crawl for {self.parser.source_name}")
        self.logger.info(f"  List pages to scan: {len(urls_to_scan)}")
        if limit > 0:
            self.logger.info(f"  Limit: {limit}")

        # 重置統計
        self._reset_stats(list_pages_processed=0, early_stopped=False, early_stop_reason=None)

        # 創建 session
        need_close = self.session is None
        if need_close:
            self.session = await self._create_session()

        try:
            total_urls_to_crawl = []
            seen_urls = set()

            # 掃描所有列表頁
            for list_url in urls_to_scan:
                try:
                    async with self.session.get(
                        list_url,
                        headers=self._get_headers(),
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status != 200:
                            self.logger.warning(f"Failed to fetch list page {list_url}: HTTP {response.status}")
                            continue

                        content = await response.text()

                    # 使用配置的 pattern 或通用 pattern
                    if article_pattern:
                        # Pattern 返回 (category, article_id) tuple
                        matches = re.findall(article_pattern, content)
                        article_urls = [f"{base_url}/news/{cat}/{aid}.aspx" for cat, aid in matches]
                    else:
                        # 通用 pattern
                        pattern = r'href=\"([^\"]+\.aspx)\"'
                        matches = re.findall(pattern, content)
                        article_urls = [f"{base_url}{m}" if m.startswith('/') else m for m in matches]

                    # 去重和過濾已爬取
                    new_urls = []
                    for url in article_urls:
                        if url not in seen_urls and not self._is_crawled(url):
                            seen_urls.add(url)
                            new_urls.append(url)

                    total_urls_to_crawl.extend(new_urls)
                    self.stats['list_pages_processed'] += 1
                    self.logger.info(f"List page {self.stats['list_pages_processed']}/{len(urls_to_scan)}: "
                                   f"{len(article_urls)} URLs, {len(new_urls)} new")

                except Exception as e:
                    self.logger.warning(f"Error fetching list page {list_url}: {e}")
                    continue

                # 檢查是否達到 limit
                if limit > 0 and len(total_urls_to_crawl) >= limit:
                    total_urls_to_crawl = total_urls_to_crawl[:limit]
                    self.logger.info(f"Reached limit of {limit} URLs")
                    break

            self.logger.info(f"Total URLs to crawl: {len(total_urls_to_crawl)}")

            if not total_urls_to_crawl:
                self.logger.info("No new URLs to crawl")
                return self.stats

            # 爬取文章
            self.stats['total'] = len(total_urls_to_crawl)

            semaphore = asyncio.Semaphore(self.concurrent_limit)

            async def process_with_semaphore(url: str):
                async with semaphore:
                    await self._random_delay()
                    return await self._process_url(url, self.session)

            batch_size = 100
            for i in range(0, len(total_urls_to_crawl), batch_size):
                batch = total_urls_to_crawl[i:i + batch_size]
                tasks = [process_with_semaphore(url) for url in batch]
                await asyncio.gather(*tasks, return_exceptions=True)

                self.stats['progress'] = min(i + batch_size, len(total_urls_to_crawl))
                self.logger.info(f"Progress: {self.stats['progress']}/{len(total_urls_to_crawl)}")

        finally:
            if need_close:
                await self.close()

        self._log_stats()
        return self.stats

    def _parse_date_input(
        self,
        date_str: str,
        end_of_month: bool = False
    ) -> Optional[datetime]:
        """
        解析日期輸入字串。

        Args:
            date_str: 日期字串，支援 "YYYY-MM" 或 "YYYY-MM-DD"
            end_of_month: 如果只有年月，是否返回月底日期

        Returns:
            datetime 物件或 None
        """
        date_str = date_str.strip()

        # 嘗試 YYYY-MM-DD
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass

        # 嘗試 YYYY-MM
        try:
            dt = datetime.strptime(date_str, '%Y-%m')
            if end_of_month:
                # 返回月底
                _, last_day = calendar.monthrange(dt.year, dt.month)
                return dt.replace(day=last_day)
            return dt
        except ValueError:
            pass

        return None

    def _log_stats(self) -> None:
        """輸出統計資訊"""
        self.logger.info("=" * 50)
        self.logger.info("Crawl Statistics:")
        self.logger.info(f"  Total:     {self.stats['total']}")
        self.logger.info(f"  Success:   {self.stats['success']}")
        self.logger.info(f"  Failed:    {self.stats['failed']}")
        self.logger.info(f"  Skipped:   {self.stats['skipped']}")
        self.logger.info(f"  Not Found: {self.stats['not_found']}")
        self.logger.info(f"  Blocked:   {self.stats['blocked']}")
        if 'out_of_range' in self.stats:
            self.logger.info(f"  Out of Range: {self.stats['out_of_range']}")

        if self.stats['total'] > 0:
            rate = (self.stats['success'] / self.stats['total']) * 100
            self.logger.info(f"  Success Rate: {rate:.1f}%")

        self.logger.info("=" * 50)

    async def close(self) -> None:
        """關閉 Session 和 Logger FileHandlers"""
        if self.session is not None:
            try:
                await asyncio.wait_for(self.session.close(), timeout=5.0)
                self.logger.info("Session closed")
            except asyncio.TimeoutError:
                self.logger.warning("Session close timed out")
            except Exception as e:
                self.logger.warning(f"Error closing session: {e}")
            finally:
                self.session = None

        # Close and remove FileHandlers to prevent resource leaks
        for handler in self.logger.handlers[:]:
            if isinstance(handler, logging.FileHandler):
                try:
                    handler.close()
                except Exception:
                    pass
                self.logger.removeHandler(handler)
