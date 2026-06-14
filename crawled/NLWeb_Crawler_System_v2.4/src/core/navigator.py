import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, Callable, Awaitable
from pathlib import Path

from config import settings

class DateNavigator:
    """
    日期導航器
    使用二分搜尋演算法定位目標日期對應的文章 ID
    
    ✅ FIX #NAVIGATOR-SCALING-FIX: 智能範圍估算
    ✅ FIX #NAVIGATOR-DIRECT-CALC: 中時 ID 直算邏輯（Turbo Mode）
    ✅ FIX #NAVIGATOR-CNA-SUPPORT: 支援中央社 12 碼 ID
    
    支援兩種 ID 格式：
    1. 流水號型 (Sequential)：如 LTN (4567890) - 使用二分搜尋
    2. 日期型 (Date-based)：如 ChinaTimes (20251212001234)、CNA (202512290031) - 直接計算
    """
    
    # ID 類型判斷閾值
    ID_TYPE_THRESHOLD = 10_000_000  # 1000 萬
    
    # 搜尋範圍設定
    SEQUENTIAL_SEARCH_RANGE = 1_000_000  # 流水號型回溯範圍（100 萬）
    DATE_BASED_SEARCH_MARGIN = 10_000_000  # 日期型安全邊界（1000 萬）
    
    # ✅ FIX #NAVIGATOR-CNA-SUPPORT: 加入 CNA
    TURBO_MODE_SOURCES = ['chinatimes', 'cna']  # 支援直算的來源
    
    def __init__(
        self,
        parser_get_date: Callable[[int], Awaitable[Optional[datetime]]],
        source_name: str = "unknown"
    ):
        """
        初始化日期導航器
        
        Args:
            parser_get_date: Parser 提供的獲取日期函式，接收 article_id，返回日期或 None
            source_name: 新聞來源名稱（用於日誌）
        """
        self.parser_get_date = parser_get_date
        self.source_name = source_name
        self.logger = logging.getLogger(f"{self.__class__.__name__}_{source_name}")
        
        # 設定日誌處理器
        if not self.logger.handlers:
            self._setup_logger()
        
        # 二分搜尋設定
        self.max_search_iterations = 50  # 二分搜尋最大迭代次數
        self.max_skip_attempts = 10  # 遇到空號時最大嘗試次數
        self.search_tolerance_days = 1  # 搜尋容忍度（天）
    
    def _setup_logger(self) -> None:
        """設置日誌處理器"""
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        
        import time
        log_file = settings.LOG_DIR / f"navigator_{self.source_name}_{time.strftime('%Y%m%d')}.log"
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
    
    def _detect_id_format(self, article_id: int) -> str:
        """
        偵測 ID 格式類型
        
        策略：
        1. 如果 ID < 10,000,000：假設是流水號（LTN 類型）
        2. 如果 ID >= 10,000,000 且前 8 位可解析為日期：日期型（ChinaTimes/CNA 類型）
        3. 否則：流水號（保守策略）
        
        Args:
            article_id: 文章 ID
            
        Returns:
            'sequential' 或 'date_based'
        """
        if article_id < self.ID_TYPE_THRESHOLD:
            return 'sequential'
        
        # 嘗試解析前 8 位為日期
        try:
            id_str = str(article_id)
            if len(id_str) >= 8:
                date_str = id_str[:8]
                datetime.strptime(date_str, '%Y%m%d')
                return 'date_based'
        except (ValueError, IndexError):
            pass
        
        # 無法確定，使用保守策略
        return 'sequential'
    
    def _parse_date_from_id(self, article_id: int) -> Optional[datetime]:
        """
        從日期型 ID 中解析日期
        
        Args:
            article_id: 文章 ID（如 20251212001234 或 202512290031）
            
        Returns:
            解析出的日期，或 None（如果無法解析）
        """
        try:
            id_str = str(article_id)
            date_str = id_str[:8]  # YYYYMMDD
            return datetime.strptime(date_str, '%Y%m%d')
        except (ValueError, IndexError):
            return None
    
    def _is_turbo_mode_enabled(self) -> bool:
        """
        判斷是否啟用 Turbo Mode（直算邏輯）
        
        ✅ FIX #NAVIGATOR-CNA-SUPPORT: 支援 ChinaTimes 和 CNA
        
        Returns:
            True 如果應該使用直算邏輯
        """
        return self.source_name in self.TURBO_MODE_SOURCES
    
    def _calculate_id_range_direct(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Tuple[int, int]:
        """
        直接計算日期範圍對應的 ID 範圍（Turbo Mode）
        
        ✅ FIX #NAVIGATOR-CNA-SUPPORT: 支援不同流水號長度
        
        策略：
        - CNA: YYYYMMDD + 0001-9999 (4碼)
        - ChinaTimes: YYYYMMDD + 000001-999999 (6碼)
        
        Args:
            start_date: 開始日期
            end_date: 結束日期
            
        Returns:
            (start_id, end_id)
        """
        # ✅ FIX #NAVIGATOR-CNA-SUPPORT: 根據來源選擇流水號長度
        if self.source_name == 'cna':
            # 中央社：YYYYMMDD + 4碼 (0001 ~ 9999)
            start_suffix = '0001'
            end_suffix = '9999'
            id_length = 12
        else:
            # 預設 (如中時)：YYYYMMDD + 6碼 (000001 ~ 999999)
            start_suffix = '000001'
            end_suffix = '999999'
            id_length = 14
        
        # 構建 ID
        start_str = start_date.strftime('%Y%m%d') + start_suffix
        end_str = end_date.strftime('%Y%m%d') + end_suffix
        
        start_id = int(start_str)
        end_id = int(end_str)
        
        self.logger.info(f"⚡️ [Turbo Mode] Direct calculation enabled")
        self.logger.info(f"   Source: {self.source_name} (ID length: {id_length})")
        self.logger.info(f"   Start date: {start_date.strftime('%Y-%m-%d')} → ID: {start_id:,}")
        self.logger.info(f"   End date:   {end_date.strftime('%Y-%m-%d')} → ID: {end_id:,}")
        self.logger.info(f"   Total range: {end_id - start_id + 1:,} IDs")
        self.logger.info(f"   Skipped binary search (instant calculation)")
        
        return (start_id, end_id)
    
    def _estimate_search_range(
        self,
        latest_id: int,
        target_date: datetime
    ) -> Tuple[int, int]:
        """
        智能估算搜尋範圍
        
        ✅ FIX #NAVIGATOR-SCALING-FIX 核心邏輯
        ✅ FIX #NAVIGATOR-CNA-SUPPORT: 支援不同流水號長度
        
        策略：
        1. 流水號型（LTN/UDN）：使用固定回溯範圍（100 萬）
        2. 日期型（ChinaTimes/CNA）：從 ID 中解析日期，構建精確範圍
        
        Args:
            latest_id: 最新文章 ID
            target_date: 目標日期
            
        Returns:
            (lower_bound, upper_bound) 搜尋範圍
        """
        id_format = self._detect_id_format(latest_id)
        
        if id_format == 'sequential':
            # ========== 流水號型（LTN/UDN）==========
            lower_bound = max(0, latest_id - self.SEQUENTIAL_SEARCH_RANGE)
            upper_bound = latest_id
            
            self.logger.info(f"📊 Detected sequential ID format (LTN/UDN-like)")
            self.logger.info(f"   Latest ID: {latest_id:,}")
            self.logger.info(f"   Search range: [{lower_bound:,}, {upper_bound:,}]")
            self.logger.info(f"   Range size: {upper_bound - lower_bound:,} IDs")
            
        else:
            # ========== 日期型（ChinaTimes/CNA）==========
            id_date = self._parse_date_from_id(latest_id)
            
            if id_date is None:
                # 無法解析，退回到保守策略
                self.logger.warning(f"⚠️  Could not parse date from ID {latest_id}, using conservative range")
                lower_bound = max(0, latest_id - self.DATE_BASED_SEARCH_MARGIN)
                upper_bound = latest_id
            else:
                # 構建目標日期的 ID 範圍
                target_date_str = target_date.strftime('%Y%m%d')
                
                # ✅ FIX #NAVIGATOR-CNA-SUPPORT: 根據來源選擇流水號長度
                if self.source_name == 'cna':
                    # 中央社：4 碼流水號
                    lower_bound = int(target_date_str + '0000')
                    upper_bound = int(target_date_str + '9999')
                else:
                    # 預設 (如中時)：6 碼流水號
                    lower_bound = int(target_date_str + '000000')
                    upper_bound = int(target_date_str + '999999')
                
                self.logger.info(f"📊 Detected date-based ID format (ChinaTimes/CNA-like)")
                self.logger.info(f"   Latest ID: {latest_id:,} (Date: {id_date.strftime('%Y-%m-%d')})")
                self.logger.info(f"   Target date: {target_date.strftime('%Y-%m-%d')}")
                self.logger.info(f"   Search range: [{lower_bound:,}, {upper_bound:,}]")
                self.logger.info(f"   Range size: {upper_bound - lower_bound:,} IDs (~1 day)")
        
        return (lower_bound, upper_bound)
    
    async def find_article_by_date(
        self,
        target_date: datetime,
        min_id: int,
        max_id: int
    ) -> Optional[int]:
        """
        使用二分搜尋找到最接近目標日期的文章 ID
        
        Args:
            target_date: 目標日期
            min_id: 搜尋範圍最小 ID
            max_id: 搜尋範圍最大 ID
            
        Returns:
            找到的文章 ID，或 None（如果搜尋失敗）
        """
        self.logger.info(f"🔍 Starting binary search for date: {target_date.strftime('%Y-%m-%d')}")
        self.logger.info(f"   Search range: [{min_id:,}, {max_id:,}]")
        
        left = min_id
        right = max_id
        best_match_id = None
        best_match_diff = float('inf')
        
        iteration = 0
        
        while left <= right and iteration < self.max_search_iterations:
            iteration += 1
            mid = (left + right) // 2
            
            self.logger.debug(f"Iteration {iteration}: Checking ID {mid:,} (range: [{left:,}, {right:,}])")
            
            # 獲取中間 ID 的日期
            mid_date = await self._get_valid_date(mid)
            
            if mid_date is None:
                self.logger.warning(f"⚠️  Could not get valid date around ID {mid:,}, narrowing search range")
                # 無法獲取有效日期，縮小搜尋範圍
                if right - left <= 1:
                    break
                # 嘗試搜尋右半部
                left = mid + 1
                continue
            
            # 計算日期差異
            date_diff = (mid_date - target_date).total_seconds()
            abs_diff = abs(date_diff)
            
            self.logger.debug(f"   ID {mid:,} -> Date: {mid_date.strftime('%Y-%m-%d %H:%M:%S')}, Diff: {date_diff / 86400:.2f} days")
            
            # 更新最佳匹配
            if abs_diff < best_match_diff:
                best_match_diff = abs_diff
                best_match_id = mid
                self.logger.info(f"✨ New best match: ID {mid:,}, Date: {mid_date.strftime('%Y-%m-%d')}, Diff: {abs_diff / 86400:.2f} days")
            
            # 檢查是否已足夠接近
            if abs_diff <= self.search_tolerance_days * 86400:  # 轉換為秒
                self.logger.info(f"✅ Found close match within tolerance: ID {mid:,}")
                return mid
            
            # 調整搜尋範圍
            if date_diff > 0:
                # mid_date 在 target_date 之後，搜尋左半部
                right = mid - 1
            else:
                # mid_date 在 target_date 之前，搜尋右半部
                left = mid + 1
        
        if best_match_id is not None:
            self.logger.info(f"🎯 Binary search completed: Best match ID {best_match_id:,}, Diff: {best_match_diff / 86400:.2f} days")
            return best_match_id
        else:
            self.logger.error(f"❌ Binary search failed: No valid article found in range [{min_id:,}, {max_id:,}]")
            return None
    
    async def _get_valid_date(self, article_id: int) -> Optional[datetime]:
        """
        獲取有效的文章日期，處理 ID 失效的情況
        
        Args:
            article_id: 文章 ID
            
        Returns:
            文章日期，或 None（如果無法獲取）
        """
        # 首先嘗試原始 ID
        date = await self.parser_get_date(article_id)
        if date is not None:
            return date
        
        self.logger.debug(f"ID {article_id:,} is invalid, trying nearby IDs...")
        
        # 如果原始 ID 失效，嘗試附近的 ID
        for offset in range(1, self.max_skip_attempts + 1):
            # 嘗試 +offset
            try_id_plus = article_id + offset
            date = await self.parser_get_date(try_id_plus)
            if date is not None:
                self.logger.debug(f"   Found valid ID: {try_id_plus:,} (original + {offset})")
                return date
            
            # 嘗試 -offset
            try_id_minus = article_id - offset
            if try_id_minus > 0:
                date = await self.parser_get_date(try_id_minus)
                if date is not None:
                    self.logger.debug(f"   Found valid ID: {try_id_minus:,} (original - {offset})")
                    return date
        
        self.logger.warning(f"⚠️  Could not find valid article near ID {article_id:,} (tried ±{self.max_skip_attempts})")
        return None
    
    async def find_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        min_id: Optional[int] = None,
        max_id: Optional[int] = None
    ) -> Optional[Tuple[int, int]]:
        """
        找到日期範圍對應的文章 ID 範圍
        
        ✅ FIX #NAVIGATOR-SCALING-FIX: 支援智能範圍估算
        ✅ FIX #NAVIGATOR-DIRECT-CALC: 支援 Turbo Mode（直算邏輯）
        ✅ FIX #NAVIGATOR-CNA-SUPPORT: 支援中央社 12 碼 ID
        
        Args:
            start_date: 開始日期
            end_date: 結束日期
            min_id: 搜尋範圍最小 ID（可選，未提供則自動估算）
            max_id: 搜尋範圍最大 ID（可選，未提供則需要從外部獲取）
            
        Returns:
            (start_id, end_id) 或 None（如果搜尋失敗）
        """
        self.logger.info(f"🔍 Finding ID range for date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        
        # ✅ FIX #NAVIGATOR-DIRECT-CALC: Turbo Mode 優先
        if self._is_turbo_mode_enabled():
            return self._calculate_id_range_direct(start_date, end_date)
        
        # ========== 傳統模式（二分搜尋）==========
        
        # 檢查 max_id 是否提供
        if max_id is None:
            self.logger.error("❌ max_id is required for range estimation")
            self.logger.error("   Please provide max_id (e.g., latest article ID)")
            return None
        
        # 智能估算搜尋範圍
        # 為開始日期估算範圍
        start_min_id, start_max_id = self._estimate_search_range(max_id, start_date)
        
        # 找到開始日期對應的 ID
        self.logger.info(f"")
        self.logger.info(f"📍 Locating start date: {start_date.strftime('%Y-%m-%d')}")
        start_id = await self.find_article_by_date(start_date, start_min_id, start_max_id)
        if start_id is None:
            self.logger.error("❌ Could not find start ID")
            return None
        
        # 為結束日期估算範圍
        end_min_id, end_max_id = self._estimate_search_range(max_id, end_date)
        
        # 找到結束日期對應的 ID
        self.logger.info(f"")
        self.logger.info(f"📍 Locating end date: {end_date.strftime('%Y-%m-%d')}")
        end_id = await self.find_article_by_date(end_date, end_min_id, end_max_id)
        if end_id is None:
            self.logger.error("❌ Could not find end ID")
            return None
        
        # 確保 start_id <= end_id
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        
        self.logger.info(f"")
        self.logger.info(f"✅ Found ID range: [{start_id:,}, {end_id:,}]")
        self.logger.info(f"   Total articles: ~{end_id - start_id + 1:,}")
        
        return (start_id, end_id)
    
    async def estimate_id_range(
        self,
        sample_ids: list[int],
        target_date: datetime
    ) -> Optional[Tuple[int, int]]:
        """
        基於樣本 ID 估算目標日期的 ID 範圍
        
        Args:
            sample_ids: 樣本 ID 列表（至少需要 2 個）
            target_date: 目標日期
            
        Returns:
            估算的 (min_id, max_id) 範圍，或 None（如果估算失敗）
        """
        if len(sample_ids) < 2:
            self.logger.error("❌ Need at least 2 sample IDs for estimation")
            return None
        
        self.logger.info(f"📊 Estimating ID range for {target_date.strftime('%Y-%m-%d')} based on {len(sample_ids)} samples")
        
        # 收集有效的樣本點
        samples = []
        for sample_id in sample_ids:
            date = await self._get_valid_date(sample_id)
            if date is not None:
                samples.append((sample_id, date))
        
        if len(samples) < 2:
            self.logger.error("❌ Not enough valid samples for estimation")
            return None
        
        # 按日期排序
        samples.sort(key=lambda x: x[1])
        
        # 計算平均每日 ID 增長率
        total_id_diff = samples[-1][0] - samples[0][0]
        total_time_diff = (samples[-1][1] - samples[0][1]).total_seconds()
        
        if total_time_diff <= 0:
            self.logger.error("❌ Invalid time range in samples")
            return None
        
        ids_per_second = total_id_diff / total_time_diff
        ids_per_day = ids_per_second * 86400
        
        self.logger.info(f"   Estimated growth rate: {ids_per_day:,.2f} IDs per day")
        
        # 基於最近的樣本點估算目標日期的 ID
        closest_sample = min(samples, key=lambda x: abs((x[1] - target_date).total_seconds()))
        time_diff_seconds = (target_date - closest_sample[1]).total_seconds()
        estimated_id = int(closest_sample[0] + (ids_per_second * time_diff_seconds))
        
        # 設定搜尋範圍（±3 天作為安全邊界）
        range_margin = int(abs(ids_per_day) * 3)  # 3 天的範圍
        min_id = max(1, estimated_id - range_margin)
        max_id = estimated_id + range_margin
        
        self.logger.info(f"   Estimated ID: {estimated_id:,}")
        self.logger.info(f"   Search range: [{min_id:,}, {max_id:,}] (±3 days)")
        
        return (min_id, max_id)
