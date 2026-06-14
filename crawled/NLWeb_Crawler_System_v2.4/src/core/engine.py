import asyncio
import aiohttp
import logging
import random
import time
from typing import Dict, List, Optional, Any, Set, Union
from pathlib import Path
from datetime import datetime, timedelta
from enum import Enum

from config import settings
from config.settings import DEFAULT_HEADERS
from src.core.interfaces import BaseParser
from src.core.pipeline import Pipeline

# ✅ FIX #ENGINE-HYBRID-SESSION: 引入 curl_cffi 支援
try:
    from curl_cffi.requests import AsyncSession as CurlSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    CurlSession = None

# ✅ FIX #ENGINE-REFACTOR-V2: 引入狀態列舉
class CrawlStatus(Enum):
    """
    爬取狀態列舉
    
    用途：精確分類每次爬取的結果，避免誤判
    
    ✅ FIX #ENGINE-REFACTOR-V2: 核心改進
    - SUCCESS: 200 OK，成功爬取
    - NOT_FOUND: 404 Not Found，文章不存在（真的沒有）
    - BLOCKED: 403/429/5xx/Timeout，被封鎖或網路問題（可能有資料）
    """
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    BLOCKED = "BLOCKED"

# ✅ FIX #ENGINE-HYBRID-SESSION: Session 類型列舉
class SessionType(Enum):
    """Session 類型列舉"""
    AIOHTTP = "aiohttp"
    CURL_CFFI = "curl_cffi"

