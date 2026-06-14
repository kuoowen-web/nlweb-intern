"""
chinatimes_parser.py - 中國時報新聞解析器

URL 格式：
  即時新聞: https://www.chinatimes.com/realtimenews/20260209002603-260402
  報紙新聞: https://www.chinatimes.com/newspapers/20260209000401-260102

ID 格式：YYYYMMDDXXXXXX（14位數字）
  前 8 碼 = 日期（YYYYMMDD）
  後 6 碼 = 序號

分類碼（URL 中 - 後的 6 位數字）：
  260402 = 社會, 260405 = 生活, 260407 = 政治 等
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from ..core.interfaces import BaseParser, SessionType
from ..core import settings
from ..utils.text_processor import TextProcessor


class ChinatimesParser(BaseParser):
    """中國時報解析器"""

    preferred_session_type = SessionType.CURL_CFFI

    # 主要分類（用於列表頁爬取）
    CATEGORIES = [
        'politic', 'chinatimes', 'society', 'life',
        'world', 'money', 'sport', 'star',
    ]

    # realtimenews category codes（URL 中 - 後的 6 位數字）
    # 按文章數量排序。get_url() 用第一個作 primary，其餘作 candidate。
    # Top 40 覆蓋 ~95.6% 的 realtimenews 文章（2026-02-23 統計）。
    # 注意：每篇文章只有其正確 category 能存取，260402 不是萬用路徑。
    REALTIMENEWS_CATEGORY_CODES = [
        '260402',  # 社會 13.2%
        '260410',  # 娛樂 8.2%
        '263201',  # 中時社論/評論 7.7%
        '263301',  # 旺報 7.1%
        '261502',  # 工商時報 6.5%
        '260405',  # 生活 6.4%
        '263101',  # 中時新聞網 5.6%
        '260407',  # 政治 4.8%
        '260404',  # 國際 4.0%
        '261507',  # 工商財經 3.2%
        '261101',  # 房產新聞 3.0%
        '261701',  # 健康 2.3%
        '261509',  # 工商產業 2.0%
        '260421',  # 軍事 1.9%
        '261504',  # 工商科技 1.8%
        '260403',  # 財經 1.5%
        '260408',  # 科技 1.5%
        '260409',  # 兩岸 1.4%
        '261511',  # 工商特刊 1.4%
        '261601',  # 消費/時尚 1.3%
        '261505',  # 工商產業 1.1%
        '261109',  # 房產其他 0.8%
        '260418',  # 政治/專欄 0.8%
        '260417',  # 社會/地方 0.7%
        '261306',  # 體育/綜合 0.7%
        '263504',  # 中天新聞 0.7%
        '265002',  # 網推 0.6%
        '261105',  # 房產/建案 0.5%
        '261312',  # 體育/籃球 0.5%
        '265001',  # 網推 0.5%
        '261809',  # 寵物 0.5%
        '263306',  # 旺報/綜合 0.5%
        '263302',  # 旺報/兩岸 0.5%
        '261307',  # 體育/棒球 0.5%
        '260423',  # 選舉 0.5%
        '264401',  # 遊戲 0.4%
        '263401',  # 翻爆 0.4%
        '261702',  # 健康/醫藥 0.3%
        '264209',  # 汽車 0.3%
        '264210',  # 機車 0.3%
    ]

    def __init__(self):
        super().__init__()
        self._id_url_map: Dict[int, str] = {}

    @property
    def source_name(self) -> str:
        return "chinatimes"

    def get_url(self, article_id: int) -> str:
        """將 article_id 轉為 URL"""
        if article_id in self._id_url_map:
            return self._id_url_map[article_id]
        # 預設使用 realtimenews 路徑（文章量最大的 section），通用分類碼 260402
        return f"https://www.chinatimes.com/realtimenews/{article_id}-260402"

    def get_candidate_urls(self, article_id: int) -> List[str]:
        """嘗試不同 category code。

        每篇文章只有其正確的 category code 能存取（260402 不是萬用路徑）。
        依文章數量排序嘗試，命中即停止（由 engine 控制）。
        Primary URL 使用 REALTIMENEWS_CATEGORY_CODES[0]，candidates 為其餘。
        """
        return [
            f"https://www.chinatimes.com/realtimenews/{article_id}-{cat}"
            for cat in self.REALTIMENEWS_CATEGORY_CODES[1:]  # skip primary (already in get_url)
        ]

    def get_sitemap_config(self) -> Optional[Dict[str, Any]]:
        """Sitemap 爬取配置（1000 個 sub-sitemap，涵蓋全部歷史文章）"""
        return {
            'index_url': 'https://www.chinatimes.com/sitemaps/sitemap_article_all_index_0.xml',
            'is_index': True,
            'article_url_pattern': r'chinatimes\.com/(realtimenews|newspapers|opinion)/\d{14}-\d{6}',
        }

    def get_list_page_config(self) -> Optional[Dict[str, Any]]:
        """列表頁爬取配置"""
        list_urls = [
            f'https://www.chinatimes.com/realtimenews/{cat}?chdtv'
            for cat in self.CATEGORIES
        ]
        return {
            'list_urls': list_urls,
            'article_url_pattern': r'href="/(realtimenews|newspapers)/(\d{14})-(\d{6})',
            'base_url': 'https://www.chinatimes.com',
        }

    async def get_latest_id(self, session=None) -> Optional[int]:
        """從即時新聞列表頁取得最新 article ID"""
        list_url = "https://www.chinatimes.com/realtimenews?chdtv"

        try:
            self.logger.info(f"Fetching latest ID from: {list_url}")

            if session is None:
                self.logger.error("No session provided")
                return None

            response = await session.get(list_url)

            if response.status_code != 200:
                self.logger.error(f"Failed to fetch list page: {response.status_code}")
                return None

            html = response.text
            soup = BeautifulSoup(html, 'lxml')
            links = soup.select('a[href*="/realtimenews/"]')

            pattern = r'/(realtimenews|newspapers)/(\d{14})-(\d{6})'
            ids = []

            for link in links:
                href = link.get('href', '')
                match = re.search(pattern, href)
                if match:
                    section = match.group(1)
                    article_id = int(match.group(2))
                    category = match.group(3)
                    full_url = f"https://www.chinatimes.com/{section}/{article_id}-{category}"
                    self._id_url_map[article_id] = full_url
                    ids.append(article_id)

            if not ids:
                self.logger.warning("No valid article IDs found")
                return None

            latest_id = max(ids)
            self.logger.info(
                f"Latest ID: {latest_id} (cached {len(self._id_url_map)} URLs)"
            )
            return latest_id

        except Exception as e:
            self.logger.error(f"Error getting latest ID: {e}")
            return None

    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """解析中國時報文章 HTML"""
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

            raw_author = self._extract_author(soup)
            author = TextProcessor.clean_author(raw_author) if raw_author else ""

            paragraphs = self._extract_paragraphs(soup)
            if not paragraphs:
                self.logger.warning(f"No content found: {url}")
                return None

            article_body = TextProcessor.smart_extract_summary(paragraphs)

            if len(article_body) < settings.MIN_ARTICLE_LENGTH:
                self.logger.warning(f"Article too short ({len(article_body)} chars): {url}")
                return None

            keywords = TextProcessor.extract_keywords_from_soup(
                soup, title, settings.MAX_KEYWORDS
            )

            article_data = {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,
                "author": author,
                "datePublished": date_published,
                "publisher": "中國時報",
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords,
            }

            self.logger.info(f"Successfully parsed: {url}")
            return article_data

        except Exception as e:
            self.logger.error(f"Error parsing {url}: {e}")
            return None

    async def get_date(self, article_id: int) -> Optional[datetime]:
        """從 ID 直接解析日期（前 8 碼 = YYYYMMDD）"""
        try:
            id_str = str(article_id)
            if len(id_str) != 14:
                self.logger.warning(f"Invalid ID length: {id_str} (expected 14 digits)")
                return None

            date_str = id_str[:8]
            return datetime.strptime(date_str, '%Y%m%d')

        except (ValueError, Exception) as e:
            self.logger.error(f"Error parsing date from ID {article_id}: {e}")
            return None

    def extract_id_from_url(self, url: str) -> Optional[int]:
        """從 URL 提取 14 位數字 ID"""
        match = re.search(r'/(\d{14})-\d{6}', url)
        if match:
            return int(match.group(1))
        return None

    # ==================== Private helpers ====================

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """提取標題"""
        # 方法 1: h1.article-title
        h1 = soup.select_one('h1.article-title')
        if h1:
            return h1.get_text(strip=True)

        # 方法 2: og:title meta
        meta = soup.find('meta', property='og:title')
        if meta and meta.get('content'):
            return meta['content']

        # 方法 3: 一般 h1
        h1 = soup.find('h1')
        if h1:
            return h1.get_text(strip=True)

        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        """提取發布日期"""
        # 方法 1: meta pubdate
        meta = soup.find('meta', attrs={'name': 'pubdate'})
        if meta and meta.get('content'):
            dt = TextProcessor.parse_iso_date(meta['content'])
            if dt:
                return dt.strftime('%Y-%m-%dT%H:%M:%S')

        # 方法 2: meta article:published_time
        meta = soup.find('meta', property='article:published_time')
        if meta and meta.get('content'):
            dt = TextProcessor.parse_iso_date(meta['content'])
            if dt:
                return dt.strftime('%Y-%m-%dT%H:%M:%S')

        # 方法 3: time element
        time_tag = soup.find('time')
        if time_tag and time_tag.get('datetime'):
            dt = TextProcessor.parse_iso_date(time_tag['datetime'])
            if dt:
                return dt.strftime('%Y-%m-%dT%H:%M:%S')

        return None

    def _extract_author(self, soup: BeautifulSoup) -> str:
        """提取作者"""
        # 方法 1: .meta-info .author a
        author_link = soup.select_one('.meta-info .author a')
        if author_link:
            return author_link.get_text(strip=True)

        # 方法 2: meta author
        meta = soup.find('meta', attrs={'name': 'author'})
        if meta and meta.get('content'):
            return meta['content']

        return ""

    def _extract_paragraphs(self, soup: BeautifulSoup) -> List[str]:
        """提取文章段落"""
        content_div = (
            soup.select_one('div.article-body') or
            soup.select_one('div[itemprop="articleBody"]') or
            soup.select_one('article')
        )

        if not content_div:
            return []

        # 移除雜訊元素
        noise_selectors = [
            'script', 'style', 'iframe', 'aside',
            'div.ad', 'div.ad.rwd', '[id^="div-gpt-ad-"]',
            '.promote-word', '.striking-text',
            '.dable-recommend', '#recommended-article',
            '.subscribe-news-letter', '.social-share',
            '.article-function', 'figure',
            '#donate-form-container',
        ]
        TextProcessor.remove_noise_elements(content_div, noise_selectors)

        paragraphs = []
        for p in content_div.find_all('p'):
            cleaned = TextProcessor.filter_paragraph(
                p.get_text(strip=True),
                min_length=settings.MIN_PARAGRAPH_LENGTH,
                blacklist_terms=[
                    '訂閱', '廣告', '相關新聞', '延伸閱讀',
                    '推薦閱讀', '更多新聞', '請繼續往下閱讀',
                    'APP', '下載',
                ],
            )
            if cleaned:
                paragraphs.append(cleaned)

        return paragraphs
