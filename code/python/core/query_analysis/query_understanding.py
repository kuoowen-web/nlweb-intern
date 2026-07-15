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

    # 時間修飾中綴（封閉集）：「記者王家瑜最近寫的」「記者X在2020年1月寫的」的
    # 名字與 possessive 尾之間允許的修飾段。設計要點（AR R3）：
    # - (?:[在於]\s*)? 介詞前導：「在2020年」「於上週」（B2）
    # - 數字必帶單位 + (?![0-9０-９]) 原子化：防裸數字誤收（「王大明2」）與
    #   catastrophic backtracking（26 位數字 run 曾 3.6s 指數增長 = availability
    #   紅線；原子化後 80 位 0.00ms）（B3）
    # - 開放集主題修飾（關於X）不支援——交一般檢索（寧可不抽也不誤抽）。
    _TIME_INFIX = (r'(?:\s*(?:[在於]\s*)?'
                   r'(?:最近|今天|昨天|本週|上週|上個月|這幾天|近期|今年|去年|本月|這個月|這週|'
                   r'[0-9０-９]{1,4}(?![0-9０-９])[年月日號]))*')

    AUTHOR_PATTERNS = {
        # Sandwich（職稱+名字+[時間修飾]+possessive）— 最高精度，必須排第一。
        # 2026-07-08 P1-5：原首位 name_before_title_zh 會把「幫我找記者X寫的文章」
        # 的請求語「幫我找」最左匹配成名字；sandwich 對同句式抽出正確名字。
        # （源自舊 author_intent_detector.py 的 author_articles_zh，QU 整併時遺失。）
        # capture = CJK 2-4 字 lazy、逐字排除的/寫（防「王家瑜最近」吸附）；
        # 記者(?![會節們群]) 防「記者會/記者節/記者們/記者群」主題詞誤觸。
        # 已知歧義（AR R3-B2 裁決）：名字尾字為在/於且緊接時間 token（王不存在2026年…）
        # 會抽短一字（王不存）——ILIKE contains 語義下為超集，檢索行為等價。
        # 職稱 alternation 帶 negative lookahead（AR R5-S1）：編輯(?![部台室群們]) 防
        # 「編輯部王大明」把「部王大明」吸進名字；作者(?![群們]) 同理。
        'title_name_possessive_zh': (
            r'(?:記者(?![會節們群])|作者(?![群們])|編輯(?![部台室群們]))\s*((?:(?![的寫])[一-鿿]){2,4}?)'
            + _TIME_INFIX + r'\s*(?:的文章|的報導|寫的|的新聞|的評論)'),
        # 左邊界 lookbehind（AR R4-B1）：名字左鄰不得是 CJK/數字——「中國時報王家瑜記者」
        # 「2026年王大明記者」「給我／列出／顯示＋名」等 mid-string 黏連開放集整類消失
        # （strip 枚舉結構上收斂不了此家族）。句首與 strip 後的位置天然滿足 lookbehind。
        # capture 用 lazy {2,4}?（AR R5-B1）：lookbehind 已釘起點，lazy 讓「王大明總編輯」
        # 正確停在'王大明'——greedy 曾吃成'王大明總'再用「編輯」接上（副總編輯/總編輯/
        # 主筆三個多字職稱在 2-3 字名下全數 corrupted-name 誤抽）。
        'name_before_title_zh': (
            r'(?<![0-9０-９一-鿿])((?:(?![的寫])[一-鿿]){2,4}?)\s*'
            r'(?:記者(?![會節們群])|作者(?![群們])|編輯(?![部台室群們])|副總編輯|總編輯|主筆)'),
        'title_colon_name_zh': r'(?:記者(?![會節們群])|作者(?![群們])|編輯(?![部台室群們]))[：:]\s*([一-鿿]{2,4})',
        # name_possessive_zh 已移除（AR R3-B1）：對全部正例命中 0（sandwich/
        # name_before_title/colon 全承接），卻是「和林資傑」（雙作者）「在立法院」
        # 「跑立法院」「年專題」「高端疫苗」誤抽的唯一來源 = 0 正貢獻純 scavenger。
        # 無職稱訊號的 possessive query 一律交 LLM。
        'by_author_en': r'(?:articles?|posts?|reports?)\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    }

    # 職稱訊號（reject 判定用；記者會/節/們/群、作者群、編輯部/台/室/群/們是主題詞
    # 非職稱；專欄作家=泛稱非具名）——與 patterns 的職稱 alternation 同步（R5-S1）
    TITLE_WORD_RE = re.compile(
        r'記者(?![會節們群])|作者(?![群們])|編輯(?![部台室群們])|主筆|專欄(?!作家)')

    # 請求語前綴——名字抽取前以 fixpoint 迴圈剝除（見 _strip_request_prefixes）：
    # 堆疊前綴（請麻煩幫我找）、填充詞（一下/一查/一找）、請求動詞（找/查/搜尋/搜/看/
    # 問/知道/了解）逐層剝淨。bare 動詞單獨出現須帶填充詞才無條件剝：
    # 「查理布朗」「看見台灣」「想見你」不受影響。
    REQUEST_PREFIX_RE = re.compile(
        r'^(?:(?:請?(?:幫我|幫忙)|請|麻煩|我想|想|我要|要|有沒有|有)'
        r'(?:找|查|搜尋|搜|看|問|知道|了解|教)?(?:一下|一查|一找)?'
        r'|(?:找|查|搜尋|搜|看)(?:一下|一查|一找))\s*')

    # bare 動詞條件剝：動詞只在後接「2-4 CJK 名 + 職稱詞」時剝（雙字動詞置前防單字先吃）。
    # 職稱集含多字職稱（AR R5-B1：漏 副總編輯/總編輯/主筆 曾讓「找王大明主筆」復活 R2 家族）。
    BARE_VERB_PREFIX_RE = re.compile(
        r'^(?:搜尋|尋找|找|查|搜|問|了解|知道)'
        r'(?=(?:(?![的寫])[一-鿿]){2,4}(?:記者(?![會節們群])|作者(?![群們])|編輯(?![部台室群們])|副總編輯|總編輯|主筆))')

    # 功能詞（時間/介詞/修飾）——抽出的名字含這些 = regex 吸附產物，一律 reject。
    FUNCTION_WORD_RE = re.compile(
        r'最近|今天|昨天|本週|上週|去年|今年|之前|以前|關於|對於|有關|所有|全部|相關')

    AUTHOR_STOPWORDS = {
        '什麼', '怎麼', '如何', '為什麼', '哪些', '這個', '那個', '最新', '最近',
        '今天', '昨天', '本週', '分析', '報導', '新聞', '文章', '發展', '趨勢',
        '技術', '產業', '市場', '公司', '企業', '科技', '經濟', '金融', '政治',
        # 高頻非人名誤抽家族（2026-07-08 P1-5 AR：strict 下誤抽 = 假空結果）
        '記者會', '記者節', '編輯部', '編輯台', '記者們',
        # 職稱修飾詞首發集（「攝影記者」「資深記者」的修飾段；
        # 其級聯超集由 _regex_author 的 TITLE_WORD-in-name reject 收掉）
        '攝影', '資深', '實習', '文字', '特派', '特約',
        # beat 詞/媒體名批次（AR R6-S1 executor 落地採納：「體育記者」類
        # beat 前綴與媒體名前綴在 strict 下誤抽 = 假空結果；
        # 政治/科技/經濟/金融已在上方基本集內）
        '體育', '財經', '國際', '地方', '文化', '娛樂', '司法', '醫藥',
        '社會', '生活', '影劇', '兩岸', '軍事', '環境', '報社', '媒體',
        '電視台', '本報', '本刊', '三立', '中天', '東森', '民視', '台視',
        '中視', '華視',
        # 請求語片語（前綴 strip 後理論上不出現，保留為雙保險）
        '幫我找', '請幫我', '幫忙找', '我想找', '我想看', '幫我查',
        '請給我', '給我看', '幫我搜', '搜尋',
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

    def _strip_request_prefixes(self, query: str) -> str:
        """Fixpoint 剝請求語前綴：堆疊前綴（請麻煩幫我找一下…）逐層剝到不動點。

        單發 sub 擋不住堆疊組合（AR R2 實測「麻煩幫我找」「幫我找一下」leak）。
        """
        while True:
            stripped = self.REQUEST_PREFIX_RE.sub('', query)
            stripped = self.BARE_VERB_PREFIX_RE.sub('', stripped)
            if stripped == query:
                return query
            query = stripped

    def _regex_author(self, query: str) -> Optional[Dict]:
        """Try to extract author name via regex. Returns dict or None."""
        query = self._strip_request_prefixes(query)
        for pattern_name, pattern in self.AUTHOR_PATTERNS.items():
            m = re.search(pattern, query)
            if m:
                name = m.group(1).strip()
                if name in self.AUTHOR_STOPWORDS or len(name) < 2:
                    continue
                if self.FUNCTION_WORD_RE.search(name):
                    # 名字含時間/介詞 = 吸附產物（「王家瑜最近」「關於高端」），
                    # reject 讓其 fallthrough——寧可不抽也不觸發假「找不到作者」
                    continue
                if self.TITLE_WORD_RE.search(name):
                    # 名字含職稱詞 = bare 職稱族／級聯超集（「記者寫」「攝影記者」
                    # 「編輯室」「記者群」），不是人名（AR R2 S1' 一刀流）
                    continue
                if name.startswith('駐'):
                    # 駐X 特派開放類（駐美/駐日…）；「駐」非漢人姓氏
                    continue
                if name[-1] in ('總', '副'):
                    # 「王總編輯」單字姓+職稱切面（AR R5-N2）：capture min 2 的結構
                    # 極限使其抽成'王總'——尾字總/副的真實人名罕見，reject 安全
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
