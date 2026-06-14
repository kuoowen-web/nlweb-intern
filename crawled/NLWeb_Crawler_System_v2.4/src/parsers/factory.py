"""
Parser 工廠模組

提供統一的 Parser 創建介面，避免在 main.py 中使用 if-elif 判斷。
使用延遲載入 (Lazy Loading) 避免循環引用問題。

✅ FIX #WORK-ORDER-923: 新增 ESG BusinessToday 來源支援
✅ FIX #WORK-ORDER-913: 新增 E-Info 來源支援
✅ FIX #WORK-ORDER-905: 支援通用參數傳遞 (**kwargs)
✅ FIX #WORK-ORDER-803: 新增 MOEA 來源支援

使用範例:
    from src.parsers.factory import CrawlerFactory
    
    # 創建 Parser（無參數）
    parser = CrawlerFactory.get_parser('ltn')
    
    # 創建 Parser（帶參數）
    parser = CrawlerFactory.get_parser('moea', max_pages=5, count=100)
    parser = CrawlerFactory.get_parser('einfo', max_pages=10, count=200)
    parser = CrawlerFactory.get_parser('esg_businesstoday', count=50)
    
    # 列出所有支援的來源
    sources = CrawlerFactory.list_sources()
"""

from typing import Optional, Dict, Type, List
import logging
import inspect

from src.core.interfaces import BaseParser