class CrawlerEngine:
    """
    通用爬蟲引擎
    
    ✅ FIX #WORK-ORDER-921: 支援來源專屬設定 (Source-Specific Config)
    ✅ FIX #WORK-ORDER-912: 修復日期解析與跳躍邏輯除錯
    ✅ FIX #WORK-ORDER-911: 修正智能跳躍計數邏輯與視覺化
    ✅ FIX #WORK-ORDER-906: 加入除錯 Log 追蹤 Duck Typing
    ✅ FIX #WORK-ORDER-902: 支援列表式爬取 (List-Based Crawling)
    ✅ FIX #WORK-ORDER-902: 修復 Pipeline.close() AttributeError
    ✅ FIX #WORK-ORDER-806: 處理 Parser 回傳 None 的情況
    ✅ FIX #ENGINE-SAFETY-PATCH: 提高智能跳躍容忍度
    ✅ FIX #ENGINE-FIX-001: 整合全域偽裝標頭
    ✅ FIX #ENGINE-REFACTOR-V2: 重構狀態分類邏輯
    ✅ FIX #ENGINE-HYBRID-SESSION: 支援 aiohttp 和 curl_cffi
    ✅ FIX #ENGINE-CLOSE-TIMEOUT: 加上 close() timeout 保護
    
    設計原則：
    1. 依賴注入：透過 BaseParser 介面與具體網站解耦
    2. 關注點分離：只負責爬取流程，解析邏輯委託給 Parser
    3. 可重用：適用於所有實作 BaseParser 的網站
    
    核心功能：
    - 範圍爬取：run_range(start_id, end_id)
    - 列表爬取：run_list(url_list)
    - 自動爬取：run_auto(count) - 支援列表式和流水號式
    - 併發控制：使用 Semaphore 限制同時請求數（支援來源專屬設定）
    - 重試機制：處理網路錯誤和臨時失敗
    - 去重機制：避免重複爬取
    - 自動儲存：整合 Pipeline
    - 智能跳躍：偵測空窗期並跳到下一天（ChinaTimes/CNA，閾值 100）
    - 狀態分類：區分 404（不存在）與 403/429（被封鎖）
    - 混合 Session：根據來源自動選擇 aiohttp 或 curl_cffi
    - None 容錯：處理 Parser 回傳 None 的情況（MOEA 列表策略）
    - 來源專屬設定：優先讀取 NEWS_SOURCES 中的 concurrent_limit 和 delay_range
    """
    
    SMART_JUMP_THRESHOLD = 100  # ✅ 連續失敗 100 次觸發跳躍
    
    def __init__(
        self,
        parser: BaseParser,
        session: Optional[Union[aiohttp.ClientSession, 'CurlSession']] = None,
        auto_save: bool = True
    ):
        """
        初始化爬蟲引擎
        
        ✅ FIX #WORK-ORDER-921: 優先讀取來源專屬設定
        ✅ FIX #ENGINE-HYBRID-SESSION: 支援多種 Session 類型
        
        Args:
            parser: BaseParser 實例（必須）
            session: aiohttp.ClientSession 或 curl_cffi.AsyncSession 實例（可選）
            auto_save: 是否自動儲存爬取結果（預設 True）
        """
        self.parser = parser
        self.session = session
        self.auto_save = auto_save
        
        # ✅ FIX #WORK-ORDER-921: 載入來源專屬設定
        self._load_source_config()
        
        # ✅ FIX #ENGINE-HYBRID-SESSION: 判斷 Session 類型
        if session is not None:
            if CURL_CFFI_AVAILABLE and isinstance(session, CurlSession):
                self.session_type = SessionType.CURL_CFFI
            else:
                self.session_type = SessionType.AIOHTTP
        else:
            # 根據來源自動選擇（可在 settings 中配置）
            if hasattr(settings, 'CURL_CFFI_SOURCES') and parser.source_name in settings.CURL_CFFI_SOURCES:
                self.session_type = SessionType.CURL_CFFI
            else:
                self.session_type = SessionType.AIOHTTP
        
        # 設定日誌
        self.logger = logging.getLogger(f"{self.__class__.__name__}_{parser.source_name}")
        if not self.logger.handlers:
            self._setup_logger()
        
        self.logger.info(f"Engine initialized with session type: {self.session_type.value}")
        
        # ✅ FIX #WORK-ORDER-921: 顯示來源專屬設定
        self.logger.info(f"   Concurrent limit: {self.concurrent_limit}")
        self.logger.info(f"   Delay range: {self.min_delay:.1f}s - {self.max_delay:.1f}s")
        
        # 初始化 Pipeline
        if self.auto_save:
            self.pipeline = Pipeline(source_name=parser.source_name)
        
        # 載入歷史記錄（去重）
        self.crawled_ids: Set[str] = set()
        self._load_history()
        
        # ✅ FIX #ENGINE-REFACTOR-V2: 更新統計資訊
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'not_found': 0,
            'blocked': 0,
        }
        
        # 智能跳躍狀態
        self.consecutive_failures = 0  # ✅ 計算所有失敗（NOT_FOUND + BLOCKED）
        self.smart_jump_count = 0
        
        # 429 降速狀態
        self.rate_limit_hit = False
        self.rate_limit_cooldown_until = 0
    
    def _load_source_config(self) -> None:
        """
        載入來源專屬設定
        
        ✅ FIX #WORK-ORDER-921: 新增方法
        
        優先順序：
        1. settings.NEWS_SOURCES[source_name]['concurrent_limit']
        2. settings.CONCURRENT_REQUESTS（全域預設值）
        
        同理適用於 delay_range
        """
        source_name = self.parser.source_name
        
        # 嘗試從 NEWS_SOURCES 讀取來源專屬設定
        if hasattr(settings, 'NEWS_SOURCES') and source_name in settings.NEWS_SOURCES:
            source_config = settings.NEWS_SOURCES[source_name]
            
            # 讀取 concurrent_limit
            self.concurrent_limit = source_config.get('concurrent_limit', settings.CONCURRENT_REQUESTS)
            
            # 讀取 delay_range
            delay_range = source_config.get('delay_range', (settings.MIN_DELAY, settings.MAX_DELAY))
            self.min_delay, self.max_delay = delay_range
            
        else:
            # 降級為全域預設值
            self.concurrent_limit = settings.CONCURRENT_REQUESTS
            self.min_delay = settings.MIN_DELAY
            self.max_delay = settings.MAX_DELAY
    
    def _setup_logger(self) -> None:
        """設置日誌處理器"""
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
        載入歷史已爬取的 URL 記錄
        
        Returns:
            載入的 URL 數量
        """
        try:
            if not settings.CRAWLED_IDS_DIR.exists():
                self.logger.info(f"No history file found for {self.parser.source_name}, starting fresh")
                return 0
            
            ids_file = settings.CRAWLED_IDS_DIR / f"{self.parser.source_name}.txt"
            
            if not ids_file.exists():
                self.logger.info(f"No history file found for {self.parser.source_name}, starting fresh")
                return 0
            
            with open(ids_file, 'r', encoding='utf-8') as f:
                for line in f:
                    url = line.strip()
                    if url:
                        self.crawled_ids.add(url)
            
            count = len(self.crawled_ids)
            self.logger.info(f"📂 Loaded {count:,} crawled URLs from history")
            return count
            
        except Exception as e:
            self.logger.error(f"Error loading history: {str(e)}")
            if settings.DEBUG:
                import traceback
                self.logger.error(traceback.format_exc())
            return 0
    
    def _is_crawled(self, url: str) -> bool:
        """檢查 URL 是否已爬取"""
        return url in self.crawled_ids
    
    def _mark_as_crawled(self, url: str) -> None:
        """標記 URL 為已爬取"""
        self.crawled_ids.add(url)
    
    async def _create_session(self) -> Union[aiohttp.ClientSession, 'CurlSession']:
        """
        創建 Session（工廠方法）
        
        ✅ FIX #ENGINE-HYBRID-SESSION: 根據 session_type 選擇
        ✅ FIX #ENGINE-FIX-001: 自動套用全域預設 Headers
        
        Returns:
            aiohttp.ClientSession 或 curl_cffi.AsyncSession
        """
        if self.session_type == SessionType.CURL_CFFI:
            if not CURL_CFFI_AVAILABLE:
                self.logger.warning("curl_cffi not available, falling back to aiohttp")
                self.session_type = SessionType.AIOHTTP
            else:
                self.logger.info("Creating curl_cffi session")
                return CurlSession(
                    headers=DEFAULT_HEADERS,
                    timeout=settings.REQUEST_TIMEOUT,
                    impersonate="chrome110"  # 偽裝為 Chrome 110
                )
        
        # 預設使用 aiohttp
        self.logger.info("Creating aiohttp session")
        import ssl
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        return aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=settings.REQUEST_TIMEOUT),
            headers=DEFAULT_HEADERS
        )
    
    def _get_headers(self) -> Dict[str, str]:
        """
        獲取請求標頭（支援動態 User-Agent 輪換）
        
        策略：
        1. 基於全域 DEFAULT_HEADERS
        2. 隨機替換 User-Agent（從 USER_AGENTS 池選擇）
        3. 保留其他標頭不變
        
        Returns:
            完整的 HTTP Headers 字典
        """
        headers = DEFAULT_HEADERS.copy()
        headers['User-Agent'] = random.choice(settings.USER_AGENTS)
        return headers
    
    def _parse_date_from_id(self, article_id: int) -> Optional[datetime]:
        """
        從日期型 ID 中解析日期（用於智能跳躍）
        
        ✅ FIX #WORK-ORDER-912: 加入詳細除錯 Log，支援 8/12/14 碼
        
        支援格式：
        - 8 碼：YYYYMMDD (如 20251231)
        - 12 碼：YYYYMMDDxxxx (如 202512310390，CNA 格式)
        - 14 碼：YYYYMMDDHHmmss (如 20251231235959，ChinaTimes 格式)
        
        Args:
            article_id: 文章 ID
            
        Returns:
            解析出的日期，或 None（如果無法解析）
        """
        try:
            id_str = str(article_id)
            
            # ✅ 確保至少有 8 碼 (YYYYMMDD)
            if len(id_str) >= 8:
                date_str = id_str[:8]  # 只取前 8 碼
                
                # ✅ FIX #WORK-ORDER-912: 加入除錯 Log
                self.logger.debug(f"[Date Parse] ID: {article_id} (len={len(id_str)}), extracted: {date_str}")
                
                parsed_date = datetime.strptime(date_str, '%Y%m%d')
                
                self.logger.debug(f"[Date Parse] Success: {parsed_date.strftime('%Y-%m-%d')}")
                return parsed_date
            else:
                self.logger.warning(f"[Date Parse] ID too short: {article_id} (len={len(id_str)} < 8)")
                return None
                
        except (ValueError, IndexError) as e:
            # ✅ FIX #WORK-ORDER-912: 詳細錯誤訊息
            self.logger.error(f"[Date Parse] Failed for ID {article_id}: {e}")
            return None
        
        return None
    
    def _calculate_jump_target(self, current_id: int) -> Optional[int]:
        """
        計算智能跳躍的目標 ID（跳到下一天）
        
        ✅ FIX #WORK-ORDER-912: 加入詳細除錯 Log
        
        Args:
            current_id: 當前 ID
            
        Returns:
            跳躍目標 ID，或 None（如果無法計算）
        """
        self.logger.debug(f"[Jump Calc] Calculating jump target from ID: {current_id}")
        
        current_date = self._parse_date_from_id(current_id)
        
        if current_date is None:
            self.logger.warning(f"[Jump Calc] Failed: Cannot parse date from ID {current_id}")
            return None
        
        # 跳到下一天的 00:00:00
        next_day = current_date + timedelta(days=1)
        
        # ✅ 根據 ID 長度決定跳躍格式
        id_str = str(current_id)
        id_len = len(id_str)
        
        if id_len == 8:
            # 8 碼格式：YYYYMMDD
            jump_target_id = int(next_day.strftime('%Y%m%d'))
        elif id_len == 12:
            # 12 碼格式：YYYYMMDDxxxx (CNA)
            jump_target_id = int(next_day.strftime('%Y%m%d') + '0001')
        elif id_len == 14:
            # 14 碼格式：YYYYMMDDHHmmss (ChinaTimes)
            jump_target_id = int(next_day.strftime('%Y%m%d') + '000000')
        else:
            # 預設：使用 14 碼格式
            jump_target_id = int(next_day.strftime('%Y%m%d') + '000000')
        
        self.logger.debug(f"[Jump Calc] Success: {current_id} -> {jump_target_id} (next day: {next_day.strftime('%Y-%m-%d')})")
        
        return jump_target_id
    
    def _should_enable_smart_jump(self) -> bool:
        """
        判斷是否應該啟用智能跳躍
        
        Returns:
            True 如果應該啟用智能跳躍（ChinaTimes/CNA）
        """
        return self.parser.source_name in settings.SMART_JUMP_ENABLED_SOURCES
    
    async def _handle_rate_limit(self) -> None:
        """
        處理 429 Rate Limit 錯誤
        
        動態降速機制
        """
        self.rate_limit_hit = True
        cooldown = settings.RATE_LIMIT_COOLDOWN
        
        self.logger.warning(f"⚠️  Rate limit detected (429), cooling down for {cooldown}s...")
        self.rate_limit_cooldown_until = time.time() + cooldown
        
        await asyncio.sleep(cooldown)
        
        self.rate_limit_hit = False
        self.logger.info(f"✅ Cooldown completed, resuming...")
    
    async def _fetch(
        self, 
        url: str, 
        session: Union[aiohttp.ClientSession, 'CurlSession']
    ) -> tuple[Optional[str], CrawlStatus]:
        """
        獲取 URL 內容，包含重試機制
        
        ✅ FIX #ENGINE-REFACTOR-V2: 回傳 CrawlStatus
        ✅ FIX #ENGINE-REFACTOR-V2: Timeout 直接視為 NOT_FOUND（加速無效 ID）
        ✅ FIX #ENGINE-HYBRID-SESSION: 兼容 aiohttp 和 curl_cffi
        
        Args:
            url: 要獲取的 URL
            session: aiohttp.ClientSession 或 curl_cffi.AsyncSession
            
        Returns:
            (HTML 內容, CrawlStatus)
        """
        # 檢查是否在冷卻期
        if self.rate_limit_hit:
            wait_time = self.rate_limit_cooldown_until - time.time()
            if wait_time > 0:
                self.logger.debug(f"Waiting for rate limit cooldown: {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
        
        retry_count = 0
        max_retries = settings.MAX_RETRIES
        last_error_type = None
        
        while retry_count <= max_retries:
            try:
                headers = self._get_headers()
                
                # ✅ FIX #ENGINE-HYBRID-SESSION: 兼容兩種 Session API
                if self.session_type == SessionType.CURL_CFFI:
                    # curl_cffi API
                    response = await session.get(url, headers=headers)
                    status = response.status_code
                    
                    if status == 200:
                        html = response.text
                        return (html, CrawlStatus.SUCCESS)
                    
                    elif status == 404:
                        self.logger.debug(f"Page not found (404): {url}")
                        return (None, CrawlStatus.NOT_FOUND)
                    
                    elif status in (403, 429):
                        self.logger.warning(f"⚠️  Blocked ({status}) for {url}")
                        last_error_type = 'blocked'
                        await self._handle_rate_limit()
                        # 繼續重試
                    
                    elif status in (500, 502, 503, 504):
                        self.logger.warning(f"Server error ({status}) for {url}")
                        last_error_type = 'server_error'
                        # 繼續到重試邏輯
                    
                    else:
                        self.logger.warning(f"Unexpected status {status} for {url}")
                        return (None, CrawlStatus.BLOCKED)
                
                else:
                    # aiohttp API
                    async with session.get(
                        url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=settings.REQUEST_TIMEOUT)
                    ) as response:
                        if response.status == 200:
                            html = await response.text()
                            return (html, CrawlStatus.SUCCESS)
                        
                        elif response.status == 404:
                            self.logger.debug(f"Page not found (404): {url}")
                            return (None, CrawlStatus.NOT_FOUND)
                        
                        elif response.status in (403, 429):
                            self.logger.warning(f"⚠️  Blocked ({response.status}) for {url}")
                            last_error_type = 'blocked'
                            await self._handle_rate_limit()
                            # 繼續重試
                        
                        elif response.status in (500, 502, 503, 504):
                            self.logger.warning(f"Server error ({response.status}) for {url}")
                            last_error_type = 'server_error'
                            # 繼續到重試邏輯
                        
                        else:
                            self.logger.warning(f"Unexpected status {response.status} for {url}")
                            return (None, CrawlStatus.BLOCKED)
            
            except asyncio.TimeoutError:
                # ✅ FIX #ENGINE-REFACTOR-V2: Timeout 直接視為 NOT_FOUND
                # 原因：針對中時 000001 等無效號，不要讓它卡住重試
                self.logger.debug(f"Timeout for {url}, treating as NOT_FOUND")
                return (None, CrawlStatus.NOT_FOUND)
            
            except (aiohttp.ClientError, Exception) as e:
                # 兼容 curl_cffi 的異常
                self.logger.debug(f"Network error fetching {url}: {str(e)}")
                last_error_type = 'network_error'
                # 繼續到重試邏輯
            
            # 重試邏輯
            retry_count += 1
            if retry_count <= max_retries:
                wait_time = self._calculate_retry_delay(retry_count)
                self.logger.debug(f"Retrying {url} in {wait_time:.2f}s... ({retry_count}/{max_retries})")
                await asyncio.sleep(wait_time)
            else:
                self.logger.warning(f"Max retries reached for {url} (reason: {last_error_type})")
        
        # ✅ 重試失敗，標記為 BLOCKED
        return (None, CrawlStatus.BLOCKED)
    
    def _calculate_retry_delay(self, retry_count: int) -> float:
        """計算指數退避延遲時間"""
        delay = settings.RETRY_DELAY * (2 ** (retry_count - 1))
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        delay += jitter
        return min(delay, settings.MAX_RETRY_DELAY)
    
    async def _random_delay(self):
        """
        隨機延遲，避免被偵測為爬蟲
        
        ✅ FIX #WORK-ORDER-921: 使用來源專屬的 delay_range
        """
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
    
    async def _process_article(
        self,
        article_id: int,
        session: Union[aiohttp.ClientSession, 'CurlSession']
    ) -> CrawlStatus:
        """
        處理單篇文章
        
        ✅ FIX #WORK-ORDER-806: 處理 Parser 回傳 None 的情況
        ✅ FIX #ENGINE-REFACTOR-V2: 回傳 CrawlStatus
        
        Args:
            article_id: 文章 ID
            session: aiohttp.ClientSession 或 curl_cffi.AsyncSession
            
        Returns:
            CrawlStatus（SUCCESS / NOT_FOUND / BLOCKED）
        """
        # ✅ FIX #WORK-ORDER-806: 生成 URL（可能回傳 None）
        url = self.parser.get_url(article_id)
        
        # ✅ FIX #WORK-ORDER-806: 如果 Parser 回傳 None，直接跳過
        if not url:
            self.logger.debug(f"⏭️  Skipping ID {article_id:,}: Parser returned no URL (Not in cache)")
            self.stats['not_found'] += 1
            return CrawlStatus.NOT_FOUND
        
        # 檢查是否已爬取
        if self._is_crawled(url):
            self.logger.debug(f"⏭️  Skipping already crawled: {url}")
            self.stats['skipped'] += 1
            return CrawlStatus.SUCCESS  # 視為成功（不影響計數）
        
        # 獲取 HTML
        html, status = await self._fetch(url, session)
        
        # ✅ FIX #ENGINE-REFACTOR-V2: 根據狀態處理
        if status == CrawlStatus.NOT_FOUND:
            self.stats['not_found'] += 1
            return CrawlStatus.NOT_FOUND
        
        elif status == CrawlStatus.BLOCKED:
            self.stats['blocked'] += 1
            return CrawlStatus.BLOCKED
        
        # status == CrawlStatus.SUCCESS
        if html is None:
            self.stats['failed'] += 1
            return CrawlStatus.BLOCKED
        
        # 解析 HTML
        try:
            data = await self.parser.parse(html, url)
            if data is None:
                self.logger.debug(f"Parser returned None for {url}")
                self.stats['failed'] += 1
                return CrawlStatus.NOT_FOUND
            
            # 標記為已爬取
            self._mark_as_crawled(url)
            
            # 自動儲存
            if self.auto_save:
                success = await self.pipeline.process_and_save(url, data)
                if success:
                    self.logger.info(f"✅ Parsed ID: {article_id:,}")
                    self.stats['success'] += 1
                else:
                    self.logger.error(f"❌ Failed to save ID: {article_id:,}")
                    self.stats['failed'] += 1
            else:
                self.logger.info(f"✅ Parsed ID: {article_id:,}")
                self.stats['success'] += 1
            
            return CrawlStatus.SUCCESS
            
        except Exception as e:
            self.logger.error(f"Error parsing {url}: {str(e)}")
            if settings.DEBUG:
                import traceback
                self.logger.error(traceback.format_exc())
            self.stats['failed'] += 1
            return CrawlStatus.BLOCKED
    
    async def run_auto(
        self,
        count: int = 100
    ) -> Dict[str, Any]:
        """
        自動爬取最新文章
        
        ✅ FIX #WORK-ORDER-921: 使用來源專屬的 concurrent_limit
        ✅ FIX #WORK-ORDER-906: 加入詳細除錯 Log
        ✅ FIX #WORK-ORDER-902: 支援列表式爬取 (List-Based Crawling)
        
        策略：
        1. 呼叫 parser.get_latest_id() 取得最新 ID
        2. 🔍 Duck Typing 檢查：Parser 是否有 get_discovered_ids() 方法
        3. 情境 A (列表模式 - MOEA/E-Info)：
           - 呼叫 get_discovered_ids() 取得 valid_ids
           - 從 valid_ids 中切片取前 count 個
           - Log: "📋 List-based crawling: using X discovered IDs"
        4. 情境 B (流水號模式 - LTN/UDN/ChinaTimes/CNA)：
           - 使用 range(latest_id, latest_id - count, -1)
           - Log: "🔢 Range-based crawling: ID X → Y"
        
        Args:
            count: 要爬取的文章數量（預設 100）
            
        Returns:
            爬取結果統計
        """
        self.logger.info(f"🚀 Starting auto crawl: {count} articles")
        
        # 步驟 1：取得最新 ID
        latest_id = await self.parser.get_latest_id()
        if latest_id is None:
            self.logger.error("Failed to get latest ID")
            return {'error': 'Failed to get latest ID'}
        
        self.logger.info(f"   Latest ID: {latest_id:,}")
        
        # ✅ FIX #WORK-ORDER-906: 詳細除錯 Log
        self.logger.info(f"")
        self.logger.info(f"🔍 [Duck Typing Check] Inspecting Parser capabilities...")
        self.logger.info(f"   Parser class: {self.parser.__class__.__name__}")
        self.logger.info(f"   Parser source: {self.parser.source_name}")
        
        # 檢查 hasattr
        has_method = hasattr(self.parser, 'get_discovered_ids')
        self.logger.info(f"   hasattr(parser, 'get_discovered_ids'): {has_method}")
        
        if has_method:
            # 檢查 callable
            is_callable = callable(getattr(self.parser, 'get_discovered_ids'))
            self.logger.info(f"   callable(parser.get_discovered_ids): {is_callable}")
            
            if is_callable:
                # 檢查方法簽名
                method = getattr(self.parser, 'get_discovered_ids')
                self.logger.info(f"   Method object: {method}")
                self.logger.info(f"   Method type: {type(method)}")
        
        # ✅ FIX #WORK-ORDER-902: Duck Typing 檢查
        # 步驟 2：檢查 Parser 是否支援列表式爬取
        if hasattr(self.parser, 'get_discovered_ids') and callable(getattr(self.parser, 'get_discovered_ids')):
            # ========== 情境 A：列表模式 (MOEA/E-Info) ==========
            self.logger.info(f"")
            self.logger.info(f"✅ [Duck Typing] Detected list-based parser!")
            self.logger.info(f"   Calling parser.get_discovered_ids()...")
            
            # 取得已發現的 ID 列表
            try:
                valid_ids = self.parser.get_discovered_ids()
                self.logger.info(f"   ✅ get_discovered_ids() returned: {len(valid_ids) if valid_ids else 0} IDs")
                
                if valid_ids:
                    # 顯示前 10 個 ID（除錯用）
                    preview = valid_ids[:10]
                    self.logger.info(f"   📋 ID preview (first 10): {preview}")
                
            except Exception as e:
                self.logger.error(f"   ❌ get_discovered_ids() failed: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
                valid_ids = None
            
            if not valid_ids:
                self.logger.error("Parser returned empty ID list, falling back to range mode")
                # 降級為流水號模式
                start_id = latest_id
                end_id = latest_id - count + 1
                target_ids = list(range(start_id, end_id - 1, -1))
                self.logger.info(f"   🔢 Fallback range: ID {start_id:,} → {end_id:,}")
            else:
                # 切片取前 count 個
                target_ids = valid_ids[:count]
                
                self.logger.info(f"")
                self.logger.info(f"📋 List-based crawling mode activated!")
                self.logger.info(f"   Using {len(target_ids)} discovered IDs (from {len(valid_ids):,} total)")
                self.logger.info(f"   ID range: {target_ids[0]:,} ... {target_ids[-1]:,}")
            
        else:
            # ========== 情境 B：流水號模式 (LTN/UDN/ChinaTimes/CNA) ==========
            self.logger.info(f"")
            self.logger.info(f"ℹ️  [Duck Typing] No get_discovered_ids() method found")
            self.logger.info(f"   Falling back to range-based crawling")
            
            # 計算範圍
            start_id = latest_id
            end_id = latest_id - count + 1
            
            target_ids = list(range(start_id, end_id - 1, -1))
            
            self.logger.info(f"")
            self.logger.info(f"🔢 Range-based crawling mode activated!")
            self.logger.info(f"   ID range: {start_id:,} → {end_id:,}")
        
        # 步驟 3：執行爬取
        self.logger.info(f"")
        self.logger.info(f"🎯 Target: {len(target_ids)} articles")
        
        # 重置統計
        self.stats = {
            'total': len(target_ids),
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'not_found': 0,
            'blocked': 0,
        }
        
        # 創建會話
        if self.session is None:
            self.session = await self._create_session()
            need_close = True
        else:
            need_close = False
        
        # ✅ FIX #WORK-ORDER-921: 使用來源專屬的 concurrent_limit
        semaphore = asyncio.Semaphore(self.concurrent_limit)
        
        async def process_with_semaphore(article_id: int):
            async with semaphore:
                await self._random_delay()
                return await self._process_article(article_id, self.session)
        
        # 創建任務列表
        tasks = [process_with_semaphore(article_id) for article_id in target_ids]
        
        # 執行所有任務
        self.logger.info(f"📊 Processing {len(tasks)} articles with {self.concurrent_limit} concurrent requests")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 處理結果
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"Task exception for ID {target_ids[i]}: {result}")
                self.stats['blocked'] += 1
        
        # 關閉會話
        if need_close:
            await self.close()
        
        # 輸出統計
        self._log_stats()
        
        return self.stats
    
    async def run_range(
        self,
        start_id: int,
        end_id: int,
        reverse: bool = False
    ) -> Dict[str, Any]:
        """
        爬取指定範圍的文章 ID
        
        ✅ FIX #WORK-ORDER-921: 使用來源專屬的 concurrent_limit
        ✅ FIX #WORK-ORDER-912: 加入跳躍失敗除錯 Log
        ✅ FIX #WORK-ORDER-911: 修正智能跳躍計數邏輯與視覺化
        ✅ FIX #ENGINE-REFACTOR-V2: 優化跳躍判斷邏輯
        
        Args:
            start_id: 起始 ID
            end_id: 結束 ID
            reverse: 是否反向爬取（從 start_id 遞減到 end_id），預設 False
            
        Returns:
            爬取結果統計
        """
        # 確保 start_id <= end_id（當 reverse=False 時）
        if not reverse and start_id > end_id:
            start_id, end_id = end_id, start_id
        elif reverse and start_id < end_id:
            start_id, end_id = end_id, start_id
        
        # 生成 ID 列表
        if reverse:
            direction = "reverse"
            step = -1
        else:
            direction = "forward"
            step = 1
        
        self.logger.info(f"🚀 Starting crawl: ID {start_id:,} → {end_id:,} ({direction})")
        
        # ✅ FIX #WORK-ORDER-911: 顯示智能跳躍狀態（包含從 Settings 讀到的數值）
        if self._should_enable_smart_jump():
            self.logger.info(f"   Smart Jump: ENABLED (threshold: {self.SMART_JUMP_THRESHOLD})")
        else:
            self.logger.info(f"   Smart Jump: DISABLED (source: {self.parser.source_name})")
        
        # 重置統計
        total_range = abs(start_id - end_id) + 1
        self.stats = {
            'total': total_range,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'not_found': 0,
            'blocked': 0,
        }
        
        # 重置智能跳躍狀態
        self.consecutive_failures = 0
        self.smart_jump_count = 0
        
        # 創建會話
        if self.session is None:
            self.session = await self._create_session()
            need_close = True
        else:
            need_close = False
        
        # ✅ FIX #WORK-ORDER-921: 使用來源專屬的 concurrent_limit
        semaphore = asyncio.Semaphore(self.concurrent_limit)
        
        # 改用 while 迴圈支援智能跳躍
        current_id = start_id
        processed_count = 0
        
        async def process_with_semaphore(article_id: int):
            async with semaphore:
                await self._random_delay()
                return await self._process_article(article_id, self.session)
        
        # 創建任務佇列
        tasks = []
        task_ids = []
        
        while (not reverse and current_id <= end_id) or (reverse and current_id >= end_id):
            # 添加任務到佇列
            task = process_with_semaphore(current_id)
            tasks.append(task)
            task_ids.append(current_id)
            processed_count += 1
            
            # 正常遞增/遞減
            current_id += step
            
            # ✅ FIX #WORK-ORDER-911: 每 10 筆就處理一次（加快反應速度）
            if len(tasks) >= 10:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # ✅ FIX #WORK-ORDER-911: 修正計數邏輯
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        self.logger.error(f"Task exception for ID {task_ids[i]}: {result}")
                        self.stats['blocked'] += 1
                        # ✅ FIX: 異常也算失敗，避免卡死
                        self.consecutive_failures += 1
                        continue
                    
                    if result == CrawlStatus.SUCCESS:
                        # ✅ 成功：重置失敗計數
                        self.consecutive_failures = 0
                    
                    elif result == CrawlStatus.NOT_FOUND:
                        # ✅ 404：增加失敗計數
                        self.consecutive_failures += 1
                    
                    elif result == CrawlStatus.BLOCKED:
                        # ✅ FIX #WORK-ORDER-911: BLOCKED 也算失敗
                        # （針對連續無效 ID 導致的 Timeout/Error）
                        self.consecutive_failures += 1
                        self.logger.warning(f"🚫 Blocked/Error detected.")
                
                # ✅ FIX #WORK-ORDER-911: 視覺化計數器（讓老闆看到數字在跑）
                if self._should_enable_smart_jump() and self.consecutive_failures > 0:
                    self.logger.info(f"   ⚠️  Consecutive Failures: {self.consecutive_failures} / {self.SMART_JUMP_THRESHOLD}")
                
                # ✅ 智能跳躍檢查
                if (self.consecutive_failures >= self.SMART_JUMP_THRESHOLD and
                    self._should_enable_smart_jump()):
                    
                    # ✅ FIX #WORK-ORDER-911: 使用這一批最後一個 ID 來算
                    jump_target = self._calculate_jump_target(task_ids[-1])
                    
                    if jump_target is not None:
                        # 檢查跳躍目標是否在範圍內
                        if (not reverse and jump_target <= end_id) or (reverse and jump_target >= end_id):
                            self.logger.warning(f"")
                            self.logger.warning(f"🚀 [Smart Jump] Triggered! ({self.consecutive_failures} failures)")
                            self.logger.warning(f"   Current ID: {task_ids[-1]:,}")
                            self.logger.warning(f"   Jump target: {jump_target:,}")
                            self.logger.warning(f"   Reason: Consecutive failures threshold reached")
                            
                            current_id = jump_target
                            self.consecutive_failures = 0
                            self.smart_jump_count += 1
                            
                            # 清空任務並跳出當前批次處理
                            tasks = []
                            task_ids = []
                            continue
                        else:
                            # ✅ FIX #WORK-ORDER-912: 跳躍目標超出範圍
                            self.logger.warning(f"")
                            self.logger.warning(f"⚠️  [Smart Jump] Target out of range!")
                            self.logger.warning(f"   Current ID: {task_ids[-1]:,}")
                            self.logger.warning(f"   Jump target: {jump_target:,}")
                            self.logger.warning(f"   End ID: {end_id:,}")
                            self.logger.warning(f"   Reason: Jump target exceeds crawl range, stopping here")
                            # 不跳躍，繼續爬取直到 end_id
                    else:
                        # ✅ FIX #WORK-ORDER-912: 新增這行：告訴老闆為什麼沒跳
                        self.logger.warning(f"")
                        self.logger.warning(f"⚠️  [Smart Jump] Condition met but target is None!")
                        self.logger.warning(f"   Current ID: {task_ids[-1]:,}")
                        self.logger.warning(f"   Consecutive failures: {self.consecutive_failures}")
                        self.logger.warning(f"   Reason: Failed to parse date from ID or calculate jump target")
                        self.logger.warning(f"   Action: Continuing normal crawl (no jump)")
                
                # 清空任務佇列
                tasks = []
                task_ids = []
        
        # 處理剩餘任務
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 同樣的狀態處理邏輯
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Task exception for ID {task_ids[i]}: {result}")
                    self.stats['blocked'] += 1
                    self.consecutive_failures += 1
                    continue
                
                if result == CrawlStatus.SUCCESS:
                    self.consecutive_failures = 0
                elif result == CrawlStatus.NOT_FOUND:
                    self.consecutive_failures += 1
                elif result == CrawlStatus.BLOCKED:
                    self.consecutive_failures += 1
                    self.logger.warning(f"🚫 Blocked/Error detected.")
        
        # ✅ FIX #ENGINE-CLOSE-TIMEOUT: 關閉會話（加上 timeout 保護）
        if need_close:
            await self.close()
        
        # 輸出統計
        self._log_stats()
        
        # 輸出智能跳躍統計
        if self.smart_jump_count > 0:
            self.logger.info(f"")
            self.logger.info(f"🚀 Smart Jump Statistics:")
            self.logger.info(f"   Total jumps: {self.smart_jump_count}")
            self.logger.info(f"   Estimated time saved: ~{self.smart_jump_count * self.SMART_JUMP_THRESHOLD * 0.5:.1f}s")
        
        return self.stats
    
    def _log_stats(self) -> None:
        """
        輸出爬取統計資訊
        
        ✅ FIX #ENGINE-REFACTOR-V2: 新增 BLOCKED 統計
        """
        self.logger.info("=" * 60)
        self.logger.info("📊 Crawl Statistics:")
        self.logger.info(f"   Total:     {self.stats['total']:,}")
        self.logger.info(f"   Success:   {self.stats['success']:,} ✅")
        self.logger.info(f"   Failed:    {self.stats['failed']:,} ❌")
        self.logger.info(f"   Skipped:   {self.stats['skipped']:,} ⏭️")
        self.logger.info(f"   Not Found: {self.stats['not_found']:,} 🔍")
        self.logger.info(f"   Blocked:   {self.stats['blocked']:,} 🚫")
        
        if self.stats['total'] > 0:
            success_rate = (self.stats['success'] / self.stats['total']) * 100
            self.logger.info(f"   Success Rate: {success_rate:.2f}%")
        
        self.logger.info("=" * 60)
    
    async def close(self):
        """
        關閉 Engine 並清理資源
        
        ✅ FIX #WORK-ORDER-902: 移除 Pipeline.close() 呼叫
        ✅ FIX #ENGINE-CLOSE-TIMEOUT: 加上 timeout 保護，避免 curl_cffi 卡住
        """
        try:
            if self.session is not None:
                # ✅ 加上 timeout 保護
                await asyncio.wait_for(
                    self.session.close(),
                    timeout=5.0
                )
        except asyncio.TimeoutError:
            self.logger.warning("⚠️  Session close timeout, forcing shutdown")
        except Exception as e:
            self.logger.error(f"❌ Error closing session: {e}")
        finally:
            self.session = None
            self.logger.info("✅ Engine closed")
        
        # ✅ FIX #WORK-ORDER-902: 移除 Pipeline.close() 呼叫
        # 原因：Pipeline 類別沒有定義 close() 方法
        # TSVWriter 在寫入後會自動關閉檔案，不需要顯式關閉
    
    async def run_list(
        self,
        url_list: List[str]
    ) -> Dict[str, Any]:
        """
        爬取指定的 URL 列表
        
        ✅ FIX #WORK-ORDER-921: 使用來源專屬的 concurrent_limit
        
        Args:
            url_list: URL 列表
            
        Returns:
            爬取結果統計
        """
        self.logger.info(f"🚀 Starting crawl: {len(url_list)} URLs")
        
        # 重置統計
        self.stats = {
            'total': len(url_list),
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'not_found': 0,
            'blocked': 0,
        }
        
        # 創建會話
        if self.session is None:
            self.session = await self._create_session()
            need_close = True
        else:
            need_close = False
        
        # ✅ FIX #WORK-ORDER-921: 使用來源專屬的 concurrent_limit
        semaphore = asyncio.Semaphore(self.concurrent_limit)
        
        async def process_url(url: str):
            async with semaphore:
                await self._random_delay()
                
                # 檢查是否已爬取
                if self._is_crawled(url):
                    self.logger.debug(f"⏭️  Skipping already crawled: {url}")
                    self.stats['skipped'] += 1
                    return CrawlStatus.SUCCESS
                
                # 獲取 HTML
                html, status = await self._fetch(url, self.session)
                
                if status == CrawlStatus.NOT_FOUND:
                    self.stats['not_found'] += 1
                    return status
                
                elif status == CrawlStatus.BLOCKED:
                    self.stats['blocked'] += 1
                    return status
                
                if html is None:
                    self.stats['failed'] += 1
                    return CrawlStatus.BLOCKED
                
                # 解析 HTML
                try:
                    data = await self.parser.parse(html, url)
                    if data is None:
                        self.stats['failed'] += 1
                        return CrawlStatus.NOT_FOUND
                    
                    # 標記為已爬取
                    self._mark_as_crawled(url)
                    
                    # 自動儲存
                    if self.auto_save:
                        success = await self.pipeline.process_and_save(url, data)
                        if success:
                            self.logger.info(f"✅ Saved: {url}")
                            self.stats['success'] += 1
                        else:
                            self.logger.error(f"❌ Failed to save: {url}")
                            self.stats['failed'] += 1
                    else:
                        self.stats['success'] += 1
                    
                    return CrawlStatus.SUCCESS
                    
                except Exception as e:
                    self.logger.error(f"Error parsing {url}: {str(e)}")
                    if settings.DEBUG:
                        import traceback
                        self.logger.error(traceback.format_exc())
                    self.stats['failed'] += 1
                    return CrawlStatus.BLOCKED
        
        # 創建任務列表
        tasks = [process_url(url) for url in url_list]
        
        # 執行所有任務
        self.logger.info(f"📊 Processing {len(tasks)} URLs with {self.concurrent_limit} concurrent requests")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # ✅ FIX #ENGINE-CLOSE-TIMEOUT: 關閉會話（加上 timeout 保護）
        if need_close:
            await self.close()
        
        # 輸出統計
        self._log_stats()
        
        return self.stats
