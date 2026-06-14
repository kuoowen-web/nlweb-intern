"""
main.py - NLWeb v2 主程式入口

通用新聞爬蟲系統，支援多種新聞來源。

執行模式：
1. 範圍模式：指定 ID 範圍爬取
2. 自動模式：自動獲取最新 ID 並向前爬取
3. 日期模式：根據日期範圍自動定位 ID（需配合 Navigator）

✅ FIX #WORK-ORDER-905: 完全通用化，移除所有來源特定邏輯

設計原則：
- 完全通用，不包含任何網站特定邏輯
- 透過工廠模式動態載入 Parser
- 所有配置透過命令列參數或配置檔
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime
from typing import Optional

from config import settings
from src.parsers.factory import CrawlerFactory, list_available_sources
from src.core.engine import CrawlerEngine
from src.core.navigator import DateNavigator


def setup_logging(verbose: bool = False) -> None:
    """
    設置日誌系統
    
    Args:
        verbose: 是否顯示詳細日誌
    """
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format=settings.LOG_FORMAT,
        datefmt=settings.LOG_DATE_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def create_parser() -> argparse.ArgumentParser:
    """
    創建命令列參數解析器
    
    Returns:
        ArgumentParser 實例
    """
    parser = argparse.ArgumentParser(
        description='NLWeb v2 - Universal News Crawler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 爬取指定範圍（從新到舊）
  python main.py --source ltn --id-start 4567890 --id-end 4567800
  
  # 自動獲取最新 ID 並向前爬取 100 篇
  python main.py --source ltn --auto-latest --count 100
  
  # 根據日期範圍爬取
  python main.py --source ltn --date-start 2025-12-01 --date-end 2025-12-07
  
  # 乾跑模式（完整模擬爬取流程但不儲存）
  python main.py --source ltn --id-start 4567890 --id-end 4567800 --dry-run
  
  # 列出所有支援的新聞來源
  python main.py --list-sources
        """
    )
    
    # 基本參數
    parser.add_argument(
        '--source',
        type=str,
        choices=list_available_sources(),
        help='News source to crawl (e.g., ltn, udn, moea)'
    )
    
    parser.add_argument(
        '--list-sources',
        action='store_true',
        help='List all available news sources and exit'
    )
    
    # ID 範圍模式
    id_group = parser.add_argument_group('ID Range Mode')
    id_group.add_argument(
        '--id-start',
        type=int,
        help='Starting article ID'
    )
    id_group.add_argument(
        '--id-end',
        type=int,
        help='Ending article ID'
    )
    id_group.add_argument(
        '--reverse',
        action='store_true',
        default=True,
        help='Crawl from start to end in reverse order (default: True)'
    )
    
    # 自動模式
    auto_group = parser.add_argument_group('Auto Mode')
    auto_group.add_argument(
        '--auto-latest',
        action='store_true',
        help='Automatically fetch the latest article ID'
    )
    auto_group.add_argument(
        '--count',
        type=int,
        default=100,
        help='Number of articles to crawl in auto mode (default: 100)'
    )
    
    # 日期模式
    date_group = parser.add_argument_group('Date Range Mode')
    date_group.add_argument(
        '--date-start',
        type=str,
        help='Start date (YYYY-MM-DD)'
    )
    date_group.add_argument(
        '--date-end',
        type=str,
        help='End date (YYYY-MM-DD)'
    )
    date_group.add_argument(
        '--search-min-id',
        type=int,
        help='Minimum ID for date search (optional)'
    )
    date_group.add_argument(
        '--search-max-id',
        type=int,
        help='Maximum ID for date search (optional)'
    )
    
    # Parser 參數（通用）
    parser_group = parser.add_argument_group('Parser Options')
    parser_group.add_argument(
        '--max-pages',
        type=int,
        help='Maximum pages to crawl (for list-based parsers like MOEA)'
    )
    
    # 其他選項
    parser.add_argument(
        '--no-auto-save',
        action='store_true',
        help='Disable automatic saving (for testing)'
    )
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Dry run mode: simulate full crawling process without saving data'
    )
    
    return parser


