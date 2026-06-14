"""
esg_businesstoday_parser.py - 今周刊 ESG 解析器

支援兩種爬取模式：
1. **Sitemap 模式**（Backfill 推薦）：從 sitemap.xml 取得全部文章 URL
2. **AJAX 模式**（Daily 推薦）：從分類頁 AJAX 接口取得最新文章

特點：
- 從文章 ID 直接提取日期（YYYYMMDDXXXX，無需網路請求）
- 支援日期過濾
- 嚴格遵守 Schema.org NewsArticle 格式
"""

import re
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from ..core.interfaces import BaseParser, SessionType
from ..core import settings
from ..utils.text_processor import TextProcessor


class EsgBusinessTodayParser(BaseParser):
    """
    今周刊 ESG 解析器

    技術特點：
    - Sitemap 模式：一次取得全部文章（約 1,000+ 篇）
    - AJAX 模式：從分類頁取得最新文章
    - 從文章 ID 直接提取日期（YYYYMMDDXXXX）
    - curl_cffi 偽裝請求繞過 WAF
    """

    BASE_URL = "https://esg.businesstoday.com.tw"
    SITEMAP_URL = "https://esg.businesstoday.com.tw/sitemap.xml"

    # 使用 settings 中的分類
    CATEGORIES = settings.ESG_BT_CATEGORIES

    preferred_session_type = SessionType.CURL_CFFI

    def __init__(
        self,
        count: Optional[int] = None,
        max_pages: Optional[int] = None,
        use_sitemap: bool = False,
        target_date: Optional[datetime] = None,
        **kwargs
    ):
        super().__init__()
        self.count = count or 50
        self.use_sitemap = use_sitemap
        self.target_date = target_date
        self._discovered_ids: List[int] = []
        self._id_url_map: Dict[int, str] = {}

        if count:
            self.max_pages = (count // 10) + 2
        elif max_pages:
            self.max_pages = max_pages
        else:
            self.max_pages = 10

    @property
    def source_name(self) -> str:
        return "esg_businesstoday"

    def is_not_found_redirect(self, request_url: str, response_url: str) -> bool:
        """不存在的文章會 301 redirect 到首頁，curl_cffi 會自動跟隨"""
        return '/post/' in request_url and '/post/' not in response_url

    @staticmethod
    def _get_response_text(response) -> Optional[str]:
        """Extract text from curl_cffi response."""
        return response.text

    def get_url(self, article_id: int) -> str:
        """構建文章 URL (含位數自動修正)"""
        # 1. 優先使用快取
        if article_id in self._id_url_map:
            return self._id_url_map[article_id]

        # 2. 位數修正：處理 14 碼 ID
        id_str = str(article_id)
        if len(id_str) == 14:
            article_id = int(id_str[:-2])
            self.logger.debug(f"URL digit fix: {id_str} (14-digit) -> {article_id} (12-digit)")

        # 3. 備用方案：使用 "全部" 分類
        return f"{self.BASE_URL}/article/category/180686/post/{article_id}"

    def get_sitemap_config(self) -> Optional[Dict[str, Any]]:
        """取得 sitemap 相關配置"""
        return {
            'index_url': self.SITEMAP_URL,
            'is_index': False,  # Single sitemap with ~1000 articles
            # Pattern applied to extracted URL (without <loc> tags)
            'article_url_pattern': r'https?://esg\.businesstoday\.com\.tw/article/',
        }

    def get_discovered_ids(self) -> List[int]:
        """回傳已發現的 ID 列表"""
        return self._discovered_ids

    async def get_latest_id(self, session=None) -> Optional[int]:
        """
        取得最新文章 ID

        根據 use_sitemap 設定選擇模式：
        - Sitemap 模式：從 sitemap.xml 取得全部 URL
        - AJAX 模式：從分類頁掃描
        """
        if self.use_sitemap:
            self.logger.info("Using Sitemap mode for article discovery")
            await self._fetch_sitemap(session)
        else:
            self.logger.info("Using AJAX mode for article discovery")
            await self._scan_categories(session)

        return self._discovered_ids[0] if self._discovered_ids else None

    async def _fetch_sitemap(self, session=None) -> None:
        """從 sitemap.xml 取得全部文章 URL"""
        if session is None:
            self.logger.error("No session provided for sitemap fetch")
            return

        try:
            self.logger.info(f"Fetching sitemap: {self.SITEMAP_URL}")
            response = await session.get(self.SITEMAP_URL)

            if response.status_code != 200:
                self.logger.error(f"Sitemap fetch failed: {response.status_code}")
                return

            xml_text = self._get_response_text(response)

            # 解析 XML（使用 regex 避免依賴 xml 套件）
            url_pattern = r'<loc>([^<]+)</loc>'
            urls = re.findall(url_pattern, xml_text)

            self.logger.info(f"Found {len(urls)} URLs in sitemap")

            found_ids = []
            for url in urls:
                # 只處理文章 URL（/post/）
                match = re.search(r'/post/(\d+)', url)
                if match:
                    article_id = int(match.group(1))
                    found_ids.append(article_id)
                    self._id_url_map[article_id] = url

            # 按日期排序（ID 包含日期，大的較新）
            found_ids.sort(reverse=True)

            # 日期過濾
            if self.target_date:
                filtered_ids = []
                for aid in found_ids:
                    article_date = await self.get_date(aid)
                    if article_date and article_date >= self.target_date:
                        filtered_ids.append(aid)
                found_ids = filtered_ids
                self.logger.info(f"After date filter: {len(found_ids)} articles")

            # 限制數量
            self._discovered_ids = found_ids[:self.count] if self.count else found_ids

            self.logger.info(f"Sitemap: {len(self._discovered_ids)} articles ready for crawling")

        except Exception as e:
            self.logger.error(f"Sitemap fetch error: {e}")

    async def _scan_categories(self, session=None) -> None:
        """掃描所有分類取得文章 ID"""
        found_ids = set()

        for cat_id, cat_name in self.CATEGORIES.items():
            self.logger.info(f"Scanning category: {cat_name} (ID: {cat_id})")

            # 1. 抓取第 1 頁 (靜態)
            url_p1 = f"{self.BASE_URL}/catalog/{cat_id}/"
            page_ids = await self._fetch_ids_from_page(session, url_p1)
            found_ids.update(page_ids)
            self.logger.info(f"  Page 1: Found {len(page_ids)} articles")

            # 2. 抓取第 2-3 頁 (AJAX)
            for page in range(2, min(4, self.max_pages + 1)):
                url_ajax = f"{self.BASE_URL}/catalog/{cat_id}/list/page/{page}/ajax"
                page_ids = await self._fetch_ids_from_page(session, url_ajax)
                found_ids.update(page_ids)
                self.logger.info(f"  Page {page}: Found {len(page_ids)} articles")
                await asyncio.sleep(0.5)  # 禮貌性延遲

                # 提前終止條件
                if len(found_ids) >= self.count * 1.5:
                    self.logger.info(f"Collected enough articles ({len(found_ids)})")
                    break

            await asyncio.sleep(1)  # 分類間延遲

        # 轉為列表並排序 (新到舊)
        sorted_ids = sorted(list(found_ids), reverse=True)
        self._discovered_ids = sorted_ids[:self.count]

        self.logger.info(f"Total found {len(sorted_ids)} articles, keeping first {len(self._discovered_ids)}")

    async def _fetch_ids_from_page(self, session, url: str) -> List[int]:
        """抓取單頁的所有 ID"""
        ids = []
        try:
            if session is None:
                self.logger.error("No session provided for ESG BusinessToday")
                return ids

            response = await session.get(url)

            if response.status_code != 200:
                return ids

            html = self._get_response_text(response)

            soup = BeautifulSoup(html, 'lxml')
            links = soup.select('a.article__item, a.hover-area, a[href*="/post/"]')

            for link in links:
                href = link.get('href', '')
                match = re.search(r'/post/(\d+)', href)
                if match:
                    article_id = int(match.group(1))
                    ids.append(article_id)

                    # 儲存完整 URL
                    if article_id not in self._id_url_map:
                        if href.startswith('/'):
                            full_url = self.BASE_URL + href
                        else:
                            full_url = href
                        self._id_url_map[article_id] = full_url

        except Exception as e:
            self.logger.debug(f"Error fetching page {url}: {e}")

        return ids

    async def get_date(self, article_id: int) -> Optional[datetime]:
        """
        從文章 ID 提取發布日期

        今周刊 ESG 的文章 ID 格式：YYYYMMDDXXXX (12碼)
        - 前 8 位：日期 (YYYYMMDD)
        - 後 4 位：流水號
        """
        try:
            # 位數修正：處理 14 碼 ID
            id_str = str(article_id)

            if len(id_str) == 14:
                id_str = id_str[:-2]
                self.logger.debug(f"ID digit fix: {article_id} (14-digit) -> {id_str} (12-digit)")

            if len(id_str) < 8:
                self.logger.warning(f"Invalid article ID format: {article_id}")
                return None

            # 提取日期部分 (YYYYMMDD)
            date_str = id_str[:8]

            # 解析為 datetime
            date_obj = datetime.strptime(date_str, '%Y%m%d')

            return date_obj

        except ValueError as e:
            self.logger.error(f"Failed to parse date from ID {article_id}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error in get_date for ID {article_id}: {e}")
            return None

    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """解析單篇文章"""
        try:
            soup = BeautifulSoup(html, 'lxml')

            # ========== 0. 首頁重導向偵測 ==========
            # 不存在的文章 ID 會返回 HTTP 200 + 首頁 HTML
            # 首頁特徵：有多個文章列表連結，但沒有 articleBody
            if not soup.select_one('div[itemprop="articleBody"]'):
                post_links = soup.select('a[href*="/post/"]')
                if len(post_links) >= 5:
                    self.logger.info(f"Homepage redirect detected (no articleBody, {len(post_links)} post links): {url}")
                    return None

            # ========== 1. 標題 (headline) ==========
            headline = None

            # 方法 1: h1 標籤
            h1_tag = soup.select_one('div.content_top h1, h1')
            if h1_tag:
                headline = TextProcessor.clean_text(h1_tag.get_text())

            # 方法 2: og:title
            if not headline:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    headline = TextProcessor.clean_text(og_title.get('content', ''))
                    headline = headline.replace('－ESG永續台灣', '').strip()

            if not headline:
                self.logger.warning(f"Cannot extract title: {url}")
                return None

            # ========== 2. 內文 (articleBody) ==========
            article_body = ""

            # 主要內文區域
            content_div = soup.select_one('div[itemprop="articleBody"]')
            if content_div:
                paragraphs = []
                for p in content_div.find_all(['p', 'h2', 'h3']):
                    text = TextProcessor.clean_text(p.get_text())
                    if text and len(text) > 10:
                        paragraphs.append(text)

                article_body = TextProcessor.smart_extract_summary(paragraphs)

            # 補充：摘要區域
            summary_div = soup.select_one('div.articlemark p')
            if summary_div:
                summary_text = TextProcessor.clean_text(summary_div.get_text())
                if summary_text and summary_text not in article_body:
                    article_body = summary_text + "\n\n" + article_body

            if not article_body:
                self.logger.warning(f"Cannot extract content: {url}")
                return None

            # ========== 3. 作者 (author) ==========
            author = "今周刊"

            author_section = soup.select_one('div.author_left')
            if author_section:
                author_text = author_section.get_text()
                match = re.search(r'撰文：\s*(.+?)(?:\s|&nbsp;|分類：)', author_text)
                if match:
                    raw_author = match.group(1).strip()
                    author = TextProcessor.clean_author(raw_author)

            if author == "今周刊":
                meta_author = soup.find('meta', attrs={'name': 'author'})
                if meta_author:
                    author = TextProcessor.clean_author(meta_author.get('content', ''))

            if not isinstance(author, str):
                author = "今周刊"

            # ========== 4. 發布日期 (datePublished) ==========
            date_published = None

            if author_section:
                date_match = re.search(r'日期：(\d{4}-\d{2}-\d{2})', author_section.get_text())
                if date_match:
                    date_str = date_match.group(1)
                    try:
                        dt = datetime.strptime(date_str, '%Y-%m-%d')
                        date_published = dt.isoformat()
                    except Exception:
                        pass

            if not date_published:
                url_match = re.search(r'/post/(\d{8})', url)
                if url_match:
                    date_str = url_match.group(1)
                    try:
                        dt = datetime.strptime(date_str, '%Y%m%d')
                        date_published = dt.isoformat()
                    except Exception:
                        pass

            if not date_published:
                date_published = datetime.now().isoformat()

            # ========== 5. 分類 (keywords) ==========
            keywords = []

            meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
            if meta_keywords:
                kw_text = meta_keywords.get('content', '')
                keywords = [k.strip() for k in kw_text.split(',') if k.strip()]

            breadcrumb = soup.select_one('div.esg-breadcrumb')
            if breadcrumb:
                for link in breadcrumb.find_all('a'):
                    cat_name = TextProcessor.clean_text(link.get_text())
                    if cat_name and cat_name != '首頁' and cat_name not in keywords:
                        keywords.append(cat_name)

            keywords = [str(k) for k in keywords if k]

            # ========== 6. 圖片來源 ==========
            image_source = None
            if author_section:
                img_match = re.search(r'圖檔來源：(.+?)(?:日期：|$)', author_section.get_text())
                if img_match:
                    image_source = TextProcessor.clean_text(img_match.group(1))

            # ========== 7. 主圖 URL ==========
            image_url = None
            main_img = soup.select_one('div.content_top img')
            if main_img:
                image_url = main_img.get('src', '')
                if image_url and not image_url.startswith('http'):
                    image_url = self.BASE_URL + image_url

            # ========== 組合結果 ==========
            result = {
                "@type": "NewsArticle",
                "headline": headline,
                "articleBody": article_body,
                "author": author,
                "publisher": "今周刊 ESG",
                "datePublished": date_published,
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords,
            }

            if image_source:
                result["imageSource"] = image_source
            if image_url:
                result["imageUrl"] = image_url

            return result

        except Exception as e:
            self.logger.error(f"Parse error ({url}): {e}")
            import traceback
            traceback.print_exc()
            return None
