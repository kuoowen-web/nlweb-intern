"""
udn_parser.py - 聯合新聞網解析器

✅ Schema 標準化 + Keywords 實作
- 優先從 meta 標籤提取
- 備用：自動提取關鍵字
"""

import re
import json
import ssl
import logging
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from src.core.interfaces import BaseParser
from src.utils.text_processor import TextProcessor
from src.features.html_analyzer import HTMLAnalyzer


class UdnParser(BaseParser):
    """
    聯合新聞網解析器
    
    職責：
    1. 解析 UDN HTML 結構
    2. 提取新聞內容（標題、日期、作者、內文）
    3. 提取關鍵字（meta 標籤 + 自動提取）
    4. 提供 URL 構建邏輯
    5. 提供最新 ID 查詢
    6. 提供輕量級日期查詢
    
    不負責：
    - HTTP 請求（由 Engine 處理）
    - 爬取流程控制（由 Engine 處理）
    - 資料儲存（由 Pipeline 處理）
    
    URL 結構：
    - /news/story/{CATEGORY_ID}/{ARTICLE_ID}
    - 預設 Category: 6656 (政治/要聞)
    """
    
    DEFAULT_CATEGORY = "6656"
    
    COMMON_CATEGORIES = [
        "6656",  # 政治
        "7088",  # 生活
        "7314",  # 社會
        "6809",  # 全球
        "7238",  # 地方
        "7239",  # 兩岸
        "6885",  # 產經
    ]
    
    def __init__(self):
        """初始化解析器"""
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @property
    def source_name(self) -> str:
        """來源名稱"""
        return "udn"
    
    def get_url(self, article_id: int) -> str:
        """根據文章 ID 構建 URL"""
        return f"https://udn.com/news/story/{self.DEFAULT_CATEGORY}/{article_id}"
    
    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        解析聯合新聞網文章 HTML
        
        ✅ Schema 標準化 + Keywords 實作
        """
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # ========== 提取基本資訊 ==========
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
            
            # ========== 提取內文段落 ==========
            paragraphs = self._extract_paragraphs(soup)
            if not paragraphs:
                self.logger.warning(f"No content found: {url}")
                return None
            
            # ========== 使用 TextProcessor 處理 ==========
            article_body = TextProcessor.smart_extract_summary(paragraphs)
            
            # 驗證內容長度
            if len(article_body) < 50:
                self.logger.warning(f"Article too short: {url}")
                return None
            
            # ========== ✅ 提取關鍵字 ==========
            keywords = self._extract_keywords(soup, title, article_body)
            
            # ========== 組裝標準格式 ==========
            article_data = {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,
                "author": author,
                "datePublished": date_published,
                "publisher": "聯合新聞網",
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords  # ✅ 實際提取的關鍵字
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
        """
        提取關鍵字
        
        策略：
        1. 優先從 meta 標籤提取
        2. 備用：使用 TextProcessor 自動提取
        
        Args:
            soup: BeautifulSoup 物件
            title: 文章標題
            article_body: 文章內文
            
        Returns:
            關鍵字列表（最多 10 個）
        """
        keywords = []
        
        # ========== 方法 1：從 meta 標籤提取 ==========
        # 嘗試 1: <meta name="keywords" content="...">
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords and meta_keywords.get('content'):
            content = meta_keywords['content']
            # 分割關鍵字（支援逗號、頓號、分號）
            keywords = [
                kw.strip() 
                for kw in re.split(r'[,，、;；]', content) 
                if kw.strip()
            ]
        
        # 嘗試 2: <meta property="article:tag" content="...">
        if not keywords:
            article_tags = soup.find_all('meta', property='article:tag')
            keywords = [
                tag['content'].strip() 
                for tag in article_tags 
                if tag.get('content')
            ]
        
        # 嘗試 3: <meta name="news_keywords" content="...">
        if not keywords:
            news_keywords = soup.find('meta', attrs={'name': 'news_keywords'})
            if news_keywords and news_keywords.get('content'):
                content = news_keywords['content']
                keywords = [
                    kw.strip() 
                    for kw in re.split(r'[,，、;；]', content) 
                    if kw.strip()
                ]
        
        # ========== 方法 2：自動提取（備用） ==========
        if not keywords:
            # 檢查 TextProcessor 是否有 extract_keywords 方法
            if hasattr(TextProcessor, 'extract_keywords'):
                try:
                    text = f"{title} {article_body}"
                    keywords = TextProcessor.extract_keywords(text, top_n=10)
                except Exception as e:
                    self.logger.debug(f"Auto keyword extraction failed: {e}")
                    keywords = []
            else:
                # 簡易備用方案：從標題提取名詞
                keywords = self._simple_keyword_extraction(title)
        
        # 限制數量（最多 10 個）
        return keywords[:10]
    
    def _simple_keyword_extraction(self, title: str) -> List[str]:
        """
        簡易關鍵字提取（備用方案）
        
        策略：
        - 移除常見停用詞
        - 提取 2-4 字的詞組
        
        Args:
            title: 文章標題
            
        Returns:
            關鍵字列表
        """
        # 常見停用詞
        stopwords = {
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
            '都', '一', '一個', '上', '也', '很', '到', '說', '要', '去',
            '你', '會', '著', '沒有', '看', '好', '自己', '這'
        }
        
        # 移除標點符號
        title_clean = re.sub(r'[^\w\s]', ' ', title)
        
        # 分詞（簡易版：按空格和常見分隔符）
        words = title_clean.split()
        
        # 過濾：2-4 字且不在停用詞中
        keywords = [
            word for word in words 
            if 2 <= len(word) <= 4 and word not in stopwords
        ]
        
        return keywords[:5]  # 最多 5 個
    
    async def get_latest_id(self) -> Optional[int]:
        """取得 UDN 當前最新文章 ID"""
        list_url = "https://udn.com/news/breaknews/1"
        
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
            
            soup = BeautifulSoup(html, 'lxml')
            links = soup.select('h2 a[href]')
            
            if not links:
                self.logger.warning("No article links found in list page")
                return None
            
            pattern = r'/news/story/(\d+)/(\d+)'
            ids = []
            
            for link in links:
                href = link.get('href', '')
                match = re.search(pattern, href)
                if match:
                    article_id = int(match.group(2))
                    ids.append(article_id)
            
            if not ids:
                self.logger.warning("No article IDs extracted from links")
                return None
            
            latest_id = max(ids)
            self.logger.info(f"Latest ID: {latest_id}")
            return latest_id
            
        except Exception as e:
            self.logger.error(f"Error getting latest ID: {e}")
            return None
    
    async def get_date(self, article_id: int) -> Optional[datetime]:
        """輕量級日期提取"""
        self.logger.debug(f"Fetching date for article ID: {article_id}")
        
        try:
            ssl_context = ssl.create_default_context()
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                for category in self.COMMON_CATEGORIES:
                    url = f"https://udn.com/news/story/{category}/{article_id}"
                    date = await self._extract_date_lightweight(session, url)
                    if date:
                        self.logger.debug(f"Found date in category {category}: {date}")
                        return date
            
            self.logger.debug(f"Article ID {article_id} not found in any category")
            return None
            
        except Exception as e:
            self.logger.error(f"Error in get_date for article {article_id}: {e}")
            return None
    
    async def _extract_date_lightweight(
        self, 
        session: aiohttp.ClientSession, 
        url: str
    ) -> Optional[datetime]:
        """輕量級日期提取：只解析 meta 標籤和 time 元素"""
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
            
            time_tag = soup.select_one('time.article-content__time')
            if time_tag:
                datetime_attr = time_tag.get('datetime')
                if datetime_attr:
                    try:
                        date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', datetime_attr)
                        return datetime.fromisoformat(date_str)
                    except Exception:
                        pass
                
                time_str = time_tag.get_text(strip=True)
                return self._parse_date_string(time_str)
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Error extracting date from {url}: {e}")
            return None
    
    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """提取標題"""
        title_tag = soup.select_one('h1.article-content__title')
        
        if title_tag:
            return title_tag.get_text(strip=True)
        
        return None
    
    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        """提取發布時間（完整版，返回字串）"""
        time_tag = soup.select_one('time.article-content__time')
        if time_tag:
            datetime_attr = time_tag.get('datetime')
            if datetime_attr:
                try:
                    date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', datetime_attr)
                    date_obj = datetime.fromisoformat(date_str)
                    return date_obj.strftime('%Y-%m-%dT%H:%M:%S')
                except Exception:
                    pass
            
            time_str = time_tag.get_text(strip=True)
            date_obj = self._parse_date_string(time_str)
            if date_obj:
                return date_obj.strftime('%Y-%m-%dT%H:%M:%S')
        
        return None
    
    def _parse_date_string(self, time_str: str) -> Optional[datetime]:
        """解析 UDN 的時間字串"""
        try:
            if '-' in time_str and ':' in time_str:
                return datetime.strptime(time_str, '%Y-%m-%d %H:%M')
            if '/' in time_str and ':' in time_str:
                return datetime.strptime(time_str, '%Y/%m/%d %H:%M')
        except Exception:
            pass
        
        return None
    
    def _extract_raw_author(self, soup: BeautifulSoup) -> str:
        """提取原始作者資訊"""
        author_tag = soup.select_one('span.article-content__author')
        if author_tag:
            return author_tag.get_text(strip=True)
        
        author_tag = soup.select_one('div.article-content__author')
        if author_tag:
            return author_tag.get_text(strip=True)
        
        content_section = soup.select_one('section.article-content__editor')
        if content_section:
            first_p = content_section.find('p')
            if first_p:
                text = first_p.get_text(strip=True)
                if '記者' in text and len(text) < 100:
                    return text
        
        return ""
    
    def _extract_paragraphs(self, soup: BeautifulSoup) -> List[str]:
        """提取內文段落"""
        content_section = soup.select_one('section.article-content__editor')
        
        if not content_section:
            return []
        
        noise_selectors = [
            'script', 'style', 'iframe', 'aside',
            '.im-pr',
            '.article-content__ad',
            '.inline-ads',
            '.related-articles',
        ]
        
        for selector in noise_selectors:
            for element in content_section.select(selector):
                element.decompose()
        
        paragraphs = []
        for p in content_section.find_all('p'):
            text = p.get_text(strip=True)
            
            if (text and 
                len(text) > 20 and 
                '訂閱' not in text and 
                '廣告' not in text and 
                '相關新聞' not in text and 
                '延伸閱讀' not in text and
                '推薦閱讀' not in text):
                
                cleaned = TextProcessor.clean_text(text)
                if cleaned:
                    paragraphs.append(cleaned)
        
        return paragraphs
