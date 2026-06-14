# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Time Range Extractor - Hybrid Temporal Parsing Module

Extracts time ranges from queries using a 3-tier approach:
1. Regex Pattern Matching (fast, high precision)
2. LLM Parsing (handles complex expressions)
3. Keyword Fallback (backward compatible)

Examples:
- "過去三天的新聞" → {relative_days: 3, start_date: '2025-01-07', end_date: '2025-01-10'}
- "last week's articles" → {relative_days: 7, ...}
- "since the election" → {type: 'event-based', event_anchor: 'election'}
- "最近的AI發展" → {relative_days: 365, confidence: 0.5} (keyword fallback)
"""

import re
import calendar
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from misc.logger.logging_config_helper import get_configured_logger
from core.llm import ask_llm

logger = get_configured_logger("time_range_extractor")

# Chinese number mapping for temporal expressions (e.g., "近三天", "過去兩年")
CHINESE_NUMBERS = {
    '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10
}


def parse_number(s):
    """解析阿拉伯數字或中文數字（含組合如十五、二十、二十五）"""
    if s.isdigit():
        return int(s)
    if s in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[s]
    # 組合數字：十五=15, 二十=20, 二十五=25
    if '十' in s:
        parts = s.split('十')
        tens = CHINESE_NUMBERS.get(parts[0], 1) if parts[0] else 1
        ones = CHINESE_NUMBERS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    try:
        return int(s)  # fallback for unrecognised patterns
    except (ValueError, TypeError):
        return 1  # safe default if unparseable


class TimeRangeExtractor:
    """
    Extracts temporal information from queries using hybrid parsing.

    Inherits from the handler's pre-check pattern to integrate with existing infrastructure.
    """

    STEP_NAME = "TimeRangeExtractor"

    # Regex patterns for common temporal expressions (bilingual)
    REGEX_PATTERNS = {
        # Chinese - Relative time (days)
        'past_x_days_zh': r'(?:過去|近)\s*([一二兩三四五六七八九十\d]+)\s*天',
        'last_x_days_zh': r'(?:最近|近)\s*([一二兩三四五六七八九十\d]+)\s*天',
        'yesterday_zh': r'昨天',
        'today_zh': r'今天',

        # Chinese - Relative time (weeks)
        'past_x_weeks_zh': r'(?:過去|近)\s*([一二兩三四五六七八九十\d]+)\s*(?:週|周|星期)',
        'last_x_weeks_zh': r'(?:最近|近)\s*([一二兩三四五六七八九十\d]+)\s*(?:週|周|星期)',
        'this_week_zh': r'(?:本週|這週|本周|這周)',

        # Chinese - Relative time (months)
        'past_x_months_zh': r'(?:過去|近)\s*([一二兩三四五六七八九十\d]+)\s*(?:個月|月)',
        'last_x_months_zh': r'(?:近|最近)\s*([一二兩三四五六七八九十\d]+)\s*(?:個月|月)',
        'this_month_zh': r'(?:本月|這個月)',

        # Chinese - Relative time (years) - NEW
        'past_x_years_zh': r'(?:過去|近)\s*([一二兩三四五六七八九十\d]+)\s*年',
        'last_x_years_zh': r'(?:近|最近)\s*([一二兩三四五六七八九十\d]+)\s*年',
        'this_year_zh': r'(?:今年|本年)',
        'last_year_zh': r'去年',

        # English - Relative time (days)
        'past_x_days_en': r'past\s+(\d+)\s+days?',
        'last_x_days_en': r'last\s+(\d+)\s+days?',
        'yesterday_en': r'yesterday',
        'today_en': r'today',

        # English - Relative time (weeks)
        'past_x_weeks_en': r'past\s+(\d+)\s+weeks?',
        'last_x_weeks_en': r'last\s+(\d+)\s+weeks?',
        'last_week_en': r'last\s+week',
        'this_week_en': r'this\s+week',

        # English - Relative time (months)
        'past_x_months_en': r'past\s+(\d+)\s+months?',
        'last_x_months_en': r'last\s+(\d+)\s+months?',
        'last_month_en': r'last\s+month',
        'this_month_en': r'this\s+month',

        # English - Relative time (years) - NEW
        'past_x_years_en': r'past\s+(\d+)\s+years?',
        'last_x_years_en': r'last\s+(\d+)\s+years?',
        'last_year_en': r'last\s+year',
        'this_year_en': r'this\s+year',

        # Absolute dates
        'iso_date': r'\d{4}-\d{2}-\d{2}',
        'yyyy_mm': r'\d{4}年\d{1,2}月',
    }

    # Keyword fallback (existing MVP system - backward compatible)
    FALLBACK_KEYWORDS = [
        '最新', '最近', '近期', '新', '現在', '目前', '當前',
        'latest', 'recent', 'new', 'current', 'now'
    ]

    def __init__(self, handler):
        """
        Initialize TimeRangeExtractor.

        Args:
            handler: The query handler (NLWebHandler or subclass)
        """
        self.handler = handler
        self.handler.state.start_precheck_step(self.STEP_NAME)

        logger.info(f"[TIME-EXTRACTOR] Initializing for query: {handler.query}")

    async def do(self) -> Optional[Dict]:
        """
        Main extraction method using 3-tier approach.

        Returns:
            Dict with temporal information or None
        """
        try:
            query = self.handler.query

            # Stage 0: Check query_params for explicit time_range_start/end
            from core.utils.utils import get_param
            time_range_start = get_param(self.handler.query_params, "time_range_start", str, None)
            time_range_end = get_param(self.handler.query_params, "time_range_end", str, None)
            user_selected_time = get_param(self.handler.query_params, "user_selected_time", str, None)
            user_time_label = get_param(self.handler.query_params, "user_time_label", str, None)

            if time_range_start and time_range_end:
                # Explicit time range provided by frontend (from clarification)
                result = {
                    'method': 'explicit_params',
                    'is_temporal': True,
                    'start_date': time_range_start,
                    'end_date': time_range_end,
                    'confidence': 1.0,
                    # NEW: Mark as user-selected for BINDING constraint in Analyst prompt
                    'user_selected': user_selected_time in ['true', 'True', '1', True],
                    'user_choice_label': user_time_label or ''
                }
                logger.info(f"[TIME-EXTRACTOR] Explicit params (user_selected={result['user_selected']}): {result}")
                self.handler.temporal_range = result
                await self.handler.state.precheck_step_done(self.STEP_NAME)
                return result

            # Stage 1: Regex Pattern Matching (fast path)
            result = self._try_regex_parsing(query)
            if result and result.get('is_temporal'):
                logger.info(f"[TIME-EXTRACTOR] Regex match: {result}")
                self.handler.temporal_range = result
                await self.handler.state.precheck_step_done(self.STEP_NAME)
                return result

            # Stage 2: LLM Parsing (complex expressions)
            result = await self._try_llm_parsing(query)
            if result and result.get('is_temporal') and result.get('confidence', 0) >= 0.7:
                logger.info(f"[TIME-EXTRACTOR] LLM parse: {result}")
                self.handler.temporal_range = result
                await self.handler.state.precheck_step_done(self.STEP_NAME)
                return result

            # Stage 3: Keyword Fallback (backward compatible)
            result = self._try_keyword_fallback(query)
            if result.get('is_temporal'):
                logger.info(f"[TIME-EXTRACTOR] Keyword fallback: {result}")
            else:
                logger.info(f"[TIME-EXTRACTOR] No temporal indicators found")

            self.handler.temporal_range = result
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return result

        except Exception as e:
            logger.error(f"[TIME-EXTRACTOR] Error: {e}", exc_info=True)
            # Fail gracefully - return non-temporal
            result = {'method': 'error', 'is_temporal': False}
            self.handler.temporal_range = result
            await self.handler.state.precheck_step_done(self.STEP_NAME)
            return result

    def _try_regex_parsing(self, query: str) -> Optional[Dict]:
        """
        Stage 1: Try regex pattern matching for common temporal expressions.

        Args:
            query: The search query

        Returns:
            Dict with temporal info if matched, None otherwise
        """
        today = datetime.now(timezone.utc)

        # Try each pattern
        for pattern_name, pattern in self.REGEX_PATTERNS.items():
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                try:
                    # Parse based on pattern type
                    if 'past_x_days' in pattern_name or 'last_x_days' in pattern_name:
                        days = parse_number(match.group(1))
                        start_date = today - timedelta(days=days)
                        return self._build_result('regex', True, start_date, today, days,
                                                 match.group(0), confidence=1.0)

                    elif 'past_x_weeks' in pattern_name or 'last_x_weeks' in pattern_name:
                        weeks = parse_number(match.group(1))
                        days = weeks * 7
                        start_date = today - timedelta(days=days)
                        return self._build_result('regex', True, start_date, today, days,
                                                 match.group(0), confidence=1.0)

                    elif 'past_x_months' in pattern_name or 'last_x_months' in pattern_name:
                        months = parse_number(match.group(1))
                        days = months * 30  # Approximate
                        start_date = today - timedelta(days=days)
                        return self._build_result('regex', True, start_date, today, days,
                                                 match.group(0), confidence=0.9)

                    elif 'past_x_years' in pattern_name or 'last_x_years' in pattern_name:
                        # Handle "近兩年", "過去3年", "last 2 years" etc.
                        years = parse_number(match.group(1))
                        days = years * 365  # Approximate (ignoring leap years)
                        start_date = today - timedelta(days=days)
                        return self._build_result('regex', True, start_date, today, days,
                                                 match.group(0), confidence=0.95)

                    elif 'this_year' in pattern_name:
                        # From Jan 1 of current year to today
                        start_date = today.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                        days = (today - start_date).days + 1
                        return self._build_result('regex', True, start_date, today, days,
                                                 match.group(0), confidence=0.95)

                    elif 'last_year' in pattern_name:
                        # Full previous year
                        last_year = today.year - 1
                        start_date = today.replace(year=last_year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                        end_date = today.replace(year=last_year, month=12, day=31, hour=23, minute=59, second=59, microsecond=0)
                        days = 365
                        return self._build_result('regex', True, start_date, end_date, days,
                                                 match.group(0), confidence=0.95)

                    elif 'yesterday' in pattern_name:
                        start_date = today - timedelta(days=1)
                        return self._build_result('regex', True, start_date, today, 1,
                                                 match.group(0), confidence=1.0)

                    elif 'today' in pattern_name:
                        start_date = today.replace(hour=0, minute=0, second=0, microsecond=0)
                        return self._build_result('regex', True, start_date, today, 1,
                                                 match.group(0), confidence=1.0)

                    elif 'this_week' in pattern_name:
                        days = today.weekday()  # Days since Monday
                        start_date = today - timedelta(days=days)
                        return self._build_result('regex', True, start_date, today, days + 1,
                                                 match.group(0), confidence=0.95)

                    elif 'last_week' in pattern_name:
                        days = today.weekday() + 7  # Last week Monday
                        start_date = today - timedelta(days=days)
                        end_date = start_date + timedelta(days=7)
                        return self._build_result('regex', True, start_date, end_date, 7,
                                                 match.group(0), confidence=0.95)

                    elif 'this_month' in pattern_name:
                        start_date = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                        days = (today - start_date).days + 1
                        return self._build_result('regex', True, start_date, today, days,
                                                 match.group(0), confidence=0.9)

                    elif 'last_month' in pattern_name:
                        # First day of current month
                        first_of_month = today.replace(day=1)
                        # Last day of previous month
                        end_date = first_of_month - timedelta(days=1)
                        # First day of previous month
                        start_date = end_date.replace(day=1)
                        days = (end_date - start_date).days + 1
                        return self._build_result('regex', True, start_date, end_date, days,
                                                 match.group(0), confidence=0.9)

                    elif 'iso_date' in pattern_name:
                        # Exact date match
                        date_str = match.group(0)
                        parsed_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                        end_date = parsed_date + timedelta(days=1)
                        return self._build_result('regex', True, parsed_date, end_date, 1,
                                                 date_str, confidence=1.0)

                    elif 'yyyy_mm' in pattern_name:
                        # Absolute year+month: "2025年12月" → 2025-12-01 to 2025-12-31
                        matched_text = match.group(0)
                        year_match = re.search(r'(\d{4})年(\d{1,2})月', matched_text)
                        if year_match:
                            year = int(year_match.group(1))
                            month = int(year_match.group(2))
                            _, last_day = calendar.monthrange(year, month)
                            start_date = datetime(year, month, 1, tzinfo=timezone.utc)
                            end_date = datetime(year, month, last_day, tzinfo=timezone.utc)
                            days = last_day
                            return self._build_result('regex', True, start_date, end_date, days,
                                                     matched_text, confidence=1.0)

                    else:
                        # Pattern matched but no handler exists — this is a bug
                        logger.warning(
                            f"[TIME-EXTRACTOR] Regex pattern '{pattern_name}' matched but has no handler"
                            f" - this is a bug"
                        )
                        continue

                except (ValueError, IndexError) as e:
                    logger.warning(f"[TIME-EXTRACTOR] Failed to parse match for {pattern_name}: {e}")
                    continue

        return None

    async def _try_llm_parsing(self, query: str) -> Optional[Dict]:
        """
        Stage 2: Use LLM to parse complex temporal expressions.

        Args:
            query: The search query

        Returns:
            Dict with temporal info if successful, None otherwise
        """
        try:
            current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

            prompt = f"""從查詢中擷取時間範圍："{query}"

