"""
ltn_parser.py - 自由時報新聞解析器

支援：
- 流水號式爬取
- Schema.org NewsArticle 格式輸出
- 關鍵字提取
"""

import re
import json
import ssl
import logging
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from ..core.interfaces import BaseParser
from ..core import settings
from ..utils.text_processor import TextProcessor


class LtnParser(BaseParser):
    """
    自由時報解析器

    職責：
    1. 解析 LTN HTML 結構
    2. 提取新聞內容（標題、日期、作者、內文）
    3. 提取關鍵字
    4. 提供 URL 構建邏輯
    5. 提供最新 ID 查詢
    """

    # 使用 settings 中的分類
    MAIN_CATEGORIES = settings.LTN_MAIN_CATEGORIES

    FALLBACK_DOMAINS = [
        "https://health.ltn.com.tw/article/breakingnews/{id}",
        "https://def.ltn.com.tw/article/breakingnews/{id}",
        "https://ec.ltn.com.tw/article/breakingnews/{id}",
        "https://sports.ltn.com.tw/news/breakingnews/{id}",
        "https://art.ltn.com.tw/article/breakingnews/{id}"
    ]

    def __init__(self):
        """初始化解析器"""
        super().__init__()

    @property
    def source_name(self) -> str:
        """來源名稱"""
        return "ltn"

    def get_url(self, article_id: int) -> str:
        """根據文章 ID 構建 URL"""
        return f"https://news.ltn.com.tw/news/life/breakingnews/{article_id}"

    def get_candidate_urls(self, article_id: int) -> List[str]:
        """All category URLs except primary (life), ordered by frequency."""
        candidates = []
        for category in self.MAIN_CATEGORIES:
            if category == 'life':
                continue  # Already tried via get_url()
            candidates.append(f"https://news.ltn.com.tw/news/{category}/breakingnews/{article_id}")
        for domain_pattern in self.FALLBACK_DOMAINS:
            candidates.append(domain_pattern.format(id=article_id))
        return candidates

    def get_sitemap_config(self) -> Optional[Dict[str, Any]]:
        """取得 sitemap 相關配置"""
        return {
            'index_url': 'https://news.ltn.com.tw/sitemap.xml',
            'is_index': False,  # LTN sitemap is a single file with latest 1000 articles
            # Pattern applied to extracted URL (without <loc> tags)
            'article_url_pattern': r'https?://news\.ltn\.com\.tw/news/',
            # LTN sitemap doesn't have date-based filenames, date filtering not applicable
            'sitemap_date_pattern': None,
        }

    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """解析自由時報文章 HTML"""
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

            raw_author = self._extract_raw_author(soup)
            author = TextProcessor.clean_author(raw_author) if raw_author else ""

            paragraphs = self._extract_paragraphs(soup)
            if not paragraphs:
                self.logger.warning(f"No content found: {url}")
                return None

            article_body = TextProcessor.smart_extract_summary(paragraphs)

            if len(article_body) < settings.MIN_ARTICLE_LENGTH:
                self.logger.warning(f"Article too short: {url}")
                return None

            keywords = self._extract_keywords(soup, title, article_body)

            # 組裝標準格式（與其他 Parser 一致）
            article_data = {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,
                "author": author,
                "datePublished": date_published,
                "publisher": "自由時報",
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

        # 從 meta 標籤提取
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords and meta_keywords.get('content'):
            content = meta_keywords['content']
            keywords = [
                kw.strip()
                for kw in re.split(r'[,，、;；]', content)
                if kw.strip()
            ]

        if not keywords:
            article_tags = soup.find_all('meta', property='article:tag')
            keywords = [
                tag['content'].strip()
                for tag in article_tags
                if tag.get('content')
            ]

        if not keywords:
            json_ld_script = soup.find('script', type='application/ld+json')
            if json_ld_script:
                try:
                    data = json.loads(json_ld_script.string)
                    if isinstance(data, dict) and 'keywords' in data:
                        kw_str = data['keywords']
                        if isinstance(kw_str, str):
                            keywords = [
                                kw.strip()
                                for kw in re.split(r'[,，、;；]', kw_str)
                                if kw.strip()
                            ]
                        elif isinstance(kw_str, list):
                            keywords = [str(kw).strip() for kw in kw_str if kw]
                except Exception:
                    pass

        return keywords[:settings.MAX_KEYWORDS]

    async def get_latest_id(self, session=None) -> Optional[int]:
        """取得 LTN 當前最新文章 ID"""
        list_url = "https://news.ltn.com.tw/list/breakingnews"

        try:
            self.logger.info(f"Fetching latest ID from: {list_url}")

            ssl_context = ssl.create_default_context()
            connector = aiohttp.TCPConnector(ssl=ssl_context)

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(list_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to fetch list page: {response.status}")
                        return None

                    html = await response.text()

            pattern = r'/news/\w+/breakingnews/(\d+)'
            matches = re.findall(pattern, html)

            if not matches:
                self.logger.warning("No article IDs found in list page")
                return None

            ids = [int(id_str) for id_str in matches]
            latest_id = max(ids)

            self.logger.info(f"Latest ID: {latest_id}")
            return latest_id

        except Exception as e:
            self.logger.error(f"Error getting latest ID: {e}")
            return None

    async def get_date(self, article_id: int) -> Optional[datetime]:
        """輕量級日期提取"""
        try:
            ssl_context = ssl.create_default_context()
            connector = aiohttp.TCPConnector(ssl=ssl_context)

            async with aiohttp.ClientSession(connector=connector) as session:
                for category in self.MAIN_CATEGORIES:
                    url = f"https://news.ltn.com.tw/news/{category}/breakingnews/{article_id}"
                    date = await self._extract_date_lightweight(session, url)
                    if date:
                        return date

                for domain_pattern in self.FALLBACK_DOMAINS:
                    url = domain_pattern.format(id=article_id)
                    date = await self._extract_date_lightweight(session, url)
                    if date:
                        return date

            return None

        except Exception as e:
            self.logger.error(f"Error in get_date for article {article_id}: {e}")
            return None

    async def _extract_date_lightweight(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[datetime]:
        """輕量級日期提取"""
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return None
                html = await response.text()

            soup = BeautifulSoup(html, 'lxml')

            meta_date = soup.find('meta', property='article:published_time')
            if meta_date and meta_date.get('content'):
                try:
                    date_str = meta_date['content']
                    date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
                    return datetime.fromisoformat(date_str)
                except Exception:
                    pass

            return None

        except Exception:
            return None

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """提取標題（支援 news/sports/ent/ec/health 等子網域）"""
        # 依優先順序嘗試不同 selector
        for selector in ['.whitecon.article h1', 'article h1', 'h1']:
            tag = soup.select_one(selector)
            if tag:
                title = tag.get_text(strip=True)
                if title:
                    return title

        # fallback: og:title
        og = soup.find('meta', property='og:title')
        if og and og.get('content'):
            return og['content'].strip()

        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        """提取發布時間"""
        meta_date = soup.find('meta', property='article:published_time')
        if meta_date and meta_date.get('content'):
            try:
                date_str = meta_date['content']
                date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
                date_obj = datetime.fromisoformat(date_str)
                return date_obj.strftime('%Y-%m-%dT%H:%M:%S')
            except Exception:
                pass

        time_tag = soup.select_one('.time')
        if time_tag:
            time_str = time_tag.get_text(strip=True)
            date_obj = self._parse_date_string(time_str)
            if date_obj:
                return date_obj.strftime('%Y-%m-%dT%H:%M:%S')

        return None

    def _parse_date_string(self, time_str: str) -> Optional[datetime]:
        """解析時間字串（委託給 TextProcessor）"""
        return TextProcessor.parse_date_string(time_str)

    def _extract_raw_author(self, soup: BeautifulSoup) -> str:
        """提取原始作者資訊"""
        author_tag = soup.select_one('.article_edit') or soup.select_one('.editor')
        if author_tag:
            return author_tag.get_text(strip=True)
        return ""

    def _extract_paragraphs(self, soup: BeautifulSoup) -> List[str]:
        """提取內文段落（支援 news/sports/ent/ec/health 等子網域）"""
        # 依優先順序嘗試，每個候選必須有實際 <p> 內容
        # ent.ltn.com.tw: .whitecon.article .text 存在但無 <p>，實際內容在 article .text
        # ec.ltn.com.tw: 無 .whitecon.article，內容在 .article_content 或 .text
        candidates = [
            '.whitecon.article .text',
            'article .text',
            '.article_content',
            '.text',
            'article',
        ]
        content_div = None
        for selector in candidates:
            # select() 回傳所有匹配元素，找第一個有 <p> 的
            # health.ltn.com.tw: 有多個 .text div，第一個空的，第二個有內容
            for div in soup.select(selector):
                if div.find('p'):
                    content_div = div
                    break
            if content_div:
                break

        if not content_div:
            return []

        noise_selectors = [
            'script', 'style', 'iframe', 'aside',
            '.suggest', '.related', '.author',
            '.before_ir', '.after_ir', '.ad',
            '.advertisement', '.boxTitle'
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
                '不用抽' not in text and
                    '請繼續往下閱讀' not in text):

                cleaned = TextProcessor.clean_text(text)
                if cleaned:
                    paragraphs.append(cleaned)

        return paragraphs