async def run_id_range_mode(
    engine: CrawlerEngine,
    start_id: int,
    end_id: int,
    reverse: bool = True
) -> dict:
    """
    執行 ID 範圍模式
    
    Args:
        engine: CrawlerEngine 實例
        start_id: 起始 ID
        end_id: 結束 ID
        reverse: 是否反向爬取
        
    Returns:
        爬取統計結果
    """
    logger = logging.getLogger('main')
    logger.info(f"🚀 Starting ID Range Mode")
    logger.info(f"   Range: {start_id} → {end_id}")
    logger.info(f"   Direction: {'Reverse (new to old)' if reverse else 'Forward (old to new)'}")
    
    stats = await engine.run_range(start_id, end_id, reverse=reverse)
    return stats


async def run_auto_mode(
    engine: CrawlerEngine,
    count: int = 100
) -> dict:
    """
    執行自動模式（獲取最新 ID 並向前爬取）
    
    ✅ FIX #WORK-ORDER-905: 使用 Engine 的 run_auto() 方法
    
    Args:
        engine: CrawlerEngine 實例
        count: 要爬取的文章數量
        
    Returns:
        爬取統計結果
    """
    logger = logging.getLogger('main')
    logger.info(f"🚀 Starting Auto Mode")
    logger.info(f"   Target: {count} articles")
    
    # ✅ 使用 Engine 的 run_auto() 方法（自動適配列表式/流水號式）
    stats = await engine.run_auto(count=count)
    return stats


