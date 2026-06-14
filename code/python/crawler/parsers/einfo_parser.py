"""
einfo_parser.py - 環境資訊中心解析器

流水號式爬取：使用 /node/{id} 結構，從最新 ID 往回爬。
支援：
- 二分搜尋自動偵測最新 ID
- 日期過濾（target_date）
- 斷點續傳（start_id）
"""

import re
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup
from htmldate import find_date
import trafilatura

from ..core.interfaces import BaseParser, SessionType
from ..core import settings
from ..utils.text_processor import TextProcessor

# A/B test 統計（模組級別，跨多篇文章累計）
_ab_stats = {"custom_only": 0, "trafilatura_only": 0, "both": 0, "neither": 0}
_ab_logger = logging.getLogger("EInfoABTest")


class EInfoParser(BaseParser):
    """環境資訊中心 (E-Info) Parser"""

    BASE_URL = "https://e-info.org.tw"
    CATEGORY_URLS = settings.EINFO_CATEGORY_URLS

    preferred_session_type = SessionType.CURL_CFFI

    def __init__(
        self,
        count: Optional[int] = None,
        start_id: Optional[int] = None,
        target_date: Optional[datetime] = None,
        max_pages: Optional[int] = None,
        **kwargs
    ):
        super().__init__()
        self.count = count or 50
        self.start_id = start_id
        self.target_date = target_date
        self._discovered_ids: List[int] = []

        if count:
            self.max_pages = (count // 10) + 2
        elif max_pages:
            self.max_pages = max_pages
        else:
            self.max_pages = 5

    @property
    def source_name(self) -> str:
        return "einfo"

    def get_url(self, article_id: int) -> str:
        return f"{self.BASE_URL}/node/{article_id}"

    def get_discovered_ids(self) -> List[int]:
        """回傳已發現的 ID 列表"""
        return self._discovered_ids

    async def get_latest_id(self, session=None) -> Optional[int]:
        """動態獲取最新 ID（優先用二分搜尋，fallback 到列表頁）"""
        try:
            if self.start_id:
                latest_id = self.start_id
                self.logger.info(f"Using provided start_id: {latest_id}")
            else:
                # 優先：二分搜尋（更可靠）
                latest_id = await self._binary_search_latest_id(session)

                # Fallback：從列表頁掃描
                if not latest_id:
                    self.logger.info("Binary search failed, trying list pages...")
                    latest_id = await self._fetch_latest_id_from_lists(session)

                # 最後 Fallback：使用預設值
                if not latest_id:
                    self.logger.warning(f"Cannot detect latest ID, using default {settings.EINFO_DEFAULT_LATEST_ID}")
                    latest_id = settings.EINFO_DEFAULT_LATEST_ID

            self._discovered_ids = list(range(
                latest_id,
                latest_id - self.count,
                -1
            ))

            if self._discovered_ids:
                self.logger.info(f"Detected latest ID: {latest_id}")
                self.logger.info(f"Will crawl {len(self._discovered_ids)} articles (ID: {latest_id} -> {self._discovered_ids[-1]})")
                return self._discovered_ids[0]

            return None

        except Exception as e:
            self.logger.error(f"get_latest_id error: {e}")
            return None

    async def _binary_search_latest_id(
        self,
        session,
        low: int = 241000,
        high: int = 270000
    ) -> Optional[int]:
        """二分搜尋找到最新有效的 node ID"""
        if session is None:
            return None

        self.logger.info(f"Binary search for latest ID in range [{low}, {high}]")
        latest_valid = None
        consecutive_errors = 0
        max_consecutive_errors = 3

        while low <= high:
            mid = (low + high) // 2
            url = self.get_url(mid)

            try:
                response = await asyncio.wait_for(session.get(url), timeout=8)
                await asyncio.sleep(0.3)  # Rate limiting

                consecutive_errors = 0  # Reset on success

                if response.status_code == 200:
                    # 這個 ID 存在，往更大的找
                    latest_valid = mid
                    low = mid + 1
                    self.logger.debug(f"  ID {mid}: exists, searching higher")
                else:
                    # 這個 ID 不存在，往更小的找
                    high = mid - 1
                    self.logger.debug(f"  ID {mid}: not found, searching lower")

            except Exception as e:
                self.logger.warning(f"Binary search error at {mid}: {e}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.warning(f"Binary search aborted: {max_consecutive_errors} consecutive errors")
                    break
                high = mid - 1

        if latest_valid:
            self.logger.info(f"Binary search found latest ID: {latest_valid}")

        return latest_valid

    async def _fetch_latest_id_from_lists(self, session=None) -> Optional[int]:
        """從列表頁提取最大的 Node ID（fallback 方法）"""
        max_id = 0

        for url in self.CATEGORY_URLS:
            try:
                self.logger.info(f"Scanning list page: {url}")

                if session is None:
                    self.logger.error("No session provided for EInfo")
                    continue

                response = await session.get(url)

                if response.status_code != 200:
                    continue

                html = response.text

                soup = BeautifulSoup(html, 'lxml')
                node_links = soup.find_all('a', href=re.compile(r'/node/(\d+)'))

                for link in node_links:
                    href = link.get('href', '')
                    match = re.search(r'/node/(\d+)', href)
                    if match:
                        node_id = int(match.group(1))
                        max_id = max(max_id, node_id)

                self.logger.info(f"   Found {len(node_links)} links, max ID: {max_id}")
                await asyncio.sleep(0.5)  # Rate limiting

            except Exception as e:
                self.logger.warning(f"List page fetch failed ({url}): {e}")
                continue

        return max_id if max_id > 0 else None

    async def get_date(self, article_id: int, session=None) -> Optional[datetime]:
        """
        輕量級日期提取（給 Navigator 用於日期過濾）

        Args:
            article_id: 文章 ID
            session: HTTP session（可選）

        Returns:
            datetime 物件或 None
        """
        if session is None:
            return None

        try:
            url = self.get_url(article_id)
            response = await session.get(url)

            if response.status_code != 200:
                return None

            html = response.text

            soup = BeautifulSoup(html, 'lxml')
            date_str = self._extract_date(soup)

            if date_str:
                return self._parse_date(date_str)

            return None

        except Exception as e:
            self.logger.warning(f"get_date error for {article_id}: {e}")
            return None

    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """解析 HTML 內容（含 Trafilatura A/B test）"""
        custom_result = await self._custom_parse(html, url)
        traf_result = self._trafilatura_parse(html, url)

        # ========== A/B test 記錄 ==========
        global _ab_stats
        if custom_result and traf_result:
            _ab_stats["both"] += 1
        elif custom_result and not traf_result:
            _ab_stats["custom_only"] += 1
        elif traf_result and not custom_result:
            _ab_stats["trafilatura_only"] += 1
            _ab_logger.info(f"TRAFILATURA_WIN: {url} (custom parser failed, trafilatura succeeded)")
        else:
            _ab_stats["neither"] += 1

        total = sum(_ab_stats.values())
        if total % 50 == 0 and total > 0:
            _ab_logger.info(
                f"A/B stats ({total} articles): "
                f"both={_ab_stats['both']} "
                f"custom_only={_ab_stats['custom_only']} "
                f"traf_only={_ab_stats['trafilatura_only']} "
                f"neither={_ab_stats['neither']}"
            )

        # 優先用 custom，fallback 到 trafilatura
        return custom_result or traf_result

    def _trafilatura_parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """用 Trafilatura 通用提取器解析"""
        try:
            # bare_extraction 回傳 Document 物件（trafilatura 2.0+）
            doc = trafilatura.bare_extraction(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            if not doc:
                return None

            title = doc.title or ''
            body = doc.text or ''
            date_str = doc.date or ''
            author = doc.author or ''

            if not title or not body or len(body) < settings.MIN_ARTICLE_LENGTH:
                return None

            # 日期處理
            published_date = None
            if date_str:
                try:
                    published_date = datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    pass
            if not published_date:
                hd = find_date(html, outputformat='%Y-%m-%d')
                if hd:
                    published_date = datetime.strptime(hd, '%Y-%m-%d')

            if not published_date:
                return None

            # 日期過濾
            if self.target_date and published_date < self.target_date:
                return None

            return {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": TextProcessor.smart_extract_summary(body.split('\n')),
                "author": author or "",
                "datePublished": published_date.strftime('%Y-%m-%dT%H:%M:%S'),
                "publisher": "環境資訊中心",
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": [],
                "_source": "trafilatura",  # 標記來源，方便後續分析
            }

        except Exception as e:
            self.logger.debug(f"Trafilatura parse error: {e}")
            return None

    async def _custom_parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """原本的自訂解析邏輯"""
        try:
            soup = BeautifulSoup(html, 'lxml')

            title = self._extract_title(soup)
            if not title:
                return None

            date_str = self._extract_date(soup)
            published_date = self._parse_date(date_str) if date_str else None

            # htmldate fallback：CSS selector 或自訂 regex 失敗時
            if not published_date:
                hd = find_date(html, outputformat='%Y-%m-%d')
                if hd:
                    published_date = datetime.strptime(hd, '%Y-%m-%d')
                    self.logger.debug(f"htmldate fallback found date: {hd}")
                else:
                    return None

            # 日期過濾
            if self.target_date and published_date < self.target_date:
                return None

            # ========== 使用智慧摘要 ==========
            paragraphs = self._extract_paragraphs(soup)
            if not paragraphs:
                return None

            article_body = TextProcessor.smart_extract_summary(paragraphs)

            if len(article_body) < settings.MIN_ARTICLE_LENGTH:
                return None

            author = self._extract_author(soup)

            # ========== 提取關鍵字 ==========
            keywords = self._extract_keywords(soup, title, article_body)

            # ========== 組裝標準格式 ==========
            return {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,
                "author": author or "",
                "datePublished": published_date.strftime('%Y-%m-%dT%H:%M:%S'),
                "publisher": "環境資訊中心",
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords
            }

        except Exception as e:
            self.logger.error(f"Custom parse error: {e}")
            return None

    def _extract_keywords(
        self,
        soup: BeautifulSoup,
        title: str,
        article_body: str
    ) -> List[str]:
        """提取關鍵字（多重策略）"""
        keywords = []

        # 方法 1：從 meta 標籤提取
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords and meta_keywords.get('content'):
            content = meta_keywords['content']
            keywords = [
                kw.strip()
                for kw in re.split(r'[,，、;；]', content)
                if kw.strip()
            ]

        # 方法 2：從分類/標籤連結提取（Drupal 常見選擇器）
        if not keywords:
            tag_selectors = [
                '.field-name-field-category a',
                '.field-name-field-tags a',
                '.tags a',
                '[rel="tag"]',
                '.taxonomy-term a',
                '.field-name-field-topic a',
            ]
            for selector in tag_selectors:
                links = soup.select(selector)
                if links:
                    keywords = [
                        link.get_text(strip=True)
                        for link in links
                        if link.get_text(strip=True)
                    ]
                    break

        # 方法 3：從 article:tag 提取
        if not keywords:
            article_tags = soup.find_all('meta', property='article:tag')
            keywords = [
                tag['content'].strip()
                for tag in article_tags
                if tag.get('content')
            ]

        # 方法 4：中文標題切詞提取
        if not keywords:
            keywords = self._chinese_keyword_extraction(title)

        return keywords[:settings.MAX_KEYWORDS]

    @staticmethod
    def _chinese_keyword_extraction(title: str) -> List[str]:
        """中文標題關鍵字提取（不依賴分詞器）

        策略：用標點和停用詞切分標題，取 2-8 字的片段。
        """
        if not title:
            return []

        # 用標點分割
        segments = re.split(
            r'[，。、！？：；「」『』（）《》〈〉\s,.\-!?:;()\[\]／/]+',
            title
        )

        stopwords = {'的', '了', '在', '是', '與', '及', '和', '或', '從', '到',
                      '被', '為', '把', '對', '讓', '將', '而', '也', '又', '都',
                      '已', '更', '再', '還', '卻', '才', '就', '要', '會', '能',
                      '可', '可能', '不', '沒', '未', '無'}

        keywords = []
        for seg in segments:
            seg = seg.strip()
            # 移除開頭結尾的停用詞
            for sw in stopwords:
                if seg.startswith(sw):
                    seg = seg[len(sw):]
                if seg.endswith(sw):
                    seg = seg[:-len(sw)]
            seg = seg.strip()
            if 2 <= len(seg) <= 8:
                keywords.append(seg)

        return keywords[:5]

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        title_tag = soup.select_one('h1.title, #page-title')
        if title_tag:
            return title_tag.get_text(strip=True)
        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        date_tag = soup.select_one('.article-create-date')
        if date_tag:
            return date_tag.get_text(strip=True)
        return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        try:
            match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
            if match:
                date_clean = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
                return datetime.strptime(date_clean, '%Y-%m-%d')
        except Exception:
            pass
        return None

    def _extract_paragraphs(self, soup: BeautifulSoup) -> List[str]:
        """提取內文段落（用於智慧摘要）"""
        article_tag = soup.select_one('article')
        if not article_tag:
            return []

        # 移除雜訊元素
        for unwanted in article_tag.select(
            '.article-create-date, .share-buttons, '
            '.field-name-field-image, .social-share'
        ):
            unwanted.decompose()

        # 提取段落
        paragraphs = []
        for p in article_tag.find_all(['p', 'div']):
            text = p.get_text(strip=True)

            if (text and
                len(text) > 20 and
                '訂閱' not in text and
                '廣告' not in text):

                cleaned = TextProcessor.clean_text(text)
                if cleaned:
                    paragraphs.append(cleaned)

        return paragraphs

    def _extract_author(self, soup: BeautifulSoup) -> Optional[str]:
        """提取作者（多重模式匹配）"""
        article_tag = soup.select_one('article')
        if not article_tag:
            return None

        text = article_tag.get_text()[:500]  # 作者資訊在前 500 字

        # 按優先順序嘗試各種模式
        patterns = [
            # 環境資訊中心記者 XXX 報導
            r'環境資訊中心\s*記者\s+([^\s報]+(?:\s*[^\s報]+)?)\s*報導',
            # 環境資訊中心綜合外電；XXX 編譯
            r'環境資訊中心[^；]*；\s*([^\s編]+(?:\s+[^\s編]+)?)\s*編譯',
            # 環境資訊中心 XXX報導（無「記者」二字）
            r'環境資訊中心\s+([\u4e00-\u9fff]{2,3})[、\s].*?報導',
            # 公視（特約）記者 XXX
            r'公視(?:特約)?記者\s+([\u4e00-\u9fff]{2,3})',
            # 特約記者XXX報導
            r'特約記者\s*([^\s、報]+(?:\s*[^\s、報]+)?)\s*[、報]',
            # 文：XXX
            r'文[：:]\s*([\u4e00-\u9fff]{2,5})',
            # 作者：XXX
            r'作者[：:]\s*([\u4e00-\u9fff]{2,5})',
            # 整理：XXX
            r'整理[：:]\s*([\u4e00-\u9fff]{2,5})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                author_name = match.group(1).strip()
                if author_name:
                    return TextProcessor.clean_author(author_name)

        return None