class CrawlerFactory:
    """
    Parser 工廠類別
    
    職責：
    1. 統一管理所有 Parser 的創建
    2. 使用延遲載入避免循環引用
    3. 提供 Parser 列表查詢功能
    4. 支援通用參數傳遞（自動適配 Parser 的 __init__ 簽名）
    
    設計模式：
    - Factory Pattern (工廠模式)
    - Lazy Loading (延遲載入)
    - Duck Typing (鴨子型別)
    """
    
    _registry: Dict[str, Type[BaseParser]] = {}
    _logger = logging.getLogger(__name__)
    
    @classmethod
    def get_parser(cls, source: str, **kwargs) -> Optional[BaseParser]:
        """
        根據來源代號創建對應的 Parser 實例
        
        ✅ FIX #WORK-ORDER-905: 支援通用參數傳遞
        
        使用延遲載入策略：
        1. 首次呼叫時才 import 對應的 Parser 類別
        2. 將類別註冊到 _registry 快取
        3. 後續呼叫直接從快取取得
        
        參數傳遞策略：
        1. 優先嘗試傳遞所有 kwargs
        2. 如果 Parser 不接受某些參數（TypeError），則嘗試無參數初始化
        3. 記錄警告，提示 Parser 需要升級以支援參數
        
        Args:
            source: 新聞來源代號 (如 'ltn', 'udn', 'chinatimes', 'cna', 'moea', 'einfo', 'esg_businesstoday')
            **kwargs: Parser 初始化參數（如 max_pages, count, target_date）
            
        Returns:
            Parser 實例，若來源不支援則返回 None
            
        Example:
            >>> # 無參數（舊版 Parser）
            >>> parser = CrawlerFactory.get_parser('ltn')
            
            >>> # 帶參數（新版 Parser）
            >>> parser = CrawlerFactory.get_parser('moea', max_pages=5, count=100)
            >>> parser = CrawlerFactory.get_parser('einfo', max_pages=10)
            >>> parser = CrawlerFactory.get_parser('esg_businesstoday', count=50)
        """
        source = source.lower()
        
        # 檢查是否已註冊
        if source not in cls._registry:
            cls._load_parser_class(source)
        
        # 從註冊表取得 Parser 類別
        parser_class = cls._registry.get(source)
        
        if parser_class is None:
            cls._logger.error(f"Unsupported source: {source}")
            return None
        
        # ✅ FIX #WORK-ORDER-905: 嘗試傳遞參數創建實例
        try:
            # 策略 1：嘗試傳遞所有 kwargs
            if kwargs:
                cls._logger.debug(f"Creating {source} parser with kwargs: {kwargs}")
            return parser_class(**kwargs)
            
        except TypeError as e:
            # 策略 2：如果 Parser 不接受參數，嘗試無參數初始化
            cls._logger.warning(
                f"⚠️  Parser '{source}' does not accept kwargs: {e}. "
                f"Trying no-args initialization."
            )
            try:
                return parser_class()
            except Exception as e2:
                cls._logger.error(f"Failed to create parser for {source}: {e2}")
                return None
        
        except Exception as e:
            cls._logger.error(f"Failed to create parser for {source}: {e}")
            return None
    
    @classmethod
    def _load_parser_class(cls, source: str) -> None:
        """
        延遲載入 Parser 類別
        
        使用 lazy import 避免循環引用問題：
        - 只在需要時才 import
        - 避免模組初始化時的相互依賴
        
        ✅ FIX #WORK-ORDER-923: 新增 esg_businesstoday 分支
        ✅ FIX #WORK-ORDER-913: 新增 einfo 分支
        ✅ FIX #WORK-ORDER-803: 新增 moea 分支
        
        Args:
            source: 新聞來源代號
        """
        try:
            if source == 'ltn':
                from src.parsers.ltn_parser import LtnParser
                cls._registry['ltn'] = LtnParser
                cls._logger.info("Loaded LtnParser")
            
            elif source == 'udn':
                from src.parsers.udn_parser import UdnParser
                cls._registry['udn'] = UdnParser
                cls._logger.info("Loaded UdnParser")
            
            elif source == 'chinatimes':
                from src.parsers.chinatimes_parser import ChinaTimesParser
                cls._registry['chinatimes'] = ChinaTimesParser
                cls._logger.info("Loaded ChinaTimesParser")
            
            elif source == 'cna':
                from src.parsers.cna_parser import CnaParser
                cls._registry['cna'] = CnaParser
                cls._logger.info("Loaded CnaParser")
            
            # ✅ FIX #WORK-ORDER-803: 新增 moea 分支
            elif source == 'moea':
                from src.parsers.moea_parser import MoeaParser
                cls._registry['moea'] = MoeaParser
                cls._logger.info("Loaded MoeaParser")
            
            # ✅ FIX #WORK-ORDER-913: 新增 einfo 分支
            elif source == 'einfo':
                from src.parsers.einfo_parser import EInfoParser
                cls._registry['einfo'] = EInfoParser
                cls._logger.info("Loaded EInfoParser")
            
            # ✅ FIX #WORK-ORDER-923: 新增 esg_businesstoday 分支
            elif source == 'esg_businesstoday':
                from src.parsers.esg_businesstoday_parser import EsgBusinessTodayParser
                cls._registry['esg_businesstoday'] = EsgBusinessTodayParser
                cls._logger.info("Loaded EsgBusinessTodayParser")
            
            # 未來可以繼續新增其他來源
            
            else:
                cls._logger.warning(f"Unknown source: {source}")
        
        except ImportError as e:
            cls._logger.error(f"Failed to import parser for {source}: {e}")
    
    @classmethod
    def list_sources(cls) -> List[str]:
        """
        列出所有支援的新聞來源
        
        ✅ FIX #WORK-ORDER-923: 新增 esg_businesstoday
        ✅ FIX #WORK-ORDER-913: 新增 einfo
        ✅ FIX #WORK-ORDER-803: 新增 moea
        
        Returns:
            新聞來源代號列表
            
        Example:
            >>> sources = CrawlerFactory.list_sources()
            >>> print(sources)
            ['ltn', 'udn', 'chinatimes', 'cna', 'moea', 'einfo', 'esg_businesstoday']
        """
        # 支援的來源列表（硬編碼，確保完整性）
        supported_sources = [
            'ltn', 
            'udn', 
            'chinatimes', 
            'cna', 
            'moea', 
            'einfo', 
            'esg_businesstoday'  # ✅ FIX #WORK-ORDER-923
        ]
        return supported_sources
    
    @classmethod
    def is_supported(cls, source: str) -> bool:
        """
        檢查是否支援指定的新聞來源
        
        Args:
            source: 新聞來源代號
            
        Returns:
            True 若支援，否則 False
            
        Example:
            >>> CrawlerFactory.is_supported('ltn')
            True
            >>> CrawlerFactory.is_supported('einfo')
            True
            >>> CrawlerFactory.is_supported('esg_businesstoday')
            True
            >>> CrawlerFactory.is_supported('unknown')
            False
        """
        return source.lower() in cls.list_sources()
    
    @classmethod
    def clear_registry(cls) -> None:
        """
        清空 Parser 註冊表
        
        主要用於測試或重新載入場景
        """
        cls._registry.clear()
        cls._logger.info("Parser registry cleared")


# ==================== 向後相容介面 (HOTFIX-001) ====================

def list_available_sources() -> List[str]:
    """
    [Hotfix] 舊版介面相容函式
    
    此函式用於向後相容舊版 main.py (line 25)。
    在重構過程中，原本的全域函式被移入 CrawlerFactory 類別，
    導致舊程式碼匯入失敗。此函式作為 Proxy 轉接新版介面。
    
    Returns:
        支援的新聞來源列表
        
    Example:
        >>> from src.parsers.factory import list_available_sources
        >>> sources = list_available_sources()
        >>> print(sources)
        ['ltn', 'udn', 'chinatimes', 'cna', 'moea', 'einfo', 'esg_businesstoday']
    
    Note:
        新程式碼建議直接使用 CrawlerFactory.list_sources()
    """
    return CrawlerFactory.list_sources()


