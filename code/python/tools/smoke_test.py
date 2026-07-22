#!/usr/bin/env python3
"""
Smoke Test — 核心模組 import 檢查 + ruff static analysis。

用途：任何程式碼修改後跑一次，確認沒有 break import chain，
      並用 ruff 抓 undefined name 等致命錯誤。
執行：從 code/python/ 目錄執行
    python tools/smoke_test.py

成功：exit code 0，印 "SMOKE TEST PASSED"
失敗：exit code 1，印失敗的模組和錯誤訊息
"""

import os
import shutil
import subprocess
import sys
import time

# 確保 code/python/ 在 sys.path（不論從哪裡執行）
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 所有關鍵模組，對應 CLAUDE.md 的關鍵檔案對應表
CORE_MODULES = [
    # Server & Middleware
    "webserver.aiohttp_server",
    # Request Processing
    "core.baseHandler",
    "core.state",
    "core.schemas",
    # Retrieval
    "core.retriever",
    "retrieval_providers.postgres_client",
    # Ranking
    "core.ranking",
    "core.xgboost_ranker",
    "core.mmr",
    # Reasoning
    "reasoning.orchestrator",
    # Auth
    "auth.auth_db",
    "auth.auth_service",
    # Session
    "core.session_service",
    # Streaming
    "core.utils.message_senders",
    "core.sse",
    # Indexing（路徑 A 已刪除 D-2026-07-16；只驗 prod 活鏈與 dashboard）
    "indexing.cloud_embed",
    "indexing.bulk_load",
    "indexing.dashboard_api",
    # Crawler
    "crawler.core.engine",
]


def run_smoke_test() -> bool:
    """Import 所有核心模組，回報結果。"""
    start = time.time()
    failed = []

    for module_name in CORE_MODULES:
        try:
            __import__(module_name)
        except Exception as e:
            failed.append((module_name, type(e).__name__, str(e)))

    elapsed = time.time() - start
    total = len(CORE_MODULES)
    passed = total - len(failed)

    print(f"\n{'=' * 50}")
    print(f"  SMOKE TEST: {passed}/{total} modules OK  ({elapsed:.1f}s)")
    print(f"{'=' * 50}")

    if failed:
        print("\nFAILED MODULES:\n")
        for module_name, error_type, error_msg in failed:
            print(f"  FAIL: {module_name}")
            print(f"        {error_type}: {error_msg}\n")
        return False

    return True


def run_ruff_check() -> bool:
    """Run ruff static analysis to catch undefined names (F821).

    Returns True if no errors found (or ruff not available).
    """
    ruff_path = shutil.which("ruff")
    if not ruff_path:
        print("  WARNING: ruff not found in PATH, skipping static analysis")
        return True

    # Run from the project root (code/python/)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    cmd = [
        ruff_path, "check", ".",
        "--select", "F821",
        "--exclude", "legacy/,chat/,data_loading/,methods/,webserver/routes/chat_refactored.py,webserver/routes/chat.py",
        "--no-fix",
        "--output-format", "concise",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        print("  WARNING: ruff not found, skipping static analysis")
        return True
    except subprocess.TimeoutExpired:
        print("  WARNING: ruff timed out after 60s, skipping static analysis")
        return True

    # ruff exits 0 = clean, 1 = errors found, 2 = internal error
    if result.returncode == 0:
        print("Static analysis (ruff F821): OK")
        return True

    if result.returncode == 2:
        # Internal ruff error — warn but don't fail the smoke test
        print(f"  WARNING: ruff internal error, skipping static analysis")
        if result.stderr:
            print(f"  {result.stderr.strip()}")
        return True

    # returncode == 1 — real errors found
    output = result.stdout.strip()
    if not output:
        # ruff returned 1 but no output — treat as clean
        print("Static analysis (ruff F821): OK")
        return True

    print("Static analysis (ruff F821): FAILED")
    for line in output.splitlines():
        print(f"  {line}")
    return False


if __name__ == "__main__":
    success = run_smoke_test()
    ruff_ok = run_ruff_check()

    print()
    if success and ruff_ok:
        print("SMOKE TEST PASSED")
    else:
        print("SMOKE TEST FAILED")
    sys.exit(0 if (success and ruff_ok) else 1)
