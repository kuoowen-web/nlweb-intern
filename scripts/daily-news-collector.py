# 桌機每日新聞收集腳本 — 所有 source 統一執行
# 直接呼叫 CrawlerEngine（不經 dashboard subprocess，避免 Windows 限制）
#
# 使用方式：
#   cd C:/users/user/nlweb/code/python
#   python ../../scripts/daily-news-collector.py
#
# 各 source 策略：
#   chinatimes: todaynews.xml（~1000 URLs，涵蓋最近 2-3 天）
#   cna:        auto（~200-300/天，count=500）
#   ltn:        auto（從最新 ID 向後掃描 3000 個）
#   udn:        auto（從最新 ID 向後掃描 2000 個）
#   moea:       auto（月產量 ~30-50，count=50 足夠）
#   esg_bt:     auto（月產量 ~35，count=50 足夠）
#   einfo:      auto（月產量 ~30，count=50 足夠）
#
# 注意：list_page 模式目前對 curl_cffi sources 不相容（aiohttp 語法），
#       所有 source 改用 auto 模式。

import asyncio
import re
import sys
import os
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code", "python"))

# Suppress noisy logs from engine (it logs to its own file anyway)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _print_result(source: str, mode: str, result: dict, elapsed: float):
    success = result.get("success", 0)
    skipped = result.get("skipped", 0)
    failed = result.get("failed", 0)
    blocked = result.get("blocked", 0)
    not_found = result.get("not_found", 0)
    total = result.get("total", success + skipped + failed + not_found)
    print(f"  [{source}] {mode} | {elapsed:.1f}s | "
          f"success={success} skip={skipped} fail={failed} "
          f"blocked={blocked} not_found={not_found} total={total}")


async def run_chinatimes():
    """Chinatimes: 下載 todaynews.xml 並爬取"""
    from crawler.core.engine import CrawlerEngine
    from crawler.parsers.chinatimes_parser import ChinatimesParser

    start = time.time()
    try:
        from curl_cffi.requests import Session
        s = Session(impersonate="chrome")
        resp = s.get(
            "https://www.chinatimes.com/sitemaps/sitemap_todaynews.xml",
            timeout=30,
        )
        urls = re.findall(
            r"<loc>(https://www\.chinatimes\.com/[^<]+)</loc>", resp.text
        )
        print(f"  [chinatimes] Downloaded todaynews.xml: {len(urls)} URLs")
        if not urls:
            return {"success": 0, "skipped": 0, "failed": 0}

        parser = ChinatimesParser()
        engine = CrawlerEngine(parser=parser, auto_save=True)
        result = await engine.run_retry_urls(urls=urls)
        await engine.close()
        _print_result("chinatimes", "todaynews", result, time.time() - start)
        return result
    except Exception as e:
        print(f"  [chinatimes] ERROR: {e}")
        return {"success": 0, "failed": 1, "error": str(e)}



async def run_auto(source_name: str, count: int = 500):
    """auto 模式"""
    from crawler.core.engine import CrawlerEngine
    from crawler.parsers.factory import CrawlerFactory

    start = time.time()
    try:
        parser = CrawlerFactory.get_parser(source_name)
        engine = CrawlerEngine(parser=parser, auto_save=True)
        result = await engine.run_auto(
            count=count,
            stop_after_consecutive_skips=10,
        )
        await engine.close()
        _print_result(source_name, f"auto(count={count})", result, time.time() - start)
        return result
    except Exception as e:
        print(f"  [{source_name}] ERROR: {e}")
        return {"success": 0, "failed": 1, "error": str(e)}


# Source 配置: (source_name, runner_function, kwargs)
SOURCES = [
    ("chinatimes", run_chinatimes, {}),
    ("cna", run_auto, {"source_name": "cna", "count": 500}),
    ("ltn", run_auto, {"source_name": "ltn", "count": 3000}),
    ("udn", run_auto, {"source_name": "udn", "count": 2000}),
    ("moea", run_auto, {"source_name": "moea", "count": 50}),
    ("esg_businesstoday", run_auto, {"source_name": "esg_businesstoday", "count": 50}),
    ("einfo", run_auto, {"source_name": "einfo", "count": 50}),
]


async def _safe_run(source_name: str, runner, kwargs: dict) -> tuple:
    """Wrap a runner with exception handling, return (source_name, result)."""
    try:
        result = await runner(**kwargs)
        return source_name, result
    except Exception as e:
        print(f"  [{source_name}] FATAL: {e}")
        return source_name, {"success": 0, "failed": 1, "error": str(e)}


async def main():
    print(f"\n{'=' * 60}")
    print(f"  Daily News Collector — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")
    print(f"  Mode: parallel ({len(SOURCES)} sources)")

    total_start = time.time()

    # Run all sources in parallel
    tasks = [_safe_run(name, runner, kwargs) for name, runner, kwargs in SOURCES]
    completed = await asyncio.gather(*tasks)
    results = dict(completed)

    # Summary
    elapsed = time.time() - total_start
    print(f"\n{'=' * 60}")
    print(f"  Summary — {elapsed / 60:.1f} min total")
    print(f"{'=' * 60}")
    print(f"  {'Source':<20} {'Success':>8} {'Skip':>8} {'Failed':>8}")
    print(f"  {'-' * 48}")
    total_success = 0
    for src, r in results.items():
        s = r.get("success", 0)
        sk = r.get("skipped", 0)
        f = r.get("failed", 0)
        total_success += s
        err = f"  ({r['error'][:40]})" if r.get("error") else ""
        print(f"  {src:<20} {s:>8,} {sk:>8,} {f:>8,}{err}")
    print(f"  {'-' * 48}")
    print(f"  {'TOTAL':<20} {total_success:>8,}")
    print(f"\n  Done: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())