今天日期：{current_date}

分析查詢並判斷：
1. 是否含時間條件？（yes/no）
2. 類型：'relative'（如「最近 3 天」）、'absolute'（如「2024-01-15」）、'event-based'（如「選舉以來」）、或 'none'
3. 若為 relative：距今幾天？
4. 若為 event-based：錨定事件為何？

只回傳 JSON 結構，不需解釋。"""

            response_structure = {
                "is_temporal": "boolean - true if query has time constraint",
                "type": "string - 'relative', 'absolute', 'event-based', or 'none'",
                "relative_days": "integer or null - days back from today if relative",
                "start_date": "string (YYYY-MM-DD) or null - calculated start date",
                "end_date": "string (YYYY-MM-DD) or null - calculated end date",
                "event_anchor": "string or null - event reference if event-based",
                "confidence": "float (0-1) - confidence in this extraction"
            }

            response = await ask_llm(
                prompt,
                response_structure,
                level="low",  # Use low-cost model for temporal parsing
                query_params=self.handler.query_params
            )

            if response and response.get('is_temporal'):
                # Convert to standard format
                result = {
                    'method': 'llm',
                    'is_temporal': True,
                    'type': response.get('type', 'unknown'),
                    'start_date': response.get('start_date'),
                    'end_date': response.get('end_date') or datetime.now(timezone.utc).strftime('%Y-%m-%d'),
                    'relative_days': response.get('relative_days'),
                    'event_anchor': response.get('event_anchor'),
                    'confidence': response.get('confidence', 0.8),
                    'original_expression': query
                }
                return result

            return None

        except Exception as e:
            logger.warning(f"[TIME-EXTRACTOR] LLM parsing failed: {e}")
            return None

    def _try_keyword_fallback(self, query: str) -> Dict:
        """
        Stage 3: Keyword fallback (existing MVP system).

        Args:
            query: The search query

        Returns:
            Dict with temporal info (broad 365-day window if keywords found)
        """
        # Check for any fallback keywords
        is_temporal = any(keyword in query for keyword in self.FALLBACK_KEYWORDS)

        if is_temporal:
            today = datetime.now(timezone.utc)
            start_date = today - timedelta(days=365)

            return {
                'method': 'keyword',
                'is_temporal': True,
                'start_date': start_date.strftime('%Y-%m-%d'),
                'end_date': today.strftime('%Y-%m-%d'),
                'relative_days': 365,
                'confidence': 0.5,  # Low confidence - broad fallback
                'original_expression': None
            }
        else:
            return {
                'method': 'none',
                'is_temporal': False,
                'start_date': None,
                'end_date': None,
                'relative_days': None,
                'confidence': 1.0  # High confidence that it's NOT temporal
            }

    def _build_result(self, method: str, is_temporal: bool, start_date: datetime,
                     end_date: datetime, relative_days: int, original_expression: str,
                     confidence: float = 1.0) -> Dict:
        """
        Build standardized result dictionary.

        Args:
            method: Extraction method ('regex', 'llm', 'keyword', 'none')
            is_temporal: Whether query has temporal constraint
            start_date: Start date as datetime object
            end_date: End date as datetime object
            relative_days: Number of days in range
            original_expression: Original temporal expression from query
            confidence: Confidence score (0-1)

        Returns:
            Standardized temporal info dict
        """
        return {
            'method': method,
            'is_temporal': is_temporal,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date': end_date.strftime('%Y-%m-%d'),
            'relative_days': relative_days,
            'confidence': confidence,
            'original_expression': original_expression
        }
