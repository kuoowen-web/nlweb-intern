"""
cna_parser.py - 中央社新聞解析器

Schema 標準化
- 移除 @context
- author 改為字串
- publisher 改為字串
- 新增 keywords 欄位
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from ..core.interfaces import BaseParser, SessionType
from ..core import settings
from ..utils.text_processor import TextProcessor


class CnaParser(BaseParser):
    """中央社解析器"""

    # 需要 curl_cffi 繞過反爬蟲
    preferred_session_type = SessionType.CURL_CFFI

    def __init__(self):
        super().__init__()
        self._id_url_map: Dict[int, str] = {}

    @property
    def source_name(self) -> str:
        return "cna"

    def get_url(self, article_id: int) -> str:
        if article_id in self._id_url_map:
            cached_url = self._id_url_map[article_id]
            self.logger.debug(f"Using cached URL for ID {article_id}: {cached_url}")
            return cached_url

        default_url = f"https://www.cna.com.tw/news/aall/{article_id}.aspx"
        self.logger.debug(f"Using default category 'aall' for ID {article_id}")
        return default_url

    def get_list_page_config(self) -> Optional[Dict[str, Any]]:
        """取得列表頁爬取配置"""
        # CNA 的主要分類列表頁
        categories = ['aipl', 'aopl', 'acn', 'aie', 'afe', 'aspt', 'ahel', 'aloc', 'acul', 'amov']
        list_urls = [f'https://www.cna.com.tw/list/{cat}.aspx' for cat in categories]

        return {
            'list_urls': list_urls,
            'article_url_pattern': r'href=\"/news/([^/]+)/(\d+)\.aspx\"',
            'base_url': 'https://www.cna.com.tw',
        }

    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """解析中央社文章 HTML"""
        try:
            soup = BeautifulSoup(html, 'lxml')

            title = self._extract_title(soup)
            if not title:
                self.logger.warning(f"No title found: {url}")
                return None

            date_published = self._extract_date(soup)
            if not date_published:
                self.logger.warning(f"No date found: {url}")
                return None

            paragraphs = self._extract_paragraphs(soup)
            if not paragraphs:
                self.logger.warning(f"No content found: {url}")
                return None

            article_body = TextProcessor.smart_extract_summary(paragraphs)

            if len(article_body) < settings.MIN_ARTICLE_LENGTH:
                self.logger.warning(f"Article too short: {url}")
                return None

            # 作者提取：HTML 選擇器 → body 正文開頭
            raw_author = self._extract_raw_author(soup)
            if not raw_author:
                raw_author = self._extract_author_from_body(paragraphs)
            author = TextProcessor.clean_author(raw_author) if raw_author else ""

            # ========== 提取關鍵字 ==========
            keywords = self._extract_keywords(soup, title, article_body)

            # ========== 組裝標準格式 ==========
            article_data = {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,
                "author": author,
                "datePublished": date_published,
                "publisher": "中央社",
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords
            }

            self.logger.info(f"Successfully parsed: {url}")
            return article_data

        except Exception as e:
            self.logger.error(f"Error parsing {url}: {e}")
            return None

    def _extract_keywords(
        self,
        soup: BeautifulSoup,
        title: str,
        article_body: str
    ) -> List[str]:
        """提取關鍵字"""
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

        # 方法 2：從 article:tag 提取
        if not keywords:
            article_tags = soup.find_all('meta', property='article:tag')
            keywords = [
                tag['content'].strip()
                for tag in article_tags
                if tag.get('content')
            ]

        # 方法 3：從 news_keywords 提取
        if not keywords:
            news_keywords = soup.find('meta', attrs={'name': 'news_keywords'})
            if news_keywords and news_keywords.get('content'):
                content = news_keywords['content']
                keywords = [
                    kw.strip()
                    for kw in re.split(r'[,，、;；]', content)
                    if kw.strip()
                ]

        # 方法 4：簡易提取
        if not keywords:
            keywords = self._simple_keyword_extraction(title)

        return keywords[:settings.MAX_KEYWORDS]

    def _simple_keyword_extraction(self, title: str) -> List[str]:
        """簡易關鍵字提取（委託給 TextProcessor）"""
        return TextProcessor.simple_keyword_extraction(title, settings.STOPWORDS_ZH)

    async def get_latest_id(self, session=None) -> Optional[int]:
        """取得中央社當前最新文章 ID"""
        list_url = "https://www.cna.com.tw/list/aall.aspx"

        try:
            self.logger.info(f"Fetching latest ID from: {list_url}")

            # 使用傳入的 session（由 Engine 提供）
            if session is None:
                self.logger.error("No session provided for CNA (requires curl_cffi)")
                return None

            response = await session.get(list_url)

            if response.status_code != 200:
                self.logger.error(f"Failed to fetch list page: {response.status_code}")
                return None

            html = response.text

            soup = BeautifulSoup(html, 'lxml')
            links = soup.select('a[href*="/news/"]')

            if not links:
                self.logger.warning("No article links found in list page")
                return None

            pattern = r'/news/([a-z]+)/(\d{12})\.aspx'
            ids = []

            for link in links:
                href = link.get('href', '')
                match = re.search(pattern, href)
                if match:
                    category = match.group(1)
                    article_id = int(match.group(2))

                    suffix = int(str(article_id)[-4:])
                    if 5001 <= suffix <= 5010:
                        self.logger.debug(f"Skipping 'Good Morning World' article: {article_id}")
                        continue

                    full_url = f"https://www.cna.com.tw/news/{category}/{article_id}.aspx"
                    self._id_url_map[article_id] = full_url
                    ids.append(article_id)

            if not ids:
                self.logger.warning("No valid article IDs extracted from links")
                return None

            latest_id = max(ids)
            self.logger.info(
                f"Latest ID: {latest_id} "
                f"(cached {len(self._id_url_map)} URLs, excluded *5001-5010 articles)"
            )
            return latest_id

        except Exception as e:
            self.logger.error(f"Error getting latest ID: {e}")
            return None

    async def get_date(self, article_id: int) -> Optional[datetime]:
        """極速日期提取：直接從 ID 解析日期"""
        try:
            id_str = str(article_id)

            if len(id_str) != 12:
                self.logger.warning(f"Invalid ID length: {id_str} (expected 12 digits)")
                return None

            date_str = id_str[:8]

            try:
                date_obj = datetime.strptime(date_str, '%Y%m%d')
                self.logger.debug(f"Parsed date from ID {article_id}: {date_obj}")
                return date_obj
            except ValueError as e:
                self.logger.warning(f"Invalid date in ID {article_id}: {date_str} - {e}")
                return None

        except Exception as e:
            self.logger.error(f"Error parsing date from ID {article_id}: {e}")
            return None

    def get_cached_url_count(self) -> int:
        return len(self._id_url_map)

    def clear_url_cache(self) -> None:
        self._id_url_map.clear()
        self.logger.info("URL cache cleared")

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        title_tag = soup.find('h1')
        if title_tag:
            return title_tag.get_text(strip=True)

        meta_title = soup.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            return meta_title['content']

        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            title_text = re.sub(r'\s*[|｜-]\s*中央社.*$', '', title_text)
            return title_text

        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        time_tag = soup.find('time')
        if time_tag:
            datetime_attr = time_tag.get('datetime')
            if datetime_attr:
                try:
                    date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', datetime_attr)
                    date_obj = datetime.fromisoformat(date_str)
                    return date_obj.strftime('%Y-%m-%dT%H:%M:%S')
                except Exception:
                    pass

        meta_date = soup.find('meta', property='article:published_time')
        if meta_date and meta_date.get('content'):
            try:
                date_str = meta_date['content']
                date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
                date_obj = datetime.fromisoformat(date_str)
                return date_obj.strftime('%Y-%m-%dT%H:%M:%S')
            except Exception:
                pass

        return None

    def _extract_raw_author(self, soup: BeautifulSoup) -> str:
        """從 HTML 選擇器提取作者"""
        for selector in ['.author', '.reporter', '.byline']:
            tag = soup.select_one(selector)
            if tag:
                return tag.get_text(strip=True)
        return ""

    def _extract_author_from_body(self, paragraphs: List[str]) -> str:
        """從正文開頭提取記者名（CNA 專用）

        常見格式：
        - （中央社記者陳韻聿倫敦8日專電）
        - （中央社記者黃郁菁屏東縣31日電）
        - （中央社台北4日電）→ 無個人記者，回傳空字串
        - （中央社紐約30日綜合外電報導）→ 無個人記者
        """
        if not paragraphs:
            return ""

        first_para = paragraphs[0][:200]

        # 格式 1：（中央社記者XXX地名日專電/電）
        m = re.search(
            r'（中央社記者\s*(.+?)(?:\d+日|報導|專電|電）)',
            first_para
        )
        if m:
            raw = m.group(1).strip()
            # 移除地名（地名通常在記者名後面，2-5個中文字）
            # 例如「陳韻聿倫敦」→「陳韻聿」,「黃郁菁屏東縣」→「黃郁菁」
            # 記者名通常 2-3 字，後面跟地名
            name_match = re.match(r'([\u4e00-\u9fff]{2,3})', raw)
            if name_match:
                return name_match.group(1)
            return raw

        return ""

    def _extract_paragraphs(self, soup: BeautifulSoup) -> List[str]:
        content_div = (
            soup.select_one('div.paragraph') or
            soup.select_one('div.article-body') or
            soup.select_one('div.content') or
            soup.select_one('article')
        )

        if not content_div:
            return []

        noise_selectors = [
            'script', 'style', 'iframe', 'aside',
            'div.ad', 'div.advertisement',
            '.related-news', '.recommend',
            '.social-share', '.share-buttons',
            'a.ad-link',
        ]

        for selector in noise_selectors:
            for element in content_div.select(selector):
                element.decompose()

        paragraphs = []
        for p in content_div.find_all('p'):
            text = p.get_text(strip=True)

            if (text and
                len(text) > 20 and
                '訂閱' not in text and
                '廣告' not in text and
                '相關新聞' not in text and
                '延伸閱讀' not in text and
                '推薦閱讀' not in text and
                '更多新聞' not in text and
                'APP' not in text and
                '下載' not in text):

                cleaned = TextProcessor.clean_text(text)
                if cleaned:
                    paragraphs.append(cleaned)

        return paragraphs
