"""
Parser 工廠模組

提供統一的 Parser 創建介面。
使用延遲載入 (Lazy Loading) 避免循環引用問題。
"""

from typing import Optional, Dict, Type, List
import logging

from ..core.interfaces import BaseParser


class CrawlerFactory:
    """
    Parser 工廠類別

    職責：
    1. 統一管理所有 Parser 的創建
    2. 使用延遲載入避免循環引用
    3. 提供 Parser 列表查詢功能
    4. 支援通用參數傳遞
    """

    _registry: Dict[str, Type[BaseParser]] = {}
    _logger = logging.getLogger(__name__)

    @classmethod
    def get_parser(cls, source: str, **kwargs) -> Optional[BaseParser]:
        """
        根據來源代號創建對應的 Parser 實例

        Args:
            source: 新聞來源代號
            **kwargs: Parser 初始化參數

        Returns:
            Parser 實例，若來源不支援則返回 None
        """
        source = source.lower()

        if source not in cls._registry:
            cls._load_parser_class(source)

        parser_class = cls._registry.get(source)

        if parser_class is None:
            cls._logger.error(f"Unsupported source: {source}")
            return None

        try:
            if kwargs:
                cls._logger.debug(f"Creating {source} parser with kwargs: {kwargs}")
            return parser_class(**kwargs)

        except TypeError as e:
            cls._logger.warning(
                f"Parser '{source}' does not accept kwargs: {e}. "
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
        """延遲載入 Parser 類別"""
        try:
            if source == 'ltn':
                from .ltn_parser import LtnParser
                cls._registry['ltn'] = LtnParser
                cls._logger.info("Loaded LtnParser")

            elif source == 'udn':
                from .udn_parser import UdnParser
                cls._registry['udn'] = UdnParser
                cls._logger.info("Loaded UdnParser")

            elif source == 'cna':
                from .cna_parser import CnaParser
                cls._registry['cna'] = CnaParser
                cls._logger.info("Loaded CnaParser")

            elif source == 'moea':
                from .moea_parser import MoeaParser
                cls._registry['moea'] = MoeaParser
                cls._logger.info("Loaded MoeaParser")

            elif source == 'einfo':
                from .einfo_parser import EInfoParser
                cls._registry['einfo'] = EInfoParser
                cls._logger.info("Loaded EInfoParser")

            elif source == 'esg_businesstoday':
                from .esg_businesstoday_parser import EsgBusinessTodayParser
                cls._registry['esg_businesstoday'] = EsgBusinessTodayParser
                cls._logger.info("Loaded EsgBusinessTodayParser")

            elif source == 'chinatimes':
                from .chinatimes_parser import ChinatimesParser
                cls._registry['chinatimes'] = ChinatimesParser
                cls._logger.info("Loaded ChinatimesParser")

            else:
                cls._logger.warning(f"Unknown source: {source}")

        except ImportError as e:
            cls._logger.error(f"Failed to import parser for {source}: {e}")

    @classmethod
    def list_sources(cls) -> List[str]:
        """列出所有支援的新聞來源"""
        return [
            'ltn',
            'udn',
            'cna',
            'moea',
            'einfo',
            'esg_businesstoday',
            'chinatimes'
        ]

    @classmethod
    def is_supported(cls, source: str) -> bool:
        """檢查是否支援指定的新聞來源"""
        return source.lower() in cls.list_sources()

    @classmethod
    def clear_registry(cls) -> None:
        """清空 Parser 註冊表"""
        cls._registry.clear()
        cls._logger.info("Parser registry cleared")


def list_available_sources() -> List[str]:
    """向後相容介面：列出所有支援的新聞來源"""
    return CrawlerFactory.list_sources()
