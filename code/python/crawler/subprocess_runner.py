"""
subprocess_runner.py - Subprocess entry point for crawler tasks

Launched by dashboard_api.py as a separate Python process for GIL isolation.
Each crawler runs in its own process with its own event loop.

IPC Protocol (stdout, JSON lines):
  {"type": "progress", "stats": {...}}
  {"type": "completed", "stats": {...}}
  {"type": "error", "error": "message"}

All logging goes to stderr (engine logger writes to file + stderr).
Only JSON protocol goes to stdout.

Usage:
  python -m crawler.subprocess_runner \
    --params '{"source":"ltn","mode":"full_scan","start_id":7800000,"end_id":9000000}' \
    --task-id "fullscan_ltn_5_1234567890" \
    --signal-dir "data/crawler/signals"
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path


# Force stdout to UTF-8 (Windows defaults to cp950, corrupting JSON with Chinese)
sys.stdout.reconfigure(encoding='utf-8')

# Redirect ALL logging to stderr (stdout reserved for JSON protocol)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("subprocess_runner")


def _send(msg: dict):
    """Write a JSON line to stdout (parent process reads this)."""
    print(json.dumps(msg, ensure_ascii=False), flush=True)


async def main(params: dict, task_id: str, signal_dir: str):
    from crawler.parsers.factory import CrawlerFactory
    from crawler.core.engine import CrawlerEngine

    source = params["source"]
    mode = params.get("mode")

    logger.info(f"Subprocess starting: source={source}, mode={mode}, task_id={task_id}")

    # CRITICAL: Patch CrawlerEngine._setup_logger so its StreamHandler goes to
    # stderr, NOT stdout. On Windows, engine logs contain Chinese characters in
    # cp950 encoding which corrupt the JSON-lines protocol on stdout.
    _original_setup_logger = CrawlerEngine._setup_logger

    def _patched_setup_logger(self_engine):
        _original_setup_logger(self_engine)
        # Replace any stdout StreamHandlers with stderr ones
        for handler in self_engine.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is not sys.stderr:
                handler.stream = sys.stderr

    CrawlerEngine._setup_logger = _patched_setup_logger

    parser = CrawlerFactory.get_parser(source)
    if parser is None:
        _send({"type": "error", "error": f"Unknown source: {source}"})
        sys.exit(1)

    # Signal file for graceful stop
    signal_file = Path(signal_dir) / f".stop_{task_id}"

    def on_progress(stats: dict):
        _send({"type": "progress", "stats": stats})

    def check_stop() -> bool:
        return signal_file.exists()

    engine = CrawlerEngine(
        parser=parser,
        auto_save=True,
        progress_callback=on_progress,
        chunk_size=params.get("chunk_size", 0),
        chunk_by_month=params.get("chunk_by_month", False),
        task_id=task_id,
        stop_check=check_stop,
    )

    try:
        if mode == "full_scan":
            result = await engine.run_full_scan(
                start_id=params.get("start_id"),
                end_id=params.get("end_id"),
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
            )
        elif mode == "auto":
            result = await engine.run_auto(
                count=params.get("count", 100),
                stop_after_consecutive_skips=params.get("stop_after_skips", 10),
                date_floor=params.get("date_floor"),
            )
        elif mode == "list_page":
            result = await engine.run_list_page(limit=params.get("limit", 0))
        elif mode == "retry":
            result = await engine.run_retry(
                max_retries=params.get("max_retries", 3),
                limit=params.get("limit", 50),
            )
        elif mode == "retry_urls":
            result = await engine.run_retry_urls(urls=params.get("urls", []))
        elif mode == "sitemap":
            result = await engine.run_sitemap(
                sitemap_index_url=params.get("sitemap_index_url"),
                date_from=params.get("date_from"),
                date_to=params.get("date_to"),
                limit=params.get("limit", 0),
                sitemap_offset=params.get("sitemap_offset", 0),
                sitemap_count=params.get("sitemap_count", 0),
            )
        else:
            _send({"type": "error", "error": f"Unknown mode: {mode}"})
            sys.exit(1)

        await engine.close()
        _send({"type": "completed", "stats": result})
        sys.stdout.close()  # Ensure pipe EOF reaches parent

    except asyncio.CancelledError:
        logger.info("Subprocess cancelled via stop signal")
        try:
            await engine.close()
        except Exception as close_err:
            logger.warning(f"Error closing engine: {close_err}")
        # Send partial stats on cancellation
        result = engine.stats.copy()
        result["early_stopped"] = True
        result["early_stop_reason"] = "User stopped via dashboard"
        _send({"type": "completed", "stats": result})
        sys.stdout.close()

    except Exception as e:
        logger.error(f"Subprocess error: {e}", exc_info=True)
        try:
            await engine.close()
        except Exception as close_err:
            logger.warning(f"Error closing engine: {close_err}")
        _send({"type": "error", "error": str(e)[:500]})
        sys.stdout.close()
        sys.exit(1)


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Crawler subprocess runner")
    arg_parser.add_argument("--params", required=True, help="JSON-encoded crawler params")
    arg_parser.add_argument("--task-id", required=True, help="Task ID for signal file")
    arg_parser.add_argument("--signal-dir", required=True, help="Directory for stop signal files")
    args = arg_parser.parse_args()

    params = json.loads(args.params)
    try:
        asyncio.run(main(params, args.task_id, args.signal_dir))
    except SystemExit:
        raise  # Re-raise sys.exit() from main() on real errors
    except Exception:
        # asyncio.run() cleanup may fail with ValueError('I/O operation on closed file')
        # when stderr fd is closed during interpreter shutdown on Windows.
        # main() already sent "completed"/"error" message before this point.
        pass
