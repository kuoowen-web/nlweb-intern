"""
moea_parser.py - 經濟部新聞解析器 (v2.6.1 - 緊急修復)

✅ 緊急修復：恢復原本的內文提取邏輯
- 使用原本的 _extract_content() 方法
- 將完整內文分段後使用智慧摘要
"""

import re
import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from bs4 import BeautifulSoup

from curl_cffi.requests import AsyncSession

from src.core.interfaces import BaseParser, SessionType
from src.utils.text_processor import TextProcessor
from src.features.html_analyzer import HTMLAnalyzer


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
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
}


class MoeaParser(BaseParser):
    """經濟部解析器 (v2.6.1 - 緊急修復)"""
    
    preferred_session_type = SessionType.CURL_CFFI
    
    def __init__(
        self,
        count: Optional[int] = None,
        max_pages: Optional[int] = None,
        target_date: Optional[datetime] = None
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._id_url_map: Dict[int, str] = {}
        
        if count:
            estimated_pages = math.ceil(count / 10) + 2
            self.max_pages = estimated_pages
            self.logger.info(
                f"🎯 Target count: {count} => Auto-configured max_pages: {self.max_pages}"
            )
        elif max_pages:
            self.max_pages = max_pages
            self.logger.info(f"📄 Using provided max_pages: {self.max_pages}")
        else:
            self.max_pages = 5
            self.logger.info(f"📄 Using default max_pages: {self.max_pages}")
        
        self.target_date = target_date
        self.pages_crawled = 0
        self.total_articles_found = 0
    
    @property
    def source_name(self) -> str:
        return "moea"
    
    def get_url(self, article_id: int) -> str:
        if article_id in self._id_url_map:
            cached_url = self._id_url_map[article_id]
            self.logger.debug(f"Using cached URL for ID {article_id}: {cached_url}")
            return cached_url
        
        self.logger.warning(
            f"ID {article_id} not in cache. "
            f"Please run get_latest_id() first to build URL map."
        )
        return None
    
    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        解析經濟部文章 HTML
        
        ✅ 緊急修復 (v2.6.1)
        - 恢復原本的 _extract_content() 方法
        - 將完整內文分段後使用智慧摘要
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
            
            org = self._extract_org(soup)
            
            # ========== ✅ 使用原本的方法提取內文 ==========
            content = self._extract_content(soup)
            if not content:
                self.logger.warning(f"No content found: {url}")
                return None
            
            if len(content) < 50:
                self.logger.warning(f"Article too short: {url}")
                return None
            
            # ========== ✅ 將完整內文分段後使用智慧摘要 ==========
            paragraphs = self._split_content_to_paragraphs(content)
            article_body = TextProcessor.smart_extract_summary(paragraphs)
            
            # ========== ✅ 提取關鍵字 ==========
            keywords = self._extract_keywords(soup, title, article_body)
            
            # ========== ✅ 組裝標準格式 ==========
            article_data = {
                "@type": "NewsArticle",
                "headline": TextProcessor.clean_text(title),
                "articleBody": article_body,  # ✅ 智慧摘要
                "author": org or "",  # ✅ 字串格式
                "datePublished": date_published,
                "publisher": "經濟部",  # ✅ 字串格式
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords  # ✅ 新增欄位
            }
            
            self.logger.info(f"Successfully parsed: {url}")
            return article_data
            
        except Exception as e:
            self.logger.error(f"Error parsing {url}: {e}")
            return None
    
    def _split_content_to_paragraphs(self, content: str) -> List[str]:
        """
        將完整內文分段
        
        策略：
        - 按照換行符分段
        - 過濾過短的段落（< 20 字）
        
        Args:
            content: 完整內文
            
        Returns:
            段落列表
        """
        # 按照雙換行或單換行分段
        paragraphs = re.split(r'\n\n+|\n', content)
        
        # 過濾過短的段落
        filtered = []
        for p in paragraphs:
            p = p.strip()
            if len(p) >= 20:
                filtered.append(p)
        
        return filtered
    
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
        
        # 方法 2：從分類提取
        if not keywords:
            kind = self._extract_kind(soup)
            if kind:
                keywords.append(kind)
        
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
    
    async def get_latest_id(self) -> Optional[int]:
        """取得經濟部當前最新文章 ID"""
        list_url = "https://www.moea.gov.tw/MNS/populace/news/NewsQuery.aspx?menu_id=45"
        
        try:
            self.logger.info(f"🚀 Starting multi-page crawl (max {self.max_pages} pages)")
            if self.target_date:
                self.logger.info(f"   📅 Date filter: {self.target_date.strftime('%Y-%m-%d')}")
            
            async with AsyncSession() as session:
                self.logger.info(f"📄 Fetching page 1...")
                response = await session.get(
                    list_url,
                    headers=DEFAULT_HEADERS,
                    timeout=15,
                    impersonate="chrome120"
                )
                
                if response.status_code != 200:
                    self.logger.error(f"Failed to fetch page 1: {response.status_code}")
                    return None
                
                html = response.text
                soup = BeautifulSoup(html, 'lxml')
                
                should_stop = await self._extract_articles_from_page(soup, page_num=1)
                self.pages_crawled = 1
                
                if should_stop:
                    self.logger.info(f"✅ Reached target date, stopping at page 1")
                    return self._get_max_id()
                
                for page_num in range(2, self.max_pages + 1):
                    self.logger.info(f"📄 Fetching page {page_num}...")
                    
                    button_name = self._extract_page_button_name(soup, page_num)
                    
                    if not button_name:
                        self.logger.warning(
                            f"Cannot find page button for page {page_num}, stopping at page {page_num - 1}"
                        )
                        break
                    
                    self.logger.debug(
                        f"   🎯 Page button: name='{button_name}', value=' {page_num} '"
                    )
                    
                    viewstate_data = self._extract_viewstate(soup)
                    if not viewstate_data:
                        self.logger.warning(f"Failed to extract ViewState, stopping at page {page_num - 1}")
                        break
                    
                    post_data = {
                        button_name: f" {page_num} ",
                        **viewstate_data
                    }
                    
                    response = await session.post(
                        list_url,
                        headers={
                            **DEFAULT_HEADERS,
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                        data=post_data,
                        timeout=15,
                        impersonate="chrome120"
                    )
                    
                    if response.status_code != 200:
                        self.logger.error(f"Failed to fetch page {page_num}: {response.status_code}")
                        break
                    
                    html = response.text
                    soup = BeautifulSoup(html, 'lxml')
                    
                    should_stop = await self._extract_articles_from_page(soup, page_num)
                    self.pages_crawled = page_num
                    
                    if should_stop:
                        self.logger.info(f"✅ Reached target date, stopping at page {page_num}")
                        break
                    
                    if not self._has_next_page(soup, page_num + 1):
                        self.logger.info(f"✅ No more pages, stopping at page {page_num}")
                        break
            
            max_id = self._get_max_id()
            
            self.logger.info(f"")
            self.logger.info(f"📊 Crawl Summary:")
            self.logger.info(f"   Pages crawled: {self.pages_crawled}")
            self.logger.info(f"   Articles found: {self.total_articles_found}")
            self.logger.info(f"   Latest ID: {max_id}")
            
            return max_id
            
        except Exception as e:
            self.logger.error(f"Error in get_latest_id: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    async def get_date(self, article_id: int) -> Optional[datetime]:
        self.logger.debug(
            f"MOEA ID {article_id} is not date-based, returning None"
        )
        return None
    
    def get_cached_url_count(self) -> int:
        return len(self._id_url_map)
    
    def clear_url_cache(self) -> None:
        self._id_url_map.clear()
        self.logger.info("URL cache cleared")
    
    def get_crawl_stats(self) -> Dict[str, int]:
        return {
            'pages_crawled': self.pages_crawled,
            'articles_found': self.total_articles_found,
            'urls_cached': len(self._id_url_map)
        }
    
    def get_discovered_ids(self) -> List[int]:
        if not self._id_url_map:
            self.logger.warning(
                "No IDs discovered yet. "
                "Please run get_latest_id() first to populate the ID map."
            )
            return []
        
        discovered_ids = sorted(self._id_url_map.keys(), reverse=True)
        
        self.logger.debug(
            f"Returning {len(discovered_ids)} discovered IDs "
            f"(range: {discovered_ids[0]} → {discovered_ids[-1]})"
        )
        
        return discovered_ids
    
    async def _extract_articles_from_page(
        self,
        soup: BeautifulSoup,
        page_num: int
    ) -> bool:
        links = soup.select('a[href*="News.aspx"]')
        
        if not links:
            self.logger.warning(f"No article links found on page {page_num}")
            return False
        
        pattern = r'news_id=(\d+)'
        page_articles = 0
        should_stop = False
        
        for link in links:
            href = link.get('href', '')
            match = re.search(pattern, href)
            if not match:
                continue
            
            article_id = int(match.group(1))
            
            if href.startswith('../'):
                full_url = f"https://www.moea.gov.tw/MNS/populace/{href[3:]}"
            elif href.startswith('/'):
                full_url = f"https://www.moea.gov.tw{href}"
            else:
                full_url = f"https://www.moea.gov.tw/MNS/populace/news/{href}"
            
            self._id_url_map[article_id] = full_url
            page_articles += 1
            
            if self.target_date:
                article_date = self._extract_date_from_list_item(link)
                if article_date and article_date < self.target_date:
                    self.logger.info(
                        f"   📅 Found article older than target date: "
                        f"{article_date.strftime('%Y-%m-%d')} < "
                        f"{self.target_date.strftime('%Y-%m-%d')}"
                    )
                    should_stop = True
                    break
        
        self.total_articles_found += page_articles
        self.logger.info(f"   ✅ Page {page_num}: Found {page_articles} articles")
        
        return should_stop
    
    def _extract_date_from_list_item(self, link_element) -> Optional[datetime]:
        try:
            parent = link_element.find_parent('tr')
            if not parent:
                return None
            
            tds = parent.find_all('td')
            for td in tds:
                text = td.get_text(strip=True)
                date_match = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})', text)
                if date_match:
                    date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                    return datetime.strptime(date_str, '%Y-%m-%d')
        except Exception as e:
            self.logger.debug(f"Failed to extract date from list item: {e}")
        
        return None
    
    def _extract_page_button_name(
        self,
        soup: BeautifulSoup,
        target_page: int
    ) -> Optional[str]:
        button = soup.find('input', {
            'type': 'submit',
            'value': f" {target_page} "
        })
        
        if button and button.get('name'):
            button_name = button['name']
            self.logger.debug(
                f"   🔍 Found page button: name='{button_name}', value=' {target_page} '"
            )
            return button_name
        
        self.logger.warning(
            f"   ⚠️  Cannot find page button for page {target_page}"
        )
        return None
    
    def _extract_viewstate(self, soup: BeautifulSoup) -> Optional[Dict[str, str]]:
        try:
            viewstate = soup.find('input', {'name': '__VIEWSTATE'})
            viewstate_generator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
            event_validation = soup.find('input', {'name': '__EVENTVALIDATION'})
            
            if not viewstate or not viewstate.get('value'):
                self.logger.error("Failed to find __VIEWSTATE")
                return None
            
            data = {
                '__VIEWSTATE': viewstate['value'],
            }
            
            if viewstate_generator and viewstate_generator.get('value'):
                data['__VIEWSTATEGENERATOR'] = viewstate_generator['value']
            
            if event_validation and event_validation.get('value'):
                data['__EVENTVALIDATION'] = event_validation['value']
            
            self.logger.debug(f"   🔐 Extracted ViewState (length: {len(data['__VIEWSTATE'])})")
            return data
            
        except Exception as e:
            self.logger.error(f"Error extracting ViewState: {e}")
            return None
    
    def _has_next_page(self, soup: BeautifulSoup, next_page_num: int) -> bool:
        next_button = soup.find('input', {
            'type': 'submit',
            'value': f" {next_page_num} "
        })
        
        if next_button and not next_button.get('disabled'):
            return True
        
        next_page_button = soup.find('input', {
            'type': 'submit',
            'value': '下一頁'
        })
        
        if next_page_button and not next_page_button.get('disabled'):
            return True
        
        return False
    
    def _get_max_id(self) -> Optional[int]:
        if not self._id_url_map:
            return None
        return max(self._id_url_map.keys())
    
    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        title_tag = soup.select_one('h2.div-info-title')
        if title_tag:
            return title_tag.get_text(strip=True)
        
        meta_title = soup.find('meta', property='og:title')
        if meta_title and meta_title.get('content'):
            return meta_title['content']
        
        return None
    
    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        date_tag = soup.select_one('.div-begin-date')
        if date_tag:
            date_str = date_tag.get_text(strip=True)
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                return date_obj.strftime('%Y-%m-%dT%H:%M:%S')
            except ValueError:
                self.logger.warning(f"Invalid date format: {date_str}")
        
        return None
    
    def _extract_org(self, soup: BeautifulSoup) -> Optional[str]:
        org_tag = soup.select_one('.div-org-name')
        if org_tag:
            return org_tag.get_text(strip=True)
        return None
    
    def _extract_kind(self, soup: BeautifulSoup) -> Optional[str]:
        kind_tag = soup.select_one('.div-info-kind')
        if kind_tag:
            text = kind_tag.get_text(strip=True)
            text = re.sub(r'^[→➜►▶]\s*', '', text)
            return text
        return None
    
    def _extract_content(self, soup: BeautifulSoup) -> Optional[str]:
        """
        提取內文（原本的方法）
        
        ✅ 恢復原本的邏輯
        """
        content_div = soup.select_one('.div-left-info')
        if not content_div:
            return None
        
        html_content = content_div.decode_contents()
        html_content = html_content.replace('<br>', '\n').replace('<br/>', '\n')
        
        text_soup = BeautifulSoup(html_content, 'lxml')
        text = text_soup.get_text()
        
        text = TextProcessor.clean_text(text)
        
        return text if text else None
