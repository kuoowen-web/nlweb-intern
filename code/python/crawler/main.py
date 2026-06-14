"""
main.py - NLWeb Crawler 主程式入口

通用新聞爬蟲系統，支援多種新聞來源。

執行模式：
1. 範圍模式：指定 ID 範圍爬取
2. 自動模式：自動獲取最新 ID 並向前爬取
3. 列出來源：列出所有支援的新聞來源

使用範例：
    # 列出所有支援的來源
    python -m crawler.main --list-sources

    # 自動爬取 LTN 最新 100 篇
    python -m crawler.main --source ltn --auto-latest --count 100

    # 爬取指定 ID 範圍
    python -m crawler.main --source ltn --id-start 4567890 --id-end 4567800

    # 乾跑模式（不儲存）
    python -m crawler.main --source ltn --auto-latest --count 10 --dry-run
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime
from typing import Optional

from .core import settings
from .parsers.factory import CrawlerFactory, list_available_sources
from .core.engine import CrawlerEngine


def setup_logging(verbose: bool = False) -> None:
    """設置日誌系統"""
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
    """創建命令列參數解析器"""
    parser = argparse.ArgumentParser(
        description='NLWeb Crawler - Universal News Crawler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 列出所有支援的新聞來源
  python -m crawler.main --list-sources

  # 自動爬取最新 100 篇
  python -m crawler.main --source ltn --auto-latest --count 100

  # 爬取指定 ID 範圍
  python -m crawler.main --source ltn --id-start 4567890 --id-end 4567800

  # 乾跑模式（不儲存）
  python -m crawler.main --source ltn --auto-latest --count 10 --dry-run
        """
    )

    # 基本參數
    parser.add_argument(
        '--source',
        type=str,
        choices=list_available_sources(),
        help='News source to crawl (e.g., ltn, udn, cna, moea)'
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
    auto_group.add_argument(
        '--date-floor',
        type=str,
        default=None,
        help='Date floor for auto mode (YYYY-MM format). Stop crawling when articles are older than this date.'
    )

    # Parser 參數
    parser_group = parser.add_argument_group('Parser Options')
    parser_group.add_argument(
        '--max-pages',
        type=int,
        help='Maximum pages to crawl (for list-based parsers)'
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
        help='Dry run mode: simulate crawling without saving data'
    )

    return parser


async def run_auto_mode(engine: CrawlerEngine, count: int = 100, date_floor: Optional[str] = None) -> dict:
    """執行自動模式"""
    logger = logging.getLogger('main')
    logger.info(f"Starting Auto Mode: {count} articles")
    if date_floor:
        logger.info(f"Date floor: {date_floor}")

    stats = await engine.run_auto(count=count, date_floor=date_floor)
    return stats


async def run_id_range_mode(
    engine: CrawlerEngine,
    start_id: int,
    end_id: int,
    reverse: bool = True
) -> dict:
    """執行 ID 範圍模式"""
    logger = logging.getLogger('main')
    logger.info(f"Starting ID Range Mode: {start_id} -> {end_id}")

    stats = await engine.run_range(start_id, end_id, reverse=reverse)
    return stats


async def main_async(args: argparse.Namespace) -> int:
    """非同步主函數"""
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
        logger.error("--source is required (use --list-sources to see available sources)")
        return 1

    # Dry run 模式提示
    if args.dry_run:
        logger.info("Dry run mode enabled - no data will be saved")

    try:
        # 準備 Parser 參數
        parser_kwargs = {}

        if args.count:
            parser_kwargs['count'] = args.count

        if args.max_pages:
            parser_kwargs['max_pages'] = args.max_pages

        # 創建 Parser
        logger.info(f"Loading parser for source: {args.source}")
        parser = CrawlerFactory.get_parser(args.source, **parser_kwargs)

        if parser is None:
            logger.error(f"Failed to load parser for source: {args.source}")
            return 1

        logger.info(f"Parser loaded: {parser.__class__.__name__}")

    except Exception as e:
        logger.error(f"Failed to load parser: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    try:
        # 創建引擎
        auto_save = (not args.no_auto_save) and (not args.dry_run)
        engine = CrawlerEngine(parser, auto_save=auto_save)
        logger.info(f"Engine initialized (auto_save={auto_save})")

        stats = None

        # 判斷執行模式
        if args.auto_latest:
            stats = await run_auto_mode(engine=engine, count=args.count, date_floor=args.date_floor)

        elif args.id_start and args.id_end:
            stats = await run_id_range_mode(
                engine=engine,
                start_id=args.id_start,
                end_id=args.id_end,
                reverse=args.reverse
            )

        else:
            logger.error("Must specify --auto-latest or --id-start/--id-end")
            return 1

        # 檢查結果
        if stats and 'error' in stats:
            logger.error(f"Crawl failed: {stats['error']}")
            return 1

        # 輸出最終統計
        if stats:
            logger.info("")
            logger.info("=" * 60)

            if args.dry_run:
                logger.info("Dry Run Completed!")
            else:
                logger.info("Crawl Completed!")

            logger.info(f"   Total:     {stats['total']}")
            logger.info(f"   Success:   {stats['success']}")
            logger.info(f"   Failed:    {stats['failed']}")
            logger.info(f"   Skipped:   {stats['skipped']}")
            logger.info(f"   Not Found: {stats['not_found']}")

            if stats['total'] > 0:
                success_rate = (stats['success'] / stats['total']) * 100
                logger.info(f"   Success Rate: {success_rate:.2f}%")

            logger.info("=" * 60)

        return 0

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def main() -> int:
    """主函數入口"""
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130


if __name__ == '__main__':
    sys.exit(main())
