"""
Full Scan Script - Scan complete ID ranges to crawl all articles

Usage:
    python backfill.py --all                    # Run all sources sequentially
    python backfill.py --source ltn             # Run specific source
    python backfill.py --source udn --start-id 7800000 --end-id 9313000  # Custom ID range
    python backfill.py --source cna --start-date 2024-01 --end-date 2025-06  # Custom date range
    python backfill.py --source ltn --test      # Test mode (small range)
    python backfill.py --status                 # Show progress

Supported sources:
    Sequential ID (requires --start-id, --end-id):
        - udn: United Daily News (IDs ~7.8M to ~9.3M)
        - ltn: Liberty Times Net (IDs ~3M+)
        - einfo: Environmental Info Center (IDs ~230K+)

    Date-based ID (uses --start-date, --end-date):
        - cna: Central News Agency (YYYYMMDDXXXX)
        - esg_businesstoday: ESG BusinessToday (YYYYMMDDXXXX)
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "code" / "python"))

from crawler.core.engine import CrawlerEngine, FULL_SCAN_CONFIG
from crawler.parsers.factory import CrawlerFactory

# ==================== Configuration ====================

# Progress file
PROGRESS_FILE = project_root / "data" / "crawler" / "fullscan_progress.json"

# ==================== Logging ====================

def setup_logging(source: Optional[str] = None) -> logging.Logger:
    """Setup logging with file and console handlers"""
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"fullscan_{source or 'all'}_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )

    return logging.getLogger(__name__)


# ==================== Progress Management ====================

def load_progress() -> Dict[str, Any]:
    """Load full scan progress from file"""
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load progress file: {e}")
    return {"sources": {}, "last_updated": None}


def save_progress(progress: Dict[str, Any]) -> None:
    """Save full scan progress to file"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    progress["last_updated"] = datetime.now().isoformat()

    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def update_source_progress(source: str, stats: Dict[str, Any]) -> None:
    """Update progress for a source after scan completion"""
    progress = load_progress()

    progress["sources"][source] = {
        "last_scanned_id": stats.get("last_scanned_id"),
        "last_scanned_date": stats.get("last_scanned_date"),
        "success": stats.get("success", 0),
        "failed": stats.get("failed", 0),
        "skipped": stats.get("skipped", 0),
        "total": stats.get("total", 0),
        "progress": stats.get("progress", 0),
        "completed_at": datetime.now().isoformat(),
    }

    save_progress(progress)


# ==================== Full Scan Execution ====================

