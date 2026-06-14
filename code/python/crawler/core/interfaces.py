"""
interfaces.py - Parser 介面定義

定義所有新聞網站 Parser 必須實作的標準合約。
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from datetime import datetime
from enum import Enum
import logging


class SessionType(Enum):
    """
    Session 類型列舉

    用途：指定 Parser 偏好的 HTTP Session 類型
    - AIOHTTP: 標準的 aiohttp.ClientSession（適用於大部分網站）
    - CURL_CFFI: curl_cffi.AsyncSession（適用於反爬強的網站，如 CNA）
    """
    AIOHTTP = "aiohttp"
    CURL_CFFI = "curl_cffi"


class BaseParser(ABC):
    """
    Parser 基底介面
    定義所有新聞網站 Parser 必須實作的標準合約

    設計原則：
    1. 只定義介面，不包含實作邏輯
    2. 讓爬蟲引擎只依賴此介面，不依賴具體網站
    3. 所有具體的 Parser 必須繼承此類別並實作所有抽象方法
    """

    # 子類別可以覆寫此屬性來指定偏好的 Session 類型
    preferred_session_type: Optional[SessionType] = None

    def __init__(self):
        """
        初始化 Parser

        自動設定 logger，使用類別名稱作為 logger 名稱。
        子類別應呼叫 super().__init__() 來繼承此行為。
        """
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def source_name(self) -> str:
        """
        回傳新聞來源代號

        Returns:
            來源代號，如 'ltn', 'udn', 'cna' 等

        範例:
            >>> parser.source_name
            'ltn'
        """
        pass

    @abstractmethod
    def get_url(self, article_id: int) -> str:
        """
        將文章 ID 轉換為完整的文章 URL

        Args:
            article_id: 文章 ID（整數）

        Returns:
            完整的文章 URL

        範例:
            >>> parser.get_url(4567890)
            'https://news.ltn.com.tw/news/life/breakingnews/4567890'
        """
        pass

    def get_candidate_urls(self, article_id: int) -> List[str]:
        """
        Return alternative URLs to try if primary URL (get_url) fails to parse.
        Default: no alternatives. Override for multi-category sources.
        """
        return []

    @abstractmethod
    async def get_latest_id(self, session=None) -> Optional[int]:
        """
        取得該網站目前最新的文章 ID

        用途：
        1. 確定爬取範圍的上界
        2. 用於增量更新（只爬取新文章）

        Args:
            session: HTTP Session 實例（可選）

        Returns:
            最新的文章 ID，或 None（如果獲取失敗）

        範例:
            >>> await parser.get_latest_id(session)
            4567890
        """
        pass

    @abstractmethod
    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        解析文章 HTML，提取結構化資料

        要求：
        1. 回傳的字典必須符合 Schema.org NewsArticle 格式
        2. 必須包含必要欄位：headline, datePublished, articleBody
        3. 如果內文過短（< 100 字）或無效，必須回傳 None
        4. 必須包含 HTML 特徵（用於後續分析）

        Args:
            html: 文章的 HTML 內容
            url: 文章的 URL

        Returns:
            符合 Schema.org NewsArticle 格式的字典，或 None（如果解析失敗或內容無效）

        回傳格式範例:
            {
                '@context': 'https://schema.org',
                '@type': 'NewsArticle',
                'headline': '文章標題',
                'datePublished': '2025-12-08T10:30:00',
                'dateModified': '2025-12-08T11:00:00',  # 可選
                'author': {
                    '@type': 'Person',
                    'name': '記者姓名'
                },
                'articleBody': '文章內容...',
                'url': 'https://...',
                'publisher': {
                    '@type': 'Organization',
                    'name': '自由時報'
                },
                '_html_features': {  # 自訂欄位，用於分析
                    'link_count': 10,
                    'image_count': 3,
                    'paragraph_count': 15,
                    'word_count': 500
                }
            }
        """
        pass

    @abstractmethod
    async def get_date(self, article_id: int) -> Optional[datetime]:
        """
        取得指定文章 ID 的發布日期

        用途：
        1. 供 Navigator 使用，進行二分搜尋
        2. 快速檢查文章是否存在
        3. 不需要完整解析文章，只提取日期即可

        Args:
            article_id: 文章 ID

        Returns:
            文章發布日期（datetime 物件），或 None（如果文章不存在或獲取失敗）

        範例:
            >>> await parser.get_date(4567890)
            datetime(2025, 12, 8, 10, 30, 0)

        注意：
        1. 此方法應該輕量化，避免完整解析 HTML
        2. 只需要提取日期資訊即可
        3. 如果文章不存在（404），應返回 None
        """
        pass

    def extract_id_from_url(self, url: str) -> Optional[int]:
        """
        從 URL 提取文章 ID

        用途：
        1. 用於 backfill 時找出最老的已爬取文章
        2. 用於統計分析

        Args:
            url: 文章 URL

        Returns:
            文章 ID（整數），或 None（如果無法提取）

        預設實作嘗試從 URL 中提取數字，子類別可覆寫此方法。
        """
        import re
        # 預設實作：嘗試從 URL 提取最後一組數字
        match = re.search(r'/(\d{6,})(?:[/?]|$)', url)
        if match:
            return int(match.group(1))
        return None

    def get_sitemap_config(self) -> Optional[Dict[str, Any]]:
        """
        取得 sitemap 相關配置

        用途：讓 CrawlerEngine 知道如何處理此來源的 sitemap

        Returns:
            配置字典，或 None（如果不支援 sitemap）

        配置格式:
            {
                'index_url': 'https://example.com/sitemap.xml',  # sitemap URL
                'is_index': True,  # True = sitemap index, False = single sitemap
                'article_url_pattern': r'<loc>(https?://...)</loc>',  # 文章 URL pattern
                'sitemap_date_pattern': r'...',  # (optional) 從 sitemap filename 提取日期的 pattern
            }

        預設回傳 None，子類別可覆寫此方法來啟用 sitemap 支援。
        """
        return None

    def get_list_page_config(self) -> Optional[Dict[str, Any]]:
        """
        取得列表頁爬取配置

        用途：讓 CrawlerEngine 知道如何從列表頁獲取文章 URLs

        Returns:
            配置字典，或 None（如果不支援列表頁爬取）

        配置格式:
            {
                'list_urls': ['https://example.com/list/cat1', ...],  # 列表頁 URLs
                'article_url_pattern': r'href=\"(/news/[^\"]+)\"',  # 文章 URL pattern
                'base_url': 'https://example.com',  # 用於補全相對路徑
            }

        預設回傳 None，子類別可覆寫此方法來啟用列表頁爬取。
        """
        return None

