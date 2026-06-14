"""
cna_parser.py - 中央社新聞解析器

✅ 派工單 #922-B: Schema 標準化
- 移除 @context
- author 改為字串
- publisher 改為字串
- 新增 keywords 欄位
- 移除 _analysis_data
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from curl_cffi.requests import AsyncSession

from src.core.interfaces import BaseParser
from src.utils.text_processor import TextProcessor
from src.features.html_analyzer import HTMLAnalyzer


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class CnaParser(BaseParser):
    """中央社解析器"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
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
    
    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        解析中央社文章 HTML
        
        ✅ Schema 標準化 (派工單 #922-B)
        """
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
            
            if len(article_body) < 50:
                self.logger.warning(f"Article too short: {url}")
                return None
            
            # ========== ✅ 提取關鍵字 ==========
            keywords = self._extract_keywords(soup, title, article_body)
            
            # ========== ✅ 組裝標準格式 ==========
            article_data = {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,
                "author": author,  # ✅ 字串格式
                "datePublished": date_published,
                "publisher": "中央社",  # ✅ 字串格式
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords  # ✅ 新增欄位
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
        
        return keywords[:10]
    
    def _simple_keyword_extraction(self, title: str) -> List[str]:
        """簡易關鍵字提取"""
        stopwords = {
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
            '都', '一', '一個', '上', '也', '很', '到', '說', '要', '去',
            '你', '會', '著', '沒有', '看', '好', '自己', '這'
        }
        
        title_clean = re.sub(r'[^\w\s]', ' ', title)
        words = title_clean.split()
        
        keywords = [
            word for word in words 
            if 2 <= len(word) <= 4 and word not in stopwords
        ]
        
        return keywords[:5]
    
    async def get_latest_id(self) -> Optional[int]:
        """取得中央社當前最新文章 ID"""
        list_url = "https://www.cna.com.tw/list/aall.aspx"
        
        try:
            self.logger.info(f"Fetching latest ID from: {list_url}")
            
            async with AsyncSession() as session:
                response = await session.get(
                    list_url,
                    headers=DEFAULT_HEADERS,
                    timeout=10,
                    impersonate="chrome120"
                )
                
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
                    
                    if str(article_id).endswith('5001'):
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
                f"(cached {len(self._id_url_map)} URLs, excluded *5001 articles)"
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
        author_tag = soup.select_one('.author')
        if author_tag:
            return author_tag.get_text(strip=True)
        
        reporter_tag = soup.select_one('.reporter')
        if reporter_tag:
            return reporter_tag.get_text(strip=True)
        
        byline_tag = soup.select_one('.byline')
        if byline_tag:
            return byline_tag.get_text(strip=True)
        
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
