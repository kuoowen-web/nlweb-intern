from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum

# ✅ FIX #ENGINE-HYBRID-SESSION: Session 類型列舉
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
    
    ✅ FIX #ENGINE-HYBRID-SESSION: 支援 Parser 定義偏好的 Session 類型
    
    設計原則：
    1. 只定義介面，不包含實作邏輯
    2. 讓爬蟲引擎只依賴此介面，不依賴具體網站
    3. 所有具體的 Parser 必須繼承此類別並實作所有抽象方法
    """
    
    # ✅ FIX #ENGINE-HYBRID-SESSION: 子類別可以覆寫此屬性
    # 用途：指定此 Parser 偏好的 Session 類型
    # 範例：preferred_session_type = SessionType.CURL_CFFI
    preferred_session_type: Optional[SessionType] = None
    
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
    
    @abstractmethod
    async def get_latest_id(self, session) -> Optional[int]:
        """
        取得該網站目前最新的文章 ID
        
        用途：
        1. 確定爬取範圍的上界
        2. 用於增量更新（只爬取新文章）
        
        Args:
            session: aiohttp.ClientSession 實例
            
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


class ParserCapabilities:
    """
    Parser 能力標記（可選）
    用於標記 Parser 支援的額外功能
    """
    
    SUPPORTS_PAGINATION = "pagination"  # 支援分頁列表
    SUPPORTS_SEARCH = "search"  # 支援搜尋功能
    SUPPORTS_CATEGORY = "category"  # 支援分類瀏覽
    SUPPORTS_AUTHOR = "author"  # 支援作者資訊
    SUPPORTS_TAGS = "tags"  # 支援標籤
    SUPPORTS_COMMENTS = "comments"  # 支援評論
    SUPPORTS_RELATED = "related"  # 支援相關文章


class ExtendedParser(BaseParser):
    """
    擴展 Parser 介面（可選）
    提供額外的功能方法，不強制實作
    """
    
    def get_capabilities(self) -> list[str]:
        """
        回傳此 Parser 支援的功能列表
        
        Returns:
            功能標記列表
        """
        return []
    
    async def get_article_list(
        self,
        session,
        page: int = 1,
        category: Optional[str] = None
    ) -> Optional[list[int]]:
        """
        取得文章列表（ID 列表）
        
        Args:
            session: aiohttp.ClientSession 實例
            page: 頁碼
            category: 分類（可選）
            
        Returns:
            文章 ID 列表，或 None（如果獲取失敗）
        """
        raise NotImplementedError("This parser does not support article listing")
    
    async def search_articles(
        self,
        session,
        keyword: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Optional[list[int]]:
        """
        搜尋文章
        
        Args:
            session: aiohttp.ClientSession 實例
            keyword: 搜尋關鍵字
            start_date: 開始日期（可選）
            end_date: 結束日期（可選）
            
        Returns:
            符合條件的文章 ID 列表，或 None（如果搜尋失敗）
        """
        raise NotImplementedError("This parser does not support search")


class ParserRegistry:
    """
    Parser 註冊表
    用於管理和查找已註冊的 Parser
    """
    
    _parsers: Dict[str, type[BaseParser]] = {}
    
    @classmethod
    def register(cls, parser_class: type[BaseParser]) -> None:
        """
        註冊 Parser
        
        Args:
            parser_class: Parser 類別（必須繼承 BaseParser）
        """
        if not issubclass(parser_class, BaseParser):
            raise TypeError(f"{parser_class.__name__} must inherit from BaseParser")
        
        # 創建臨時實例以獲取 source_name
        # 注意：這要求 Parser 的 __init__ 不需要必要參數
        try:
            temp_instance = parser_class()
            source_name = temp_instance.source_name
            cls._parsers[source_name] = parser_class
        except Exception as e:
            raise ValueError(f"Cannot register parser {parser_class.__name__}: {e}")
    
    @classmethod
    def get(cls, source_name: str) -> Optional[type[BaseParser]]:
        """
        取得已註冊的 Parser 類別
        
        Args:
            source_name: 來源代號
            
        Returns:
            Parser 類別，或 None（如果未註冊）
        """
        return cls._parsers.get(source_name)
    
    @classmethod
    def list_sources(cls) -> list[str]:
        """
        列出所有已註冊的來源
        
        Returns:
            來源代號列表
        """
        return list(cls._parsers.keys())
    
    @classmethod
    def create(cls, source_name: str, *args, **kwargs) -> Optional[BaseParser]:
        """
        創建 Parser 實例
        
        Args:
            source_name: 來源代號
            *args, **kwargs: 傳遞給 Parser 建構子的參數
            
        Returns:
            Parser 實例，或 None（如果來源未註冊）
        """
        parser_class = cls.get(source_name)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)


# 裝飾器：自動註冊 Parser
def register_parser(parser_class: type[BaseParser]) -> type[BaseParser]:
    """
    裝飾器：自動將 Parser 註冊到 ParserRegistry
    
    使用方式:
        @register_parser
        class LTNParser(BaseParser):
            ...
    """
    ParserRegistry.register(parser_class)
    return parser_class