async def run_full_scan_for_source(
    source: str,
    start_id: Optional[int] = None,
    end_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    test_mode: bool = False,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """Run full scan for a single source"""
    logger = logger or logging.getLogger(__name__)

    if source not in FULL_SCAN_CONFIG:
        logger.error(f"Unknown source: {source}")
        return {"error": f"Unknown source: {source}"}

    config = FULL_SCAN_CONFIG[source]

    parser = CrawlerFactory.get_parser(source)
    if parser is None:
        logger.error(f"No parser found for source: {source}")
        return {"error": f"No parser for {source}"}

    # For sequential sources, auto-detect end_id if not provided
    if config["type"] == "sequential":
        if start_id is None:
            start_id = config.get("default_start_id")
            logger.info(f"[{source}] Using default start_id: {start_id:,}")

        if end_id is None:
            logger.info(f"[{source}] Auto-detecting latest ID...")
            try:
                end_id = await parser.get_latest_id()
                logger.info(f"[{source}] Latest ID: {end_id:,}")
            except Exception as e:
                logger.error(f"[{source}] Failed to auto-detect end_id: {e}")
                return {"error": f"Cannot auto-detect end_id for {source}: {e}"}

        # Test mode: only scan a small range
        if test_mode:
            end_id = min(end_id, start_id + 100)
            logger.info(f"[{source}] Test mode: scanning {start_id:,} to {end_id:,} (100 IDs)")

        logger.info(f"[{source}] Starting full scan: {start_id:,} -> {end_id:,}")

    elif config["type"] == "date_based":
        if test_mode and not end_date:
            # Test mode: only scan today
            end_date = start_date or datetime.now().strftime("%Y-%m-%d")
            start_date = start_date or end_date
            logger.info(f"[{source}] Test mode: scanning {start_date} only")

        logger.info(f"[{source}] Starting full scan: {start_date or '2024-01-01'} -> {end_date or 'today'}")

    try:
        engine = CrawlerEngine(parser=parser, auto_save=True)

        result = await engine.run_full_scan(
            start_id=start_id,
            end_id=end_id,
            start_date=start_date,
            end_date=end_date,
        )

        await engine.close()

        # Save progress
        update_source_progress(source, result)

        logger.info(f"[{source}] Full scan complete: success={result.get('success', 0)}, "
                   f"failed={result.get('failed', 0)}, skipped={result.get('skipped', 0)}")

        return {"status": "complete", "source": source, "stats": result}

    except Exception as e:
        logger.error(f"[{source}] Error during full scan: {e}", exc_info=True)
        return {"status": "error", "source": source, "error": str(e)}


async def run_all_sources(
    test_mode: bool = False,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """Run full scan for all sources sequentially"""
    logger = logger or logging.getLogger(__name__)

    results = {}

    for source, config in FULL_SCAN_CONFIG.items():
        logger.info(f"\n{'='*60}")
        logger.info(f"Starting full scan for: {source} ({config['type']})")
        logger.info(f"{'='*60}\n")

        result = await run_full_scan_for_source(
            source=source,
            test_mode=test_mode,
            logger=logger
        )

        results[source] = result

    return results


def show_status() -> None:
    """Display current full scan progress"""
    progress = load_progress()

    print("\n" + "="*60)
    print("Full Scan Progress Status")
    print("="*60)

    if not progress.get("sources"):
        print("\nNo full scan progress recorded yet.")
        print(f"\nTo start: python backfill.py --all")
        return

    print(f"\nLast updated: {progress.get('last_updated', 'N/A')}")

    for source, data in progress.get("sources", {}).items():
        config = FULL_SCAN_CONFIG.get(source, {})
        scan_type = config.get("type", "unknown")

        print(f"\n--- {source} ({scan_type}) ---")

        if data.get("last_scanned_id") is not None:
            print(f"  Last scanned ID: {data['last_scanned_id']:,}")
        if data.get("last_scanned_date"):
            print(f"  Last scanned date: {data['last_scanned_date']}")

        progress_val = data.get("progress", 0)
        total = data.get("total", 0)
        pct = (progress_val / total * 100) if total > 0 else 0
        print(f"  Progress: {progress_val:,} / {total:,} ({pct:.1f}%)")
        print(f"  Stats: success={data.get('success', 0):,}, "
              f"failed={data.get('failed', 0):,}, "
              f"skipped={data.get('skipped', 0):,}")

        if data.get("completed_at"):
            print(f"  Completed at: {data['completed_at']}")

    print()


# ==================== Main ====================

def main():
    parser = argparse.ArgumentParser(
        description="Full scan: crawl complete ID ranges from news sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument("--all", action="store_true", help="Run all sources sequentially")
    parser.add_argument("--source", type=str, help="Run specific source")
    parser.add_argument("--start-id", type=int, help="Start ID (sequential sources)")
    parser.add_argument("--end-id", type=int, help="End ID (sequential sources)")
    parser.add_argument("--start-date", type=str, help="Start date YYYY-MM or YYYY-MM-DD (date-based sources)")
    parser.add_argument("--end-date", type=str, help="End date YYYY-MM or YYYY-MM-DD (date-based sources)")
    parser.add_argument("--test", action="store_true", help="Test mode (small range)")
    parser.add_argument("--status", action="store_true", help="Show full scan progress")
    parser.add_argument("--reset", type=str, metavar="SOURCE",
                       help="Reset progress for a source (or 'all')")

    args = parser.parse_args()

    # Show status
    if args.status:
        show_status()
        return

    # Reset progress
    if args.reset:
        progress = load_progress()
        if args.reset == "all":
            progress["sources"] = {}
            print("Reset all progress")
        elif args.reset in progress.get("sources", {}):
            del progress["sources"][args.reset]
            print(f"Reset progress for {args.reset}")
        else:
            print(f"No progress found for {args.reset}")
            return
        save_progress(progress)
        return

    # Validate arguments
    if not args.all and not args.source:
        parser.print_help()
        print("\nError: Must specify --all or --source")
        sys.exit(1)

    if args.source and args.source not in FULL_SCAN_CONFIG:
        print(f"Error: Unknown source '{args.source}'")
        print(f"Available sources: {', '.join(FULL_SCAN_CONFIG.keys())}")
        sys.exit(1)

    # Setup logging
    logger = setup_logging(args.source if args.source else "all")

    # Run full scan
    logger.info("="*60)
    logger.info("Full Scan Started")
    if args.start_id is not None:
        end_str = f"{args.end_id:,}" if args.end_id is not None else "auto"
        logger.info(f"ID range: {args.start_id:,} -> {end_str}")
    if args.start_date:
        logger.info(f"Date range: {args.start_date} -> {args.end_date or 'today'}")
    logger.info(f"Test mode: {args.test}")
    logger.info("="*60)

    try:
        if args.all:
            result = asyncio.run(run_all_sources(
                test_mode=args.test,
                logger=logger
            ))
        else:
            result = asyncio.run(run_full_scan_for_source(
                source=args.source,
                start_id=args.start_id,
                end_id=args.end_id,
                start_date=args.start_date,
                end_date=args.end_date,
                test_mode=args.test,
                logger=logger
            ))

        logger.info("\n" + "="*60)
        logger.info("Full Scan Complete")
        logger.info(f"Result: {json.dumps(result, indent=2, ensure_ascii=False, default=str)}")
        logger.info("="*60)

    except KeyboardInterrupt:
        logger.info("\nFull scan interrupted by user")
        logger.info("Progress has been saved. Run again to continue.")
    except Exception as e:
        logger.error(f"Full scan failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
