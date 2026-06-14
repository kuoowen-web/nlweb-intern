# 桌機版 Chinatimes todaynews 每日收集腳本
# 直接執行 CrawlerEngine（不經 dashboard subprocess，避免 Windows 命令列長度限制）
#
# 使用方式：
#   cd C:/users/user/nlweb/code/python
#   python ../../scripts/chinatimes-todaynews.py
import asyncio
import re
import sys
import os
import time

# Ensure crawler modules are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code", "python"))


async def main():
    from crawler.core.engine import CrawlerEngine
    from crawler.parsers.chinatimes_parser import ChinatimesParser

    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} | Chinatimes todaynews collection ===")

    # 1. 下載 todaynews.xml
    print("[todaynews] Downloading sitemap_todaynews.xml...")
    try:
        from curl_cffi.requests import Session
        s = Session(impersonate="chrome")
        resp = s.get("https://www.chinatimes.com/sitemaps/sitemap_todaynews.xml", timeout=30)
        content = resp.text
    except Exception as e:
        print(f"[todaynews] ERROR: Failed to download: {e}")
        return

    urls = re.findall(r'<loc>(https://www\.chinatimes\.com/[^<]+)</loc>', content)
    print(f"[todaynews] Found {len(urls)} URLs")

    if not urls:
        print("[todaynews] ERROR: No URLs found")
        return

    # 2. 建立 engine 並執行
    parser = ChinatimesParser()
    engine = CrawlerEngine(parser=parser, auto_save=True)

    print(f"[todaynews] Starting crawl of {len(urls)} URLs...")
    start = time.time()
    result = await engine.run_retry_urls(urls=urls)
    elapsed = time.time() - start

    # 3. 報告結果
    success = result.get("success", 0)
    failed = result.get("failed", 0)
    skipped = result.get("skipped", 0)
    blocked = result.get("blocked", 0)

    print(f"\n[todaynews] DONE in {elapsed/60:.1f} min")
    print(f"  Success: {success}")
    print(f"  Failed:  {failed}")
    print(f"  Skipped: {skipped}")
    print(f"  Blocked: {blocked}")
    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} | Done ===")


if __name__ == "__main__":
    asyncio.run(main())