async def run_date_range_mode(
    engine: CrawlerEngine,
    parser,
    start_date: datetime,
    end_date: datetime,
    search_min_id: Optional[int] = None,
    search_max_id: Optional[int] = None
) -> dict:
    """
    執行日期範圍模式（使用 Navigator 定位 ID）
    
    Args:
        engine: CrawlerEngine 實例
        parser: Parser 實例
        start_date: 開始日期
        end_date: 結束日期
        search_min_id: 搜尋範圍最小 ID（可選）
        search_max_id: 搜尋範圍最大 ID（可選）
        
    Returns:
        爬取統計結果
    """
    logger = logging.getLogger('main')
    logger.info(f"🚀 Starting Date Range Mode")
    logger.info(f"   Date Range: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
    
    # 創建 Navigator
    navigator = DateNavigator(parser.get_date, parser.source_name)
    
    # 如果未提供搜尋範圍，嘗試自動獲取
    if search_max_id is None:
        logger.info("   Fetching latest article ID for search range...")
        
        search_max_id = await parser.get_latest_id()
        
        if search_max_id is None:
            logger.error("❌ Failed to determine search range")
            return {'error': 'Failed to determine search range'}
        
        logger.info(f"   Latest ID: {search_max_id}")
    
    if search_min_id is None:
        # 預設搜尋範圍：最新 ID 往前推 100 萬
        search_min_id = max(1, search_max_id - 1000000)
    
    logger.info(f"   Search Range: [{search_min_id}, {search_max_id}]")
    logger.info(f"   Locating article IDs for date range...")
    
    # 使用 Navigator 定位 ID 範圍
    id_range = await navigator.find_date_range(
        start_date=start_date,
        end_date=end_date,
        min_id=search_min_id,
        max_id=search_max_id
    )
    
    if id_range is None:
        logger.error("❌ Failed to locate article IDs for date range")
        return {'error': 'Failed to locate IDs'}
    
    start_id, end_id = id_range
    logger.info(f"   Located ID Range: [{start_id}, {end_id}]")
    
    # 執行爬取
    stats = await engine.run_range(start_id, end_id, reverse=False)
    return stats


async def main_async(args: argparse.Namespace) -> int:
    """
    非同步主函數
    
    ✅ FIX #WORK-ORDER-905: 完全通用化，移除所有來源特定邏輯
    
    Args:
        args: 命令列參數
        
    Returns:
        退出碼（0 表示成功）
    """
    logger = logging.getLogger('main')
    
    # 列出來源模式
    if args.list_sources:
        sources = list_available_sources()
        print("Available news sources:")
        for source in sources:
            print(f"  - {source}")
        return 0
    
    # 檢查必要參數
    if not args.source:
        logger.error("❌ --source is required (use --list-sources to see available sources)")
        return 1
    
    # Dry run 模式提示
    if args.dry_run:
        logger.info("🔍 Dry run mode enabled")
        logger.info("   Will simulate full crawling process")
        logger.info("   No data will be saved to disk")
    
    try:
        # ✅ FIX #WORK-ORDER-905: 準備通用參數
        parser_kwargs = {}
        
        # 傳遞 count（用於計算 max_pages）
        if args.count:
            parser_kwargs['count'] = args.count
        
        # 傳遞 max_pages（優先級高於 count）
        if args.max_pages:
            parser_kwargs['max_pages'] = args.max_pages
        
        # 傳遞 target_date（未來支援日期過濾）
        if args.date_start:
            try:
                target_date = datetime.strptime(args.date_start, '%Y-%m-%d')
                parser_kwargs['target_date'] = target_date
            except ValueError:
                pass
        
        # ✅ FIX #WORK-ORDER-905: 通用呼叫（不管你是 moea, ltn 還是 future_source）
        logger.info(f"📦 Loading parser for source: {args.source}")
        if parser_kwargs:
            logger.info(f"   Parser kwargs: {parser_kwargs}")
        
        parser = CrawlerFactory.get_parser(args.source, **parser_kwargs)
        
        if parser is None:
            logger.error(f"❌ Failed to load parser for source: {args.source}")
            return 1
        
        logger.info(f"✅ Parser loaded: {parser.__class__.__name__}")
        
    except Exception as e:
        logger.error(f"❌ Failed to load parser: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    
    try:
        # 創建引擎時考慮 dry_run 模式
        auto_save = (not args.no_auto_save) and (not args.dry_run)
        engine = CrawlerEngine(parser, auto_save=auto_save)
        logger.info(f"🔧 Engine initialized (auto_save={auto_save})")
        
        # 判斷執行模式
        stats = None
        
        # 模式 1：日期範圍模式
        if args.date_start and args.date_end:
            try:
                start_date = datetime.strptime(args.date_start, '%Y-%m-%d')
                end_date = datetime.strptime(args.date_end, '%Y-%m-%d')
                
                stats = await run_date_range_mode(
                    engine=engine,
                    parser=parser,
                    start_date=start_date,
                    end_date=end_date,
                    search_min_id=args.search_min_id,
                    search_max_id=args.search_max_id
                )
            except ValueError as e:
                logger.error(f"❌ Invalid date format: {e}")
                return 1
        
        # 模式 2：自動模式
        elif args.auto_latest:
            stats = await run_auto_mode(
                engine=engine,
                count=args.count
            )
        
        # 模式 3：ID 範圍模式
        elif args.id_start and args.id_end:
            stats = await run_id_range_mode(
                engine=engine,
                start_id=args.id_start,
                end_id=args.id_end,
                reverse=args.reverse
            )
        
        else:
            logger.error("❌ Must specify one of: --id-start/--id-end, --auto-latest, or --date-start/--date-end")
            return 1
        
        # 檢查結果
        if stats and 'error' in stats:
            logger.error(f"❌ Crawl failed: {stats['error']}")
            return 1
        
        # 輸出最終統計
        if stats:
            logger.info("")
            logger.info("=" * 60)
            
            # Dry run 模式的特殊提示
            if args.dry_run:
                logger.info("🔍 Dry Run Completed Successfully!")
                logger.info("   (No data was saved to disk)")
            else:
                logger.info("🎉 Crawl Completed Successfully!")
            
            logger.info(f"   Total:     {stats['total']}")
            logger.info(f"   Success:   {stats['success']} ✅")
            logger.info(f"   Failed:    {stats['failed']} ❌")
            logger.info(f"   Skipped:   {stats['skipped']} ⏭️")
            logger.info(f"   Not Found: {stats['not_found']} 🔍")
            
            if stats['total'] > 0:
                success_rate = (stats['success'] / stats['total']) * 100
                logger.info(f"   Success Rate: {success_rate:.2f}%")
            
            logger.info("=" * 60)
        
        return 0
        
    except KeyboardInterrupt:
        logger.warning("\n⚠️  Interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def main() -> int:
    """
    主函數入口
    
    Returns:
        退出碼
    """
    # 解析命令列參數
    parser = create_parser()
    args = parser.parse_args()
    
    # 設置日誌
    setup_logging(verbose=args.verbose)
    
    # 執行非同步主函數
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
        return 130


if __name__ == '__main__':
    sys.exit(main())
