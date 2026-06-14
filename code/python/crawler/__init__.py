"""
NLWeb Crawler Module

新聞爬蟲系統，負責抓取新聞來源並輸出 TSV 格式供 M0 Indexing Module 使用。

架構來自 NLWeb_Crawler_System_v2.4，適配至 NLWeb 專案。

支援來源：
- ltn: 自由時報
- udn: 聯合報
- cna: 中央社
- moea: 經濟部
- einfo: 環境資訊中心
- esg_businesstoday: 今周刊 ESG

使用範例：
    from crawler import CrawlerFactory

    parser = CrawlerFactory.get_parser('ltn')
    # 或使用 CLI
    # python -m crawler.main --source ltn --auto-latest --count 100
"""

from .parsers.factory import CrawlerFactory, list_available_sources
from .core.interfaces import BaseParser, SessionType

__all__ = [
    'CrawlerFactory',
    'list_available_sources',
    'BaseParser',
    'SessionType',
]

__version__ = '1.0.0'