# ==================== 測試程式碼 ====================
if __name__ == "__main__":
    import asyncio
    
    async def test_factory():
        """測試 CrawlerFactory"""
        print("="*60)
        print("CrawlerFactory 測試（含 kwargs 傳遞）")
        print("="*60)
        
        # 測試 1：列出支援的來源
        print("\n【測試 1】支援的新聞來源")
        sources = CrawlerFactory.list_sources()
        print(f"  支援來源: {sources}")
        
        # 測試 2：檢查來源支援
        print("\n【測試 2】檢查來源支援")
        for source in ['ltn', 'udn', 'chinatimes', 'cna', 'moea', 'einfo', 'esg_businesstoday', 'unknown']:
            supported = CrawlerFactory.is_supported(source)
            status = "✅" if supported else "❌"
            print(f"  {status} {source}: {supported}")
        
        # 測試 3：創建 LTN Parser（無參數）
        print("\n【測試 3】創建 LTN Parser（無參數）")
        ltn_parser = CrawlerFactory.get_parser('ltn')
        if ltn_parser:
            print(f"  ✅ 成功創建: {ltn_parser.__class__.__name__}")
            print(f"  ✅ Source Name: {ltn_parser.source_name}")
        else:
            print("  ❌ 創建失敗")
        
        # 測試 4：創建 MOEA Parser（無參數）
        print("\n【測試 4】創建 MOEA Parser（無參數）")
        moea_parser = CrawlerFactory.get_parser('moea')
        if moea_parser:
            print(f"  ✅ 成功創建: {moea_parser.__class__.__name__}")
            print(f"  ✅ Source Name: {moea_parser.source_name}")
        else:
            print("  ❌ 創建失敗")
        
        # ✅ 測試 5：創建 MOEA Parser（帶參數）
        print("\n【測試 5】創建 MOEA Parser（帶參數：max_pages=5）")
        moea_parser_with_args = CrawlerFactory.get_parser('moea', max_pages=5)
        if moea_parser_with_args:
            print(f"  ✅ 成功創建: {moea_parser_with_args.__class__.__name__}")
            print(f"  ✅ max_pages: {moea_parser_with_args.max_pages}")
        else:
            print("  ❌ 創建失敗")
        
        # ✅ 測試 6：創建 E-Info Parser（帶參數）
        print("\n【測試 6】創建 E-Info Parser（帶參數：max_pages=10）")
        einfo_parser = CrawlerFactory.get_parser('einfo', max_pages=10)
        if einfo_parser:
            print(f"  ✅ 成功創建: {einfo_parser.__class__.__name__}")
            print(f"  ✅ Source Name: {einfo_parser.source_name}")
            print(f"  ✅ max_pages: {einfo_parser.max_pages}")
        else:
            print("  ❌ 創建失敗")
        
        # ✅ 測試 7：創建 ESG BusinessToday Parser（帶參數）
        print("\n【測試 7】創建 ESG BusinessToday Parser（帶參數：count=50）")
        esg_parser = CrawlerFactory.get_parser('esg_businesstoday', count=50)
        if esg_parser:
            print(f"  ✅ 成功創建: {esg_parser.__class__.__name__}")
            print(f"  ✅ Source Name: {esg_parser.source_name}")
            print(f"  ✅ count: {esg_parser.count}")
        else:
            print("  ❌ 創建失敗")
        
        # ✅ 測試 8：向舊版 Parser 傳遞參數（應該降級為無參數）
        print("\n【測試 8】向 LTN Parser 傳遞參數（應該降級）")
        ltn_parser_with_args = CrawlerFactory.get_parser('ltn', max_pages=5)
        if ltn_parser_with_args:
            print(f"  ✅ 成功創建（降級為無參數）: {ltn_parser_with_args.__class__.__name__}")
            print(f"  ℹ️  說明：舊版 Parser 不支援參數，自動降級")
        else:
            print("  ❌ 創建失敗")
        
        # 測試 9：測試 get_latest_id
        print("\n【測試 9】測試 get_latest_id")
        for source in ['ltn', 'moea', 'einfo', 'esg_businesstoday']:
            parser = CrawlerFactory.get_parser(source)
            if parser:
                try:
                    latest_id = await parser.get_latest_id()
                    if latest_id:
                        print(f"  ✅ {source.upper()} 最新 ID: {latest_id}")
                    else:
                        print(f"  ⚠️  {source.upper()} 無法取得最新 ID")
                except Exception as e:
                    print(f"  ⚠️  {source.upper()} 測試失敗: {e}")
        
        print("\n" + "="*60)
        print("✅ 測試完成（kwargs 傳遞機制正常）")
        print("="*60)
    
    # 執行測試
    asyncio.run(test_factory())
