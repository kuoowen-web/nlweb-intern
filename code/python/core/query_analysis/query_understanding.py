"""
QueryUnderstanding - Unified query analysis module.

Consolidates: QueryRewrite + AuthorIntentDetector + TimeRangeExtractor (LLM) + Domain detection.
Single LLM call after regex fast path.
"""

import re
import json
import calendar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.prompts import PromptRunner
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("query_understanding")


class QueryUnderstanding(PromptRunner):
    """
    Unified query understanding with regex fast path + LLM analysis.

    Flow:
    1. Regex fast path (time + author) — runs immediately
    2. Wait for Decontextualize
    3. Single LLM call — fills in everything regex missed
    4. Set handler attributes (backward compatible)
    """

    PROMPT_NAME = "QueryUnderstanding"
    STEP_NAME = "QueryUnderstanding"

    # --- Regex fast path patterns ---

    TIME_PATTERNS = {
        'yyyy_mm': r'(\d{4})年(\d{1,2})月',
        'today_zh': r'今[天日]',
        'yesterday_zh': r'昨[天日]',
        'this_week_zh': r'這[一]?[週周]|本[週周]',
        'this_month_zh': r'這個月|本月',
        'recent_n_days': r'最近(\d+)[天日]',
        'recent_n_months': r'最近(\d+)個月',
        'month_period_zh': r'(\d{1,2})月[中初底末]',
    }

    AUTHOR_PATTERNS = {
        'name_before_title_zh': r'([一-鿿]{2,4})\s*(?:記者|作者|編輯|副總編輯|總編輯|主筆)',
        'title_colon_name_zh': r'(?:記者|作者|編輯)[：:]\s*([一-鿿]{2,4})',
        'name_possessive_zh': r'([一-鿿]{2,4})\s*(?:的文章|的報導|寫的|的新聞)',
        'by_author_en': r'(?:articles?|posts?|reports?)\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    }

    AUTHOR_STOPWORDS = {
        '什麼', '怎麼', '如何', '為什麼', '哪些', '這個', '那個', '最新', '最近',
        '今天', '昨天', '本週', '分析', '報導', '新聞', '文章', '發展', '趨勢',
        '技術', '產業', '市場', '公司', '企業', '科技', '經濟', '金融', '政治',
    }

    def __init__(self, handler):
        super().__init__(handler)
        self.handler.state.start_precheck_step(self.STEP_NAME)

    async def do(self):
        """Main entry point — regex fast path then LLM."""
        try:
            # 1. Regex fast path on raw query
            raw_query = self.handler.query
            regex_time = self._regex_time(raw_query)
            regex_author = self._regex_author(raw_query)

            logger.info(f"[QU] Regex fast path — time: {regex_time is not None}, author: {regex_author is not None}")

            # 2. Wait for Decontextualize to finish
            await self.handler.state._decon_event.wait()

            # 3. Build hints from regex results and set on handler for prompt variable resolution
            self.handler.query_analysis_hints = self._build_hints(regex_time, regex_author)

            # 4. Call LLM via PromptRunner
            response = await self.run_prompt(
                self.PROMPT_NAME,
                level="low",
                timeout=15,
                max_length=1024
            )

            if not response:
                logger.warning("[QU] LLM returned empty response, using regex-only results")
                response = {}

            # 5. Merge regex + LLM results and set handler attributes
            self._set_handler_attributes(response, regex_time, regex_author)

            logger.info(
                f"[QU] Done — rewritten={len(self.handler.rewritten_queries)}, "
                f"temporal={self.handler.temporal_range.get('is_temporal', False)}, "
                f"author={self.handler.author_search.get('is_author_search', False)}, "
                f"domain={self.handler.domain_context.get('detected', False)}"
            )

        except Exception as e:
            logger.error(f"[QU] Failed: {e}", exc_info=True)
            self._set_defaults()

        finally:
            await self.handler.state.precheck_step_done(self.STEP_NAME)

    # --- Regex Fast Path ---

    def _regex_time(self, query: str) -> Optional[Dict]:
        """Try to extract time range via regex. Returns dict or None."""
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')

        if re.search(self.TIME_PATTERNS['today_zh'], query):
            return {'is_temporal': True, 'start_date': today_str, 'end_date': today_str,
                    'method': 'regex', 'confidence': 1.0}

        if re.search(self.TIME_PATTERNS['yesterday_zh'], query):
            d = (today - timedelta(days=1)).strftime('%Y-%m-%d')
            return {'is_temporal': True, 'start_date': d, 'end_date': d,
                    'method': 'regex', 'confidence': 1.0}

        if re.search(self.TIME_PATTERNS['this_week_zh'], query):
            start = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
            return {'is_temporal': True, 'start_date': start, 'end_date': today_str,
                    'method': 'regex', 'confidence': 0.9, 'relative_days': 7}

        if re.search(self.TIME_PATTERNS['this_month_zh'], query):
            start = today.replace(day=1).strftime('%Y-%m-%d')
            return {'is_temporal': True, 'start_date': start, 'end_date': today_str,
                    'method': 'regex', 'confidence': 0.9, 'relative_days': 30}

        m = re.search(self.TIME_PATTERNS['recent_n_days'], query)
        if m:
            days = int(m.group(1))
            start = (today - timedelta(days=days)).strftime('%Y-%m-%d')
            return {'is_temporal': True, 'start_date': start, 'end_date': today_str,
                    'method': 'regex', 'confidence': 0.95, 'relative_days': days}

        m = re.search(self.TIME_PATTERNS['recent_n_months'], query)
        if m:
            months = int(m.group(1))
            start = (today - timedelta(days=months * 30)).strftime('%Y-%m-%d')
            return {'is_temporal': True, 'start_date': start, 'end_date': today_str,
                    'method': 'regex', 'confidence': 0.9, 'relative_days': months * 30}

        m = re.search(self.TIME_PATTERNS['yyyy_mm'], query)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 2000 <= year <= today.year + 1:
                last_day = calendar.monthrange(year, month)[1]
                return {'is_temporal': True, 'start_date': f'{year}-{month:02d}-01',
                        'end_date': f'{year}-{month:02d}-{last_day}',
                        'method': 'regex', 'confidence': 1.0}

        # X月中/初/底 — year ambiguous, let LLM handle
        if re.search(self.TIME_PATTERNS['month_period_zh'], query):
            return None

        return None

    def _regex_author(self, query: str) -> Optional[Dict]:
        """Try to extract author name via regex. Returns dict or None."""
        for pattern_name, pattern in self.AUTHOR_PATTERNS.items():
            m = re.search(pattern, query)
            if m:
                name = m.group(1).strip()
                if name in self.AUTHOR_STOPWORDS or len(name) < 2:
                    continue
                return {'is_author_search': True, 'author_name': name,
                        'pattern_matched': pattern_name}
        return None

    # --- Hint Building ---

    def _build_hints(self, regex_time: Optional[Dict], regex_author: Optional[Dict]) -> str:
        """Build hint text for LLM from regex results."""
        parts = []
        if regex_time:
            parts.append(f"Regex 時間偵測結果：{json.dumps(regex_time, ensure_ascii=False)}")
            parts.append("（此結果 confidence 高，你可以直接採用，除非你認為 regex 解析有誤）")
        if regex_author:
            parts.append(f"Regex 作者偵測結果：{json.dumps(regex_author, ensure_ascii=False)}")
            parts.append("（此結果 confidence 高，你可以直接採用）")
        if not parts:
            parts.append("Regex 預分析：無高信度結果，請完整分析所有欄位。")
        return "\n".join(parts)

    # --- Handler Attribute Setting ---

    def _set_handler_attributes(self, llm_response: Dict, regex_time: Optional[Dict], regex_author: Optional[Dict]):
        """Merge regex + LLM results and set handler attributes for backward compatibility."""

        # rewritten_queries
        raw_queries = llm_response.get('rewritten_queries', [])
        if isinstance(raw_queries, list):
            self.handler.rewritten_queries = [q for q in raw_queries if isinstance(q, str) and q.strip()]
        else:
            self.handler.rewritten_queries = []
        self.handler.needs_query_expansion = len(self.handler.rewritten_queries) > 0

        # display_instruction
        self.handler.display_instruction = llm_response.get('display_instruction') or None

        # temporal_range — regex wins if confident, else LLM
        if regex_time and regex_time.get('confidence', 0) >= 0.9:
            self.handler.temporal_range = regex_time
        else:
            llm_time = llm_response.get('time_range', {})
            detected = str(llm_time.get('detected', 'false')).lower() == 'true'
            if detected and llm_time.get('start_date'):
                self.handler.temporal_range = {
                    'is_temporal': True,
                    'start_date': llm_time['start_date'],
                    'end_date': llm_time.get('end_date'),
                    'method': 'llm',
                    'confidence': float(llm_time.get('confidence', 0.8)),
                    'relative_days': None,
                }
            elif regex_time:
                self.handler.temporal_range = regex_time
            else:
                self.handler.temporal_range = {'is_temporal': False}

        # author_search — regex wins if matched, else LLM
        if regex_author:
            self.handler.author_search = regex_author
        else:
            llm_author = llm_response.get('author', {})
            detected = str(llm_author.get('detected', 'false')).lower() == 'true'
            if detected and llm_author.get('name'):
                self.handler.author_search = {
                    'is_author_search': True,
                    'author_name': llm_author['name'],
                    'pattern_matched': 'llm'
                }
            else:
                self.handler.author_search = {'is_author_search': False}

        # domain_context — always from LLM
        llm_domain = llm_response.get('domain', {})
        detected = str(llm_domain.get('detected', 'false')).lower() == 'true'
        if detected:
            self.handler.domain_context = {
                'detected': True,
                'primary_topic': llm_domain.get('primary_topic', ''),
                'boost_keywords': llm_domain.get('boost_keywords', []),
            }
        else:
            self.handler.domain_context = {'detected': False, 'boost_keywords': []}

        # Build query_analysis_hints for downstream prompts (Summarize, Synthesize)
        self._build_query_analysis_hints()

    def _build_query_analysis_hints(self):
        """Build query_analysis_hints string from analysis results for downstream prompts."""
        hints = []
        if self.handler.display_instruction:
            hints.append(f"USER FORMAT REQUEST: The user wants the answer formatted as: {self.handler.display_instruction}")
        author = getattr(self.handler, 'author_search', {})
        if author.get('is_author_search'):
            hints.append(f"AUTHOR FILTER: Results are filtered by author: {author.get('author_name', '')}")
        self.handler.query_analysis_hints = '\n'.join(hints)

    def _set_defaults(self):
        """Set safe defaults when everything fails."""
        self.handler.rewritten_queries = []
        self.handler.needs_query_expansion = False
        self.handler.display_instruction = None
        self.handler.temporal_range = {'is_temporal': False}
        self.handler.author_search = {'is_author_search': False}
        self.handler.domain_context = {'detected': False, 'boost_keywords': []}
        self.handler.query_analysis_hints = ''
