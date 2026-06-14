"""
einfo_parser.py - 環境資訊中心解析器

✅ 派工單 #922-B: Schema 標準化
- 完全重構輸出格式
- 使用智慧摘要
- 新增 keywords 欄位
- 符合標準 Schema
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import re
from bs4 import BeautifulSoup
from curl_cffi import requests
from src.core.interfaces import BaseParser, SessionType
from src.utils.text_processor import TextProcessor


class EInfoParser(BaseParser):
    """環境資訊中心 (E-Info) Parser"""
    
    BASE_URL = "https://e-info.org.tw"
    CATEGORY_URLS = [
        "https://e-info.org.tw/taxonomy/term/258/all",
        "https://e-info.org.tw/taxonomy/term/266",
        "https://e-info.org.tw/taxonomy/term/35283/all"
    ]
    
    preferred_session_type = SessionType.CURL_CFFI

    def __init__(
        self, 
        count: Optional[int] = None,
        start_id: Optional[int] = None,
        target_date: Optional[datetime] = None,
        **kwargs
    ):
        super().__init__()
        self.count = count or 50
        self.start_id = start_id
        self.target_date = target_date
        self._discovered_ids: List[int] = []

    @property
    def source_name(self) -> str:
        return "einfo"
        
    async def get_latest_id(self) -> Optional[int]:
        """動態獲取最新 ID"""
        try:
            if self.start_id:
                latest_id = self.start_id
            else:
                latest_id = await self._fetch_latest_id_from_lists()
                if not latest_id:
                    print("⚠️  無法偵測最新 ID，使用預設值 242797")
                    latest_id = 242797
            
            self._discovered_ids = list(range(
                latest_id,
                latest_id - self.count,
                -1
            ))
            
            if self._discovered_ids:
                print(f"✅ 偵測到最新 ID: {latest_id}")
                print(f"📊 將抓取 {len(self._discovered_ids)} 篇文章 (ID: {latest_id} → {self._discovered_ids[-1]})")
                return self._discovered_ids[0]
            
            return None
            
        except Exception as e:
            print(f"❌ get_latest_id 錯誤: {e}")
            return None
    
    async def _fetch_latest_id_from_lists(self) -> Optional[int]:
        """從三個列表頁提取最大的 Node ID"""
        max_id = 0
        
        for url in self.CATEGORY_URLS:
            try:
                print(f"🔍 偵查列表頁: {url}")
                response = requests.get(
                    url, 
                    impersonate="chrome110", 
                    timeout=30
                )
                
                if response.status_code != 200:
                    continue
                
                soup = BeautifulSoup(response.text, 'lxml')
                node_links = soup.find_all('a', href=re.compile(r'/node/(\d+)'))
                
                for link in node_links:
                    href = link.get('href', '')
                    match = re.search(r'/node/(\d+)', href)
                    if match:
                        node_id = int(match.group(1))
                        max_id = max(max_id, node_id)
                
                print(f"   ✓ 找到 {len(node_links)} 個連結，最大 ID: {max_id}")
                
            except Exception as e:
                print(f"⚠️  列表頁抓取失敗 ({url}): {e}")
                continue
        
        return max_id if max_id > 0 else None
    
    def get_discovered_ids(self) -> List[int]:
        return self._discovered_ids
    
    def get_url(self, article_id: int) -> str:
        return f"{self.BASE_URL}/node/{article_id}"
    
    async def get_date(self, article_id: int) -> Optional[datetime]:
        """給 Navigator 用（回傳 datetime 物件）"""
        try:
            url = self.get_url(article_id)
            response = requests.get(
                url, 
                impersonate="chrome110", 
                timeout=30
            )
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'lxml')
            date_str = self._extract_date(soup)
            if not date_str: 
                return None
            
            return self._parse_date(date_str)
            
        except Exception:
            return None
    
    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        解析 HTML 內容
        
        ✅ Schema 標準化 (派工單 #922-B)
        """
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            match = re.search(r'/node/(\d+)', url)
            article_id = int(match.group(1)) if match else 0

            title = self._extract_title(soup)
            if not title: 
                return None
            
            date_str = self._extract_date(soup)
            if not date_str: 
                return None
            
            published_date = self._parse_date(date_str)
            if not published_date: 
                return None
            
            # 日期過濾
            if self.target_date and published_date < self.target_date:
                return None
            
            # ========== ✅ 使用智慧摘要 ==========
            paragraphs = self._extract_paragraphs(soup)
            if not paragraphs:
                return None
            
            article_body = TextProcessor.smart_extract_summary(paragraphs)
            
            if len(article_body) < 50:
                return None
            
            author = self._extract_author(soup)
            
            # ========== ✅ 提取關鍵字 ==========
            keywords = self._extract_keywords(soup, title, article_body)
            
            # ========== ✅ 組裝標準格式 ==========
            return {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,  # ✅ 智慧摘要
                "author": author or "",  # ✅ 字串格式
                "datePublished": published_date.strftime('%Y-%m-%dT%H:%M:%S'),
                "publisher": "環境資訊中心",  # ✅ 字串格式
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords  # ✅ 新增欄位
            }
            
        except Exception as e:
            print(f"❌ 解析錯誤: {e}")
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
        
        # 方法 2：從分類標籤提取
        if not keywords:
            category_links = soup.select('.field-name-field-category a, .tags a')
            keywords = [
                link.get_text(strip=True) 
                for link in category_links
            ]
        
        # 方法 3：簡易提取
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
        article_tag = soup.select_one('article')
        if not article_tag:
            return None
        
        text = article_tag.get_text(strip=True)
        patterns = [
            r'環境資訊中心記者\s+([^報導]+)報導',
            r'文：([^（）]+)',
            r'作者[：:]\s*([^\n]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                author_name = match.group(1).strip()
                return TextProcessor.clean_author(author_name)
        
        return None
