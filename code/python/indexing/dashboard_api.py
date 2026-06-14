"""
dashboard_api.py - Indexing Dashboard API handlers

Provides REST API endpoints for:
- Statistics (Registry + Qdrant counts)
- Crawler control (start/stop/status)
- Source listing
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional
from aiohttp import web
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CrawlerTaskStatus(Enum):
    """Crawler task status"""
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    EARLY_STOPPED = "early_stopped"  # 因連續失敗/skip 提前停止
    FAILED = "failed"


# Full Scan source configurations
FULL_SCAN_CONFIG = {
    "udn":  {"type": "sequential", "name": "United Daily News", "start_id": 7_800_000, "default_end_id": 9_400_000},
    "ltn":  {"type": "sequential", "name": "Liberty Times Net", "start_id": 4_550_000, "default_end_id": 5_400_000},
    "einfo": {"type": "sequential", "name": "Environmental Info Center", "start_id": 230_000, "default_end_id": 270_000},
    "cna":  {"type": "date_based", "name": "Central News Agency"},
    "esg_businesstoday": {"type": "date_based", "name": "ESG BusinessToday"},
    "chinatimes": {"type": "date_based", "name": "China Times"},
    "moea": {"type": "sequential", "name": "Ministry of Economic Affairs", "start_id": 110_000, "default_end_id": 122_000},
}

# Sources that auto-restart after early_stop (e.g., rate-limit recovery).
# Maps source_id -> delay in seconds before restarting.
AUTO_RESTART_DELAY = {
    "moea": 900,  # 15 minutes — rate limit recovery
}


@dataclass
class CrawlerTask:
    """Represents a running crawler task"""
    task_id: str
    source: str
    mode: str
    count: int
    status: CrawlerTaskStatus = CrawlerTaskStatus.IDLE
    progress: int = 0
    total: int = 0
    started_at: float = 0
    finished_at: float = 0
    error: Optional[str] = None
    early_stop_reason: Optional[str] = None  # 提前停止的原因
    stats: Dict[str, int] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)  # 原始參數，用於 resume
    _process: Optional[asyncio.subprocess.Process] = None   # subprocess handle
    _reader_task: Optional[asyncio.Task] = None             # stdout reader task
    _pid: Optional[int] = None                              # subprocess PID (persisted for orphan cleanup)
    # Full scan specific fields
    last_scanned_id: Optional[int] = None       # checkpoint for sequential
    last_scanned_date: Optional[str] = None      # checkpoint for date-based
    scan_start: Optional[str] = None             # "7800000" or "2024-01-01"
    scan_end: Optional[str] = None               # "9313000" or "2026-02-07"


ALLOWED_SOURCES = {"ltn", "udn", "cna", "esg_businesstoday", "einfo", "chinatimes", "moea"}
ALLOWED_MODES = {"auto", "full_scan", "retry", "retry_urls", "list_page", "sitemap"}
MAX_COUNT = 100_000
MAX_LIMIT = 10_000


class IndexingDashboardAPI:
    """API handlers for Indexing Dashboard"""

    # 持久化檔案路徑（使用專案根目錄的絕對路徑）
    TASKS_FILE = str(Path(__file__).parent.parent.parent.parent / "data" / "crawler" / "crawler_tasks.json")
    SIGNAL_DIR = str(Path(__file__).parent.parent.parent.parent / "data" / "crawler" / "signals")

    def __init__(self):
        self._crawler_tasks: Dict[str, CrawlerTask] = {}
        self._task_counter = 0
        self._websockets: set = set()
        self._last_save_time: float = 0.0  # Throttle _save_tasks
        self._save_interval: float = 5.0   # Save at most every 5 seconds
        self._pending_auto_resume: list = []  # zombie task IDs to auto-resume
        self._load_tasks()  # 啟動時載入歷史 tasks

    # ==================== Statistics APIs ====================

    async def get_stats(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/stats

        Returns combined statistics from:
        - CrawledRegistry (articles by source)
        - Qdrant (vector counts) - if available
        """
        try:
            stats = {
                "registry": await self._get_registry_stats(),
                "qdrant": await self._get_qdrant_stats(),
                "timestamp": time.time()
            }
            return web.json_response(stats)
        except Exception as e:
            logger.error(f"Failed to get stats: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _get_registry_stats(self) -> Dict[str, Any]:
        """Get statistics from CrawledRegistry"""
        try:
            from crawler.core.crawled_registry import get_registry
            registry = get_registry()
            stats = registry.get_stats()
            date_ranges = registry.get_date_range_by_source()
            return {
                "total_articles": stats.get("total", 0),
                "by_source": stats.get("by_source", {}),
                "date_ranges": date_ranges
            }
        except Exception as e:
            logger.warning(f"Failed to get registry stats: {e}")
            return {"total_articles": 0, "by_source": {}, "date_ranges": {}, "error": str(e)}

    async def _get_qdrant_stats(self) -> Dict[str, Any]:
        """Get statistics from Qdrant (if available).

        Runs synchronous QdrantClient in executor to avoid blocking event loop.
        """
        try:
            from qdrant_client import QdrantClient
            import os

            qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
            collection_name = os.environ.get("QDRANT_COLLECTION", "nlweb")

            def _sync_qdrant():
                client = QdrantClient(url=qdrant_url, timeout=3)
                try:
                    info = client.get_collection(collection_name)
                    return {
                        "vectors_count": info.vectors_count,
                        "points_count": info.points_count,
                        "collection": collection_name
                    }
                except Exception:
                    return {"vectors_count": 0, "points_count": 0, "error": "Collection not found"}
                finally:
                    client.close()

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync_qdrant)

        except ImportError:
            return {"vectors_count": 0, "error": "qdrant-client not installed"}
        except Exception as e:
            logger.warning(f"Failed to get Qdrant stats: {e}")
            return {"vectors_count": 0, "error": str(e)}

    async def get_sources(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/sources

        Returns list of available news sources with their configurations.
        """
        try:
            from crawler.core import settings

            sources = []
            for source_id, config in settings.NEWS_SOURCES.items():
                sources.append({
                    "id": source_id,
                    "name": config.get("name", source_id),
                    "concurrent_limit": config.get("concurrent_limit", 3),
                    "delay_range": config.get("delay_range", (1.0, 2.0))
                })

            return web.json_response({
                "sources": sources,
                "count": len(sources)
            })
        except Exception as e:
            logger.error(f"Failed to get sources: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def get_monthly_stats(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/stats/monthly/{source_id}

        Returns monthly article counts for a source from the registry.
        """
        source_id = request.match_info.get("source_id")
        if not source_id:
            return web.json_response({"error": "source_id is required"}, status=400)

        try:
            from crawler.core.crawled_registry import get_registry
            registry = get_registry()
            monthly = registry.get_monthly_counts(source_id)
            return web.json_response({
                "source_id": source_id,
                "months": monthly,
                "total": sum(m["count"] for m in monthly)
            })
        except Exception as e:
            logger.error(f"Failed to get monthly stats for {source_id}: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    # ==================== Crawler Control APIs ====================

    async def start_crawler(self, request: web.Request) -> web.Response:
        """
        POST /api/indexing/crawler/start

        Request body:
        {
            "source": "ltn",
            "mode": "auto",  // "auto" | "backfill" | "range"
            "count": 100,    // max articles for auto/backfill
            "stop_after_skips": 10,  // for auto mode: stop after N consecutive skips
            "overlap": 10,   // for backfill mode: start from oldest - overlap
            "start_id": 123, // for range mode
            "end_id": 456,   // for range mode
            "chunk_size": 5000,    // optional: split output every N articles
            "chunk_by_month": true // optional: split output by article month
        }
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        source = body.get("source")
        mode = body.get("mode", "auto")
        count = body.get("count", 100)

        if not source:
            return web.json_response({"error": "source is required"}, status=400)

        if source not in ALLOWED_SOURCES:
            return web.json_response({"error": f"Invalid source: {source}. Allowed: {sorted(ALLOWED_SOURCES)}"}, status=400)

        if mode not in ALLOWED_MODES:
            return web.json_response({"error": f"Invalid mode: {mode}. Allowed: {sorted(ALLOWED_MODES)}"}, status=400)

        # Cap count/limit to prevent resource exhaustion
        try:
            count = min(int(count), MAX_COUNT)
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid count parameter"}, status=400)
        if "limit" in body:
            try:
                body["limit"] = min(int(body["limit"]), MAX_LIMIT)
            except (ValueError, TypeError):
                return web.json_response({"error": "Invalid limit parameter"}, status=400)

        # Check if there's already a running task for this source
        for task in self._crawler_tasks.values():
            if task.source == source and task.status == CrawlerTaskStatus.RUNNING:
                return web.json_response({
                    "error": f"Crawler for {source} is already running",
                    "task_id": task.task_id
                }, status=409)

        # Create new task
        self._task_counter += 1
        task_id = f"crawler_{source}_{self._task_counter}_{int(time.time())}"

        # Determine scan_start/scan_end for full_scan mode display
        scan_start = None
        scan_end = None
        if mode == "full_scan":
            config = FULL_SCAN_CONFIG.get(source, {})
            if config.get("type") == "sequential":
                scan_start = str(body.get("start_id", config.get("start_id", "")))
                scan_end = str(body.get("end_id", config.get("default_end_id", "")))
            elif config.get("type") == "date_based":
                scan_start = body.get("start_date", "2024-01-01")
                scan_end = body.get("end_date", time.strftime("%Y-%m-%d"))

        task = CrawlerTask(
            task_id=task_id,
            source=source,
            mode=mode,
            count=count,
            status=CrawlerTaskStatus.RUNNING,
            total=count,
            started_at=time.time(),
            scan_start=scan_start,
            scan_end=scan_end,
        )

        task.params = body
        self._crawler_tasks[task_id] = task
        self._save_tasks()  # 保存新任務

        # Start crawler as subprocess
        reader_task = asyncio.create_task(
            self._run_crawler_subprocess(task, body)
        )
        task._reader_task = reader_task

        logger.info(f"Started crawler task: {task_id} for source={source}, mode={mode}, count={count}")

        return web.json_response({
            "task_id": task_id,
            "source": source,
            "mode": mode,
            "count": count,
            "status": task.status.value
        })

    async def _run_crawler_subprocess(self, task: CrawlerTask, params: Dict[str, Any]) -> None:
        """Launch crawler as subprocess and read JSON lines from stdout."""
        signal_dir = Path(self.SIGNAL_DIR)
        signal_dir.mkdir(parents=True, exist_ok=True)

        params_json = json.dumps({**params, "source": task.source})

        # Must run from code/python/ directory for imports to work
        code_dir = str(Path(__file__).parent.parent)

        # Redirect stderr to per-task log files instead of piping through event loop.
        # Piping stderr from 6+ subprocesses saturates the asyncio event loop on Windows,
        # starving HTTP handlers and causing API timeouts.
        log_dir = signal_dir  # reuse signal dir for log files
        stderr_log_path = log_dir / f"{task.task_id}.stderr.log"
        stderr_log_file = open(stderr_log_path, "w", encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "crawler.subprocess_runner",
                "--params", params_json,
                "--task-id", task.task_id,
                "--signal-dir", str(signal_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_log_file,
                cwd=code_dir,
            )
        except Exception:
            stderr_log_file.close()
            raise
        task._process = proc
        task._pid = proc.pid
        task._stderr_log_file = stderr_log_file
        logger.info(f"Subprocess launched: PID={proc.pid} for task {task.task_id} (stderr -> {stderr_log_path})")

        try:
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON stdout from subprocess: {text[:200]}")
                    continue

                msg_type = msg.get("type")

                if msg_type == "progress":
                    stats = msg["stats"]
                    task.stats = stats
                    task.progress = stats.get("progress", stats.get("success", 0) + stats.get("skipped", 0))
                    task.total = stats.get("total", task.total)
                    if "last_scanned_id" in stats:
                        task.last_scanned_id = stats["last_scanned_id"]
                    if "last_scanned_date" in stats:
                        task.last_scanned_date = stats["last_scanned_date"]
                    # Backfill scan_start/scan_end from engine stats if not set
                    if task.scan_start is None and "scan_start" in stats:
                        task.scan_start = str(stats["scan_start"])
                    if task.scan_end is None and "scan_end" in stats:
                        task.scan_end = str(stats["scan_end"])
                    asyncio.create_task(self._broadcast_status(task))
                    await asyncio.sleep(0)  # Yield to event loop

                elif msg_type == "completed":
                    task.stats = msg["stats"]
                    if msg["stats"].get("early_stopped"):
                        task.status = CrawlerTaskStatus.EARLY_STOPPED
                        task.early_stop_reason = msg["stats"].get("early_stop_reason")
                    else:
                        task.status = CrawlerTaskStatus.COMPLETED
                    task.finished_at = time.time()
                    logger.info(f"Subprocess completed: task {task.task_id}")

                elif msg_type == "error":
                    task.status = CrawlerTaskStatus.FAILED
                    task.error = msg.get("error", "Unknown subprocess error")
                    task.finished_at = time.time()
                    logger.error(f"Subprocess error: task {task.task_id}: {task.error}")

            await proc.wait()
            # Close stderr log file
            if hasattr(task, '_stderr_log_file') and task._stderr_log_file:
                try:
                    task._stderr_log_file.close()
                except Exception:
                    pass

            # If process exited with error but we didn't get an error message
            if proc.returncode != 0 and task.status == CrawlerTaskStatus.RUNNING:
                task.status = CrawlerTaskStatus.FAILED
                task.error = f"Subprocess exited with code {proc.returncode}"
                task.finished_at = time.time()
                logger.error(f"Subprocess exited with code {proc.returncode}: {task.task_id}")

            # Process exited cleanly but we didn't get a completed message
            elif proc.returncode == 0 and task.status == CrawlerTaskStatus.RUNNING:
                if task.stats.get("early_stopped"):
                    task.status = CrawlerTaskStatus.EARLY_STOPPED
                    task.early_stop_reason = task.stats.get("early_stop_reason")
                else:
                    task.status = CrawlerTaskStatus.COMPLETED
                task.finished_at = time.time()
                logger.info(f"Subprocess exited cleanly (inferred completed): {task.task_id}")

        except asyncio.CancelledError:
            # Reader task cancelled — happens when force-killing a subprocess
            # (Windows pipes may not close cleanly after proc.kill())
            if task.status in (CrawlerTaskStatus.RUNNING, CrawlerTaskStatus.STOPPING):
                task.status = CrawlerTaskStatus.FAILED
                task.error = task.error or "Subprocess force-terminated"
                task.finished_at = task.finished_at or time.time()
                logger.info(f"Reader task cancelled for {task.task_id}")

        except Exception as e:
            task.status = CrawlerTaskStatus.FAILED
            task.error = str(e)
            task.finished_at = time.time()
            logger.error(f"Subprocess reader error: {task.task_id}: {e}", exc_info=True)

        finally:
            # Close stderr log file if still open
            if hasattr(task, '_stderr_log_file') and task._stderr_log_file:
                try:
                    task._stderr_log_file.close()
                except Exception:
                    pass
            # Clean up signal file
            signal_file = signal_dir / f".stop_{task.task_id}"
            if signal_file.exists():
                try:
                    signal_file.unlink()
                except OSError:
                    pass
            self._save_tasks()
            await self._broadcast_status(task)

            # Schedule auto-restart for early_stopped sources (e.g., MOEA rate-limit recovery)
            if task.status == CrawlerTaskStatus.EARLY_STOPPED:
                delay = AUTO_RESTART_DELAY.get(task.source)
                if delay:
                    logger.info(f"Auto-restart: {task.source} will restart in {delay}s (task {task.task_id})")
                    asyncio.create_task(self._delayed_restart(task.task_id, delay))

    async def _delayed_restart(self, old_task_id: str, delay: float) -> None:
        """Wait `delay` seconds, then auto-resume an early_stopped task from its checkpoint."""
        try:
            old_task = self._crawler_tasks.get(old_task_id)
            source = old_task.source if old_task else old_task_id
            logger.info(f"Auto-restart: waiting {delay}s before restarting {source}...")
            await asyncio.sleep(delay)

            # Check if source already has a running task (user may have manually restarted)
            for task in self._crawler_tasks.values():
                if task.source == source and task.status == CrawlerTaskStatus.RUNNING:
                    logger.info(f"Auto-restart: skipping {old_task_id}, {source} already running")
                    return

            new_task_id = await self._auto_resume_task(old_task_id)
            if new_task_id:
                logger.info(f"Auto-restart: {source} restarted as {new_task_id}")
            else:
                logger.warning(f"Auto-restart: failed to restart {source} from {old_task_id}")
        except asyncio.CancelledError:
            logger.info(f"Auto-restart: cancelled for {old_task_id}")
        except Exception as e:
            logger.error(f"Auto-restart: error restarting {old_task_id}: {e}", exc_info=True)

    async def _run_crawler_inprocess(self, task: CrawlerTask, params: Dict[str, Any]) -> None:
        """Run crawler in-process (legacy fallback)."""
        try:
            # Import crawler components
            from crawler.core.engine import CrawlerEngine
            from crawler.core import settings

            source = task.source

            # Get parser for source
            parser = await self._get_parser(source)
            if parser is None:
                task.status = CrawlerTaskStatus.FAILED
                task.error = f"Unknown source: {source}"
                task.finished_at = time.time()
                await self._broadcast_status(task)
                return

            # Progress callback to update task and broadcast
            def on_progress(stats: Dict[str, Any]):
                task.stats = stats
                task.progress = stats.get("progress", stats.get("success", 0) + stats.get("skipped", 0))
                task.total = stats.get("total", task.total)
                # Update full_scan checkpoints
                if "last_scanned_id" in stats:
                    task.last_scanned_id = stats["last_scanned_id"]
                if "last_scanned_date" in stats:
                    task.last_scanned_date = stats["last_scanned_date"]
                # Schedule async broadcast
                asyncio.create_task(self._broadcast_status(task))

            # Get chunk settings
            chunk_size = params.get("chunk_size", 0)
            chunk_by_month = params.get("chunk_by_month", False)

            # Create engine with progress callback and chunk settings
            engine = CrawlerEngine(
                parser=parser,
                auto_save=True,
                progress_callback=on_progress,
                chunk_size=chunk_size,
                chunk_by_month=chunk_by_month
            )

            # Run based on mode
            mode = params.get("mode", "auto")

            if mode == "auto":
                count = params.get("count", 100)
                stop_after_skips = params.get("stop_after_skips", 10)
                date_floor = params.get("date_floor")  # "YYYY-MM" format
                result = await engine.run_auto(
                    count=count,
                    stop_after_consecutive_skips=stop_after_skips,
                    date_floor=date_floor
                )
            elif mode == "full_scan":
                result = await engine.run_full_scan(
                    start_id=params.get("start_id"),
                    end_id=params.get("end_id"),
                    start_date=params.get("start_date"),
                    end_date=params.get("end_date"),
                )
            elif mode == "retry":
                max_retries = params.get("max_retries", 3)
                limit = params.get("limit", 50)
                result = await engine.run_retry(max_retries=max_retries, limit=limit)
            elif mode == "retry_urls":
                urls = params.get("urls", [])
                result = await engine.run_retry_urls(urls=urls)
            elif mode == "list_page":
                limit = params.get("limit", 0)
                result = await engine.run_list_page(limit=limit)
            else:
                task.status = CrawlerTaskStatus.FAILED
                task.error = f"Unknown mode: {mode}"
                task.finished_at = time.time()
                await self._broadcast_status(task)
                return

            # Update task with results
            task.stats = result
            task.progress = result.get("success", 0) + result.get("skipped", 0)

            # 檢查是否提前停止
            if result.get("early_stopped"):
                task.status = CrawlerTaskStatus.EARLY_STOPPED
                task.early_stop_reason = result.get("early_stop_reason", "Unknown reason")
                logger.info(f"Crawler task {task.task_id} early stopped: {task.early_stop_reason}")
            else:
                task.status = CrawlerTaskStatus.COMPLETED
                logger.info(f"Crawler task {task.task_id} completed: {task.stats}")

            task.finished_at = time.time()

            await engine.close()

            await self._broadcast_status(task)

        except asyncio.CancelledError:
            task.status = CrawlerTaskStatus.STOPPING
            task.finished_at = time.time()
            logger.info(f"Crawler task {task.task_id} was cancelled")
            await self._broadcast_status(task)

        except Exception as e:
            task.status = CrawlerTaskStatus.FAILED
            task.error = str(e)
            task.finished_at = time.time()
            logger.error(f"Crawler task {task.task_id} failed: {e}", exc_info=True)
            await self._broadcast_status(task)

    async def _get_parser(self, source: str):
        """Get parser instance for source using CrawlerFactory"""
        try:
            from crawler.parsers.factory import CrawlerFactory
            parser = CrawlerFactory.get_parser(source)
            if parser is None:
                logger.error(f"No parser found for source: {source}")
            return parser
        except Exception as e:
            logger.error(f"Failed to get parser for {source}: {e}")
            return None

    def _load_tasks(self) -> None:
        """從檔案載入歷史 tasks。Running/stopping 狀態的 task 在重啟後：
        1. 建立 signal file 讓孤兒 subprocess graceful shutdown
        2. 嘗試 terminate 孤兒 subprocess (by saved PID)
        3. 標記為 failed
        4. 收集可 auto-resume 的 full_scan tasks
        """
        tasks_path = Path(self.TASKS_FILE)
        if not tasks_path.exists():
            logger.info("No saved tasks found")
            return

        try:
            with open(tasks_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            zombie_count = 0
            orphans_killed = 0
            for task_data in data.get('tasks', []):
                status = CrawlerTaskStatus(task_data['status'])

                # 重啟後 running/stopping 的 task = zombie（subprocess 可能仍在跑）
                if status in (CrawlerTaskStatus.RUNNING, CrawlerTaskStatus.STOPPING):
                    task_id = task_data['task_id']
                    pid = task_data.get('pid')

                    # Step 1: 建立 signal file，讓孤兒 subprocess 自行停止
                    signal_dir = Path(self.SIGNAL_DIR)
                    signal_dir.mkdir(parents=True, exist_ok=True)
                    signal_file = signal_dir / f".stop_{task_id}"
                    try:
                        signal_file.touch()
                    except OSError:
                        pass

                    # Step 2: 嘗試 terminate 孤兒 subprocess
                    if pid:
                        if self._kill_orphan_process(pid):
                            orphans_killed += 1

                    status = CrawlerTaskStatus.FAILED
                    task_data['error'] = "Server restarted while task was running"
                    task_data['finished_at'] = task_data.get('finished_at') or time.time()
                    zombie_count += 1

                    # Collect full_scan tasks with checkpoints for auto-resume
                    if (task_data['mode'] == 'full_scan'
                            and (task_data.get('last_scanned_id') is not None
                                 or task_data.get('last_scanned_date'))):
                        self._pending_auto_resume.append(task_id)

                task = CrawlerTask(
                    task_id=task_data['task_id'],
                    source=task_data['source'],
                    mode=task_data['mode'],
                    count=task_data['count'],
                    status=status,
                    progress=task_data.get('progress', 0),
                    total=task_data.get('total', 0),
                    started_at=task_data.get('started_at', 0),
                    finished_at=task_data.get('finished_at', 0),
                    error=task_data.get('error'),
                    early_stop_reason=task_data.get('early_stop_reason'),
                    stats=task_data.get('stats', {}),
                    params=task_data.get('params', {}),
                    last_scanned_id=task_data.get('last_scanned_id'),
                    last_scanned_date=task_data.get('last_scanned_date'),
                    scan_start=task_data.get('scan_start'),
                    scan_end=task_data.get('scan_end'),
                    _pid=task_data.get('pid'),
                )
                self._crawler_tasks[task.task_id] = task

            self._task_counter = data.get('task_counter', 0)
            if zombie_count > 0:
                logger.warning(
                    f"Marked {zombie_count} zombie tasks as failed "
                    f"(killed {orphans_killed} orphan subprocess(es))"
                )
                self._save_tasks()
            logger.info(f"Loaded {len(self._crawler_tasks)} tasks from {self.TASKS_FILE}")

        except Exception as e:
            logger.warning(f"Failed to load tasks: {e}")

    @staticmethod
    def _kill_orphan_process(pid: int) -> bool:
        """Try to terminate an orphan subprocess by PID. Returns True if killed."""
        try:
            # Check if process is still alive
            os.kill(pid, 0)  # signal 0 = existence check, doesn't kill
        except (OSError, SystemError):
            # Process doesn't exist, or Windows raised SystemError for special PIDs
            return False

        try:
            if sys.platform == "win32":
                # Windows: use taskkill for reliable termination
                os.system(f"taskkill /F /PID {pid} >nul 2>&1")
            else:
                os.kill(pid, signal.SIGTERM)
            logger.info(f"Killed orphan subprocess PID={pid}")
            return True
        except (OSError, SystemError) as e:
            logger.warning(f"Failed to kill orphan PID={pid}: {e}")
            return False

    _save_lock: Optional[asyncio.Lock] = None  # Initialized lazily to avoid event loop issues

    def _get_save_lock(self) -> asyncio.Lock:
        """Get or create the save lock (must be called from async context)."""
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()
        return self._save_lock

    def _save_tasks(self) -> None:
        """將 tasks 保存到檔案（atomic write: temp file + os.replace）.

        Called from both sync and async contexts. Uses atomic write to prevent
        corruption from concurrent writes or crashes.
        """
        tasks_path = Path(self.TASKS_FILE)
        tasks_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Prune old completed/failed tasks (keep most recent 50)
            completed = [(k, v) for k, v in self._crawler_tasks.items()
                         if v.status in (CrawlerTaskStatus.COMPLETED, CrawlerTaskStatus.FAILED,
                                         CrawlerTaskStatus.EARLY_STOPPED)]
            if len(completed) > 50:
                completed.sort(key=lambda x: x[1].finished_at or 0, reverse=True)
                for k, v in completed[50:]:
                    del self._crawler_tasks[k]

            data = {
                'tasks': [self._task_to_dict(t) for t in self._crawler_tasks.values()],
                'task_counter': self._task_counter,
            }
            content = json.dumps(data, ensure_ascii=False, indent=2)

            def _atomic_write():
                temp_path = tasks_path.with_suffix('.tmp')
                with open(temp_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                os.replace(str(temp_path), str(tasks_path))

            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, _atomic_write)
            except RuntimeError:
                # No running event loop (e.g. during sync init) — write directly
                _atomic_write()

        except Exception as e:
            logger.warning(f"Failed to save tasks: {e}")

    async def get_crawler_status(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/crawler/status
        GET /api/indexing/crawler/status/{task_id}

        Returns status of crawler task(s).
        """
        task_id = request.match_info.get("task_id")

        if task_id:
            # Get specific task
            task = self._crawler_tasks.get(task_id)
            if not task:
                return web.json_response({"error": "Task not found"}, status=404)
            return web.json_response(self._task_to_dict(task))
        else:
            # Get all tasks
            tasks = [self._task_to_dict(t) for t in self._crawler_tasks.values()]
            return web.json_response({
                "tasks": tasks,
                "count": len(tasks)
            })

    async def stop_crawler(self, request: web.Request) -> web.Response:
        """
        POST /api/indexing/crawler/stop

        Request body:
        {
            "task_id": "crawler_ltn_1_1234567890"
        }
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        task_id = body.get("task_id")
        if not task_id:
            return web.json_response({"error": "task_id is required"}, status=400)

        task = self._crawler_tasks.get(task_id)
        if not task:
            return web.json_response({"error": "Task not found"}, status=404)

        if task.status != CrawlerTaskStatus.RUNNING:
            return web.json_response({
                "error": f"Task is not running (status: {task.status.value})"
            }, status=400)

        # Create signal file for graceful stop
        signal_file = Path(self.SIGNAL_DIR) / f".stop_{task_id}"
        signal_file.parent.mkdir(parents=True, exist_ok=True)
        signal_file.touch()

        # Give subprocess time for graceful shutdown, then force terminate
        async def _force_kill_after(proc, timeout=10):
            await asyncio.sleep(timeout)
            if proc.returncode is None:
                logger.warning(f"Force terminating subprocess for task {task_id}")
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                # Cancel the reader task to unblock the stdout pipe reader
                # On Windows, proc.terminate()/kill() may not close pipes cleanly,
                # causing `async for line in proc.stdout` to hang indefinitely.
                if hasattr(task, '_reader_task') and task._reader_task:
                    task._reader_task.cancel()

        if task._process and task._process.returncode is None:
            asyncio.create_task(_force_kill_after(task._process))

        task.status = CrawlerTaskStatus.STOPPING

        logger.info(f"Stopping crawler task: {task_id} (signal file created)")

        return web.json_response({
            "task_id": task_id,
            "status": task.status.value,
            "message": "Stop signal sent"
        })

    # ==================== WebSocket for Real-time Updates ====================

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """
        WebSocket endpoint for real-time crawler status updates.

        GET /api/indexing/ws
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._websockets.add(ws)
        logger.info(f"WebSocket client connected. Total: {len(self._websockets)}")

        try:
            # Send current status on connect
            tasks = [self._task_to_dict(t) for t in self._crawler_tasks.values()]
            await ws.send_json({
                "type": "init",
                "tasks": tasks
            })

            # Keep connection alive
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # Handle ping/pong or other messages
                    if msg.data == "ping":
                        await ws.send_str("pong")
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break

        finally:
            self._websockets.discard(ws)
            logger.info(f"WebSocket client disconnected. Total: {len(self._websockets)}")

        return ws

    async def _broadcast_status(self, task: CrawlerTask) -> None:
        """Broadcast task status to all WebSocket clients"""
        message = {
            "type": "status_update",
            "task": self._task_to_dict(task)
        }

        if not self._websockets:
            # Skip broadcast overhead when no clients connected
            pass
        else:
            async def _send_safe(ws):
                try:
                    await ws.send_json(message)
                except Exception as e:
                    logger.warning(f"Failed to send WebSocket message: {e}")
                    self._websockets.discard(ws)

            await asyncio.gather(
                *[_send_safe(ws) for ws in list(self._websockets)],
                return_exceptions=True
            )

        # 持久化 tasks（節流：最多每 N 秒保存一次，終態立即保存）
        now = time.time()
        is_terminal = task.status in (
            CrawlerTaskStatus.COMPLETED,
            CrawlerTaskStatus.FAILED,
            CrawlerTaskStatus.EARLY_STOPPED,
        )
        if is_terminal or (now - self._last_save_time >= self._save_interval):
            self._save_tasks()
            self._last_save_time = now

    # ==================== Failed URLs / Errors APIs ====================

    async def get_errors(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/errors

        Query params:
            source: Filter by source (optional)
            error_type: Filter by single error type (optional)
            error_types: Filter by multiple error types, comma-separated (optional)
            limit: Max results (default 100)
            offset: Pagination offset (default 0)
        """
        try:
            source = request.query.get("source")
            error_type = request.query.get("error_type")
            error_types_str = request.query.get("error_types")
            try:
                limit = int(request.query.get("limit", 100))
                offset = int(request.query.get("offset", 0))
            except (ValueError, TypeError):
                return web.json_response({"error": "Invalid limit or offset parameter"}, status=400)

            # Parse multiple error types
            error_types = None
            if error_types_str:
                error_types = [t.strip() for t in error_types_str.split(",") if t.strip()]
            elif error_type:
                error_types = [error_type]

            from crawler.core.crawled_registry import get_registry
            registry = get_registry()

            errors = registry.get_failed_urls(
                source_id=source,
                error_types=error_types,
                limit=limit,
                offset=offset
            )

            stats = registry.get_failed_stats()

            return web.json_response({
                "errors": errors,
                "count": len(errors),
                "stats": stats
            })
        except Exception as e:
            logger.error(f"Failed to get errors: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def clear_errors(self, request: web.Request) -> web.Response:
        """
        POST /api/indexing/errors/clear

        Request body:
        {
            "source": "ltn",  // optional, if not provided clears all
            "urls": ["url1", "url2"]  // optional, specific URLs to clear
        }
        """
        try:
            body = await request.json()
        except Exception:
            body = {}

        source = body.get("source")
        urls = body.get("urls")

        try:
            from crawler.core.crawled_registry import get_registry
            registry = get_registry()

            if urls and isinstance(urls, list):
                # Clear specific URLs
                count = 0
                for url in urls:
                    if registry.remove_failed(url):
                        count += 1
                return web.json_response({
                    "cleared": count,
                    "mode": "selected"
                })
            else:
                # Clear by source/error_types or all
                error_types = body.get("error_types")
                count = registry.clear_failed(source_id=source, error_types=error_types)
                return web.json_response({
                    "cleared": count,
                    "source": source or "all",
                    "error_types": error_types
                })
        except Exception as e:
            logger.error(f"Failed to clear errors: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def retry_errors(self, request: web.Request) -> web.Response:
        """
        POST /api/indexing/errors/retry

        Request body (option 1 - by source with optional filters):
        {
            "source": "ltn",
            "error_types": ["blocked", "parse_error"],  // optional filter
            "max_retries": 3,
            "limit": 50
        }

        Request body (option 2 - specific URLs):
        {
            "urls": ["url1", "url2"],
            "sources": {"ltn": ["url1"], "udn": ["url2"]}
        }

        Request body (option 3 - retry all with filters):
        {
            "retry_all": true,
            "source": "ltn",  // optional
            "error_types": ["blocked", "parse_error"]  // optional
        }

        This is a convenience endpoint that starts a retry crawler task.
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        urls = body.get("urls")
        sources = body.get("sources")  # Dict: source -> list of URLs
        retry_all = body.get("retry_all", False)

        # Mode 0: Retry all with filters (no limit)
        if retry_all:
            return await self._retry_all_filtered(body)

        # Mode 1: Specific URLs grouped by source
        if urls and sources:
            return await self._retry_specific_urls(sources)

        # Mode 2: By source
        source = body.get("source")
        if not source:
            return web.json_response({"error": "source is required (or provide urls + sources)"}, status=400)

        # Check if there's already a running task for this source
        for task in self._crawler_tasks.values():
            if task.source == source and task.status == CrawlerTaskStatus.RUNNING:
                return web.json_response({
                    "error": f"Crawler for {source} is already running",
                    "task_id": task.task_id
                }, status=409)

        # Get count of retryable errors
        from crawler.core.crawled_registry import get_registry
        registry = get_registry()
        max_retries = body.get("max_retries", 3)
        limit = body.get("limit", 50)
        retry_urls = registry.get_failed_urls_for_retry(
            source_id=source,
            max_retries=max_retries,
            limit=limit
        )

        if not retry_urls:
            return web.json_response({
                "error": f"No failed URLs to retry for {source}",
                "count": 0
            }, status=404)

        # Create retry task
        self._task_counter += 1
        task_id = f"retry_{source}_{self._task_counter}_{int(time.time())}"

        task = CrawlerTask(
            task_id=task_id,
            source=source,
            mode="retry",
            count=len(retry_urls),
            status=CrawlerTaskStatus.RUNNING,
            total=len(retry_urls),
            started_at=time.time()
        )

        self._crawler_tasks[task_id] = task

        # Start retry in background
        params = {
            "source": source,
            "mode": "retry",
            "max_retries": max_retries,
            "limit": limit
        }
        reader_task = asyncio.create_task(self._run_crawler_subprocess(task, params))
        task._reader_task = reader_task

        logger.info(f"Started retry task: {task_id} for {len(retry_urls)} URLs")

        return web.json_response({
            "task_id": task_id,
            "source": source,
            "mode": "retry",
            "count": len(retry_urls),
            "status": task.status.value
        })

    async def _retry_all_filtered(self, params: Dict[str, Any]) -> web.Response:
        """
        Retry all failed URLs matching filters (no limit).

        Args:
            params: Dict with optional 'source' and 'error_types' filters
        """
        source_filter = params.get("source")
        error_types = params.get("error_types")
        max_retries = params.get("max_retries", 3)

        from crawler.core.crawled_registry import get_registry
        registry = get_registry()

        # Get ALL failed URLs matching filters (use large limit)
        all_failed = registry.get_failed_urls(
            source_id=source_filter,
            error_types=error_types,
            limit=10000  # Large limit to get all
        )

        if not all_failed:
            return web.json_response({
                "error": "No failed URLs matching filters",
                "count": 0
            }, status=404)

        # Group by source
        by_source: Dict[str, list] = {}
        for err in all_failed:
            src = err["source_id"]
            if src not in by_source:
                by_source[src] = []
            by_source[src].append(err["url"])

        # Clear these URLs first
        for url_list in by_source.values():
            for url in url_list:
                registry.remove_failed(url)

        # Start retry tasks for each source
        return await self._retry_specific_urls(by_source)

    async def _retry_specific_urls(self, sources: Dict[str, list]) -> web.Response:
        """
        Retry specific URLs grouped by source.

        Args:
            sources: Dict mapping source_id to list of URLs
        """
        task_ids = []
        total_urls = 0

        for source, urls in sources.items():
            if not urls:
                continue

            # Check if there's already a running task for this source
            running = False
            for task in self._crawler_tasks.values():
                if task.source == source and task.status == CrawlerTaskStatus.RUNNING:
                    running = True
                    break

            if running:
                logger.warning(f"Skipping {source}: already running")
                continue

            # Create task for this source
            self._task_counter += 1
            task_id = f"retry_{source}_{self._task_counter}_{int(time.time())}"

            task = CrawlerTask(
                task_id=task_id,
                source=source,
                mode="retry",
                count=len(urls),
                status=CrawlerTaskStatus.RUNNING,
                total=len(urls),
                started_at=time.time()
            )

            self._crawler_tasks[task_id] = task

            # Start retry in background with specific URLs
            params = {
                "source": source,
                "mode": "retry_urls",
                "urls": urls
            }
            reader_task = asyncio.create_task(self._run_crawler_subprocess(task, params))
            task._reader_task = reader_task

            task_ids.append(task_id)
            total_urls += len(urls)

            logger.info(f"Started retry task: {task_id} for {len(urls)} URLs from {source}")

        if not task_ids:
            return web.json_response({
                "error": "No tasks could be started (sources may already be running)",
                "count": 0
            }, status=409)

        return web.json_response({
            "task_ids": task_ids,
            "mode": "retry_selected",
            "count": total_urls,
            "status": "running"
        })

    # ==================== Resume API ====================

    async def resume_crawler(self, request: web.Request) -> web.Response:
        """
        POST /api/indexing/crawler/resume

        Resume a failed/stopped crawler task with the same parameters.

        Request body:
        {
            "task_id": "crawler_ltn_1_1234567890"
        }
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        task_id = body.get("task_id")
        if not task_id:
            return web.json_response({"error": "task_id is required"}, status=400)

        old_task = self._crawler_tasks.get(task_id)
        if not old_task:
            return web.json_response({"error": "Task not found"}, status=404)

        if old_task.status == CrawlerTaskStatus.RUNNING:
            return web.json_response({"error": "Task is still running"}, status=400)

        if not old_task.params:
            return web.json_response({"error": "No saved params for this task"}, status=400)

        # Check if same source already has a running task
        for task in self._crawler_tasks.values():
            if task.source == old_task.source and task.status == CrawlerTaskStatus.RUNNING:
                return web.json_response({
                    "error": f"Crawler for {old_task.source} is already running",
                    "task_id": task.task_id
                }, status=409)

        # Route based on mode
        if old_task.mode == "full_scan":
            params = old_task.params.copy()
            source = old_task.source

            # Resume from checkpoint
            if old_task.last_scanned_id is not None:
                params["start_id"] = old_task.last_scanned_id + 1
                logger.info(f"Resuming full scan from ID {old_task.last_scanned_id + 1}")
            if old_task.last_scanned_date:
                params["start_date"] = old_task.last_scanned_date
                logger.info(f"Resuming full scan from date {old_task.last_scanned_date}")

            self._task_counter += 1
            new_task_id = f"fullscan_{source}_{self._task_counter}_{int(time.time())}"

            new_task = CrawlerTask(
                task_id=new_task_id,
                source=source,
                mode="full_scan",
                count=0,
                status=CrawlerTaskStatus.RUNNING,
                started_at=time.time(),
                params=params,
                scan_start=params.get("start_id") or params.get("start_date"),
                scan_end=old_task.scan_end,
            )

            self._crawler_tasks[new_task_id] = new_task
            self._save_tasks()

            reader_task = asyncio.create_task(
                self._run_crawler_subprocess(new_task, params)
            )
            new_task._reader_task = reader_task

            logger.info(f"Resumed full scan task {task_id} as {new_task_id}")

            return web.json_response({
                "task_id": new_task_id,
                "resumed_from": task_id,
                "source": source,
                "mode": "full_scan",
                "status": new_task.status.value
            })
        else:
            # Regular crawler task
            params = old_task.params
            source = params.get("source", old_task.source)
            mode = params.get("mode", old_task.mode)
            count = params.get("count", old_task.count)

            self._task_counter += 1
            new_task_id = f"crawler_{source}_{self._task_counter}_{int(time.time())}"

            new_task = CrawlerTask(
                task_id=new_task_id,
                source=source,
                mode=mode,
                count=count,
                status=CrawlerTaskStatus.RUNNING,
                total=count,
                started_at=time.time(),
                params=params,
            )

            self._crawler_tasks[new_task_id] = new_task
            self._save_tasks()

            reader_task = asyncio.create_task(
                self._run_crawler_subprocess(new_task, params)
            )
            new_task._reader_task = reader_task

            logger.info(f"Resumed crawler task {task_id} as {new_task_id}")

            return web.json_response({
                "task_id": new_task_id,
                "resumed_from": task_id,
                "source": source,
                "mode": mode,
                "count": count,
                "status": new_task.status.value
            })

    # ==================== Auto-Resume ====================

    async def _auto_resume_task(self, old_task_id: str) -> Optional[str]:
        """
        Auto-resume a single zombie full_scan task from its checkpoint.

        Returns new task_id on success, None on failure.
        """
        old_task = self._crawler_tasks.get(old_task_id)
        if not old_task:
            logger.warning(f"Auto-resume: task {old_task_id} not found")
            return None

        if old_task.mode != 'full_scan':
            logger.warning(f"Auto-resume: task {old_task_id} is not full_scan (mode={old_task.mode})")
            return None

        if old_task.last_scanned_id is None and not old_task.last_scanned_date:
            logger.warning(f"Auto-resume: task {old_task_id} has no checkpoint")
            return None

        # Check if same source already has a running task
        for task in self._crawler_tasks.values():
            if task.source == old_task.source and task.status == CrawlerTaskStatus.RUNNING:
                logger.info(f"Auto-resume: skipping {old_task_id}, source {old_task.source} already running")
                return None

        # Build params from checkpoint
        params = old_task.params.copy() if old_task.params else {"source": old_task.source, "mode": "full_scan"}

        if old_task.last_scanned_id is not None:
            params["start_id"] = old_task.last_scanned_id + 1
            logger.info(f"Auto-resume {old_task_id}: from ID {old_task.last_scanned_id + 1}")
        if old_task.last_scanned_date:
            params["start_date"] = old_task.last_scanned_date
            logger.info(f"Auto-resume {old_task_id}: from date {old_task.last_scanned_date}")

        # Create new task
        self._task_counter += 1
        source = old_task.source
        new_task_id = f"fullscan_{source}_{self._task_counter}_{int(time.time())}"

        new_task = CrawlerTask(
            task_id=new_task_id,
            source=source,
            mode="full_scan",
            count=0,
            status=CrawlerTaskStatus.RUNNING,
            started_at=time.time(),
            params=params,
            scan_start=str(params.get("start_id") or params.get("start_date", "")),
            scan_end=old_task.scan_end,
        )

        self._crawler_tasks[new_task_id] = new_task
        self._save_tasks()

        reader_task = asyncio.create_task(self._run_crawler_subprocess(new_task, params))
        new_task._reader_task = reader_task

        logger.info(f"Auto-resumed zombie task {old_task_id} as {new_task_id}")
        return new_task_id

    async def schedule_auto_resume(self) -> None:
        """Auto-resume all zombie full_scan tasks collected during _load_tasks()."""
        if not self._pending_auto_resume:
            return

        logger.info(f"Auto-resume: {len(self._pending_auto_resume)} zombie full_scan task(s) to resume")

        results = await asyncio.gather(
            *[self._auto_resume_task(tid) for tid in self._pending_auto_resume],
            return_exceptions=True
        )
        for old_task_id, result in zip(self._pending_auto_resume, results):
            if isinstance(result, Exception):
                logger.warning(f"Auto-resume: failed to resume {old_task_id}: {result}")
            elif result:
                logger.info(f"Auto-resume: {old_task_id} -> {result}")
            else:
                logger.warning(f"Auto-resume: failed to resume {old_task_id}")

        self._pending_auto_resume.clear()

    # ==================== Full Scan APIs ====================

    async def start_full_scan(self, request: web.Request) -> web.Response:
        """
        POST /api/indexing/fullscan/start

        Start full scan for one or more sources.

        Request body:
        {
            "sources": ["udn", "ltn"],
            "start_id": 7800000,       // for sequential sources (optional, default from config)
            "end_id": null,            // for sequential sources (auto-detect via get_latest_id)
            "start_date": "2024-01-01", // for date-based sources
            "end_date": "2026-02-07"   // for date-based sources (default: today)
        }
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        sources_input = body.get("sources", [])

        # Determine which sources to run
        if sources_input == "all" or sources_input == ["all"]:
            sources = list(FULL_SCAN_CONFIG.keys())
        elif isinstance(sources_input, list):
            sources = [s for s in sources_input if s in FULL_SCAN_CONFIG]
        else:
            return web.json_response({"error": "Invalid sources parameter"}, status=400)

        if not sources:
            return web.json_response({"error": "No valid sources specified"}, status=400)

        # Check for already running tasks
        running_sources = []
        for task in self._crawler_tasks.values():
            if task.source in sources and task.status == CrawlerTaskStatus.RUNNING:
                running_sources.append(task.source)

        if running_sources:
            return web.json_response({
                "error": f"Sources already running: {running_sources}",
                "running": running_sources
            }, status=409)

        task_ids = []

        # Phase 1: Auto-detect end_ids for sequential sources in parallel
        detected_end_ids = {}
        if body.get("end_id") is None:
            detect_sources = [
                s for s in sources
                if FULL_SCAN_CONFIG[s]["type"] == "sequential"
            ]
            if detect_sources:
                detect_results = await asyncio.gather(
                    *[self._detect_latest_id(s) for s in detect_sources],
                    return_exceptions=True
                )
                for source, result in zip(detect_sources, detect_results):
                    if isinstance(result, Exception):
                        logger.warning(f"Failed to auto-detect end_id for {source}: {result}")
                    elif result is not None:
                        detected_end_ids[source] = result
                        logger.info(f"Auto-detected end_id for {source}: {result:,}")

        # Phase 2: Create tasks (fast, no network I/O)
        for source in sources:
            config = FULL_SCAN_CONFIG[source]

            # Build params for this source
            params = {"source": source, "mode": "full_scan"}

            if config["type"] == "sequential":
                # Use watermark to avoid re-scanning already-scanned IDs
                default_start = config.get("start_id")
                watermark = self._get_watermark(source)
                if watermark and watermark.get("last_scanned_id"):
                    wm_id = watermark["last_scanned_id"] + 1
                    if wm_id > default_start:
                        logger.info(f"Watermark override for {source}: {default_start:,} -> {wm_id:,}")
                        default_start = wm_id
                start_id = body.get("start_id") or default_start
                end_id = body.get("end_id") or detected_end_ids.get(source)

                # Fallback to config default_end_id if auto-detection failed
                if end_id is None and config.get("default_end_id"):
                    end_id = config["default_end_id"]
                    logger.info(f"Using default_end_id for {source}: {end_id:,}")

                if start_id is None or end_id is None:
                    logger.warning(f"Skipping {source}: start_id or end_id not available")
                    continue

                params["start_id"] = start_id
                params["end_id"] = end_id
                scan_start = str(start_id)
                scan_end = str(end_id)

            else:  # date_based
                # Use watermark to avoid re-scanning already-scanned dates
                default_start_date = "2024-01-01"
                watermark = self._get_watermark(source)
                if watermark and watermark.get("last_scanned_date"):
                    from datetime import datetime as dt, timedelta
                    wm_date = dt.strptime(watermark["last_scanned_date"], "%Y-%m-%d")
                    new_start = (wm_date + timedelta(days=1)).strftime("%Y-%m-%d")
                    if new_start > default_start_date:
                        logger.info(f"Watermark override for {source}: {default_start_date} -> {new_start}")
                        default_start_date = new_start
                params["start_date"] = body.get("start_date") or default_start_date
                params["end_date"] = body.get("end_date", time.strftime("%Y-%m-%d"))
                scan_start = params["start_date"]
                scan_end = params["end_date"]

            # Create task
            self._task_counter += 1
            task_id = f"fullscan_{source}_{self._task_counter}_{int(time.time())}"

            task = CrawlerTask(
                task_id=task_id,
                source=source,
                mode="full_scan",
                count=0,
                status=CrawlerTaskStatus.RUNNING,
                started_at=time.time(),
                params=params,
                scan_start=scan_start,
                scan_end=scan_end,
            )

            self._crawler_tasks[task_id] = task

            reader_task = asyncio.create_task(
                self._run_crawler_subprocess(task, params)
            )
            task._reader_task = reader_task

            task_ids.append(task_id)
            logger.info(f"Started full scan: {task_id} for {source} ({scan_start} -> {scan_end})")

        if task_ids:
            self._save_tasks()

        if not task_ids:
            return web.json_response({"error": "No tasks could be started"}, status=400)

        return web.json_response({
            "task_ids": task_ids,
            "sources": sources,
        })

    async def get_full_scan_status(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/fullscan/status

        Returns status of all full scan tasks and source info.
        """
        # Get full scan tasks
        scan_tasks = [
            self._task_to_dict(t)
            for t in self._crawler_tasks.values()
            if t.mode == "full_scan"
        ]

        # Get source info
        sources_info = {}
        for source, config in FULL_SCAN_CONFIG.items():
            sources_info[source] = {
                "name": config["name"],
                "type": config["type"],
                "start_id": config.get("start_id"),
            }

        return web.json_response({
            "tasks": scan_tasks,
            "sources": sources_info,
        })

    async def get_watermarks(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/watermarks

        Returns scan watermarks for all sources.
        """
        try:
            from crawler.core.crawled_registry import get_registry
            registry = get_registry()
            watermarks = registry.get_all_watermarks()
            return web.json_response({"watermarks": watermarks})
        except Exception as e:
            logger.warning(f"Failed to read watermarks: {e}")
            return web.json_response({"watermarks": {}, "error": str(e)})

    async def get_reference_points(self, request: web.Request) -> web.Response:
        """
        GET /api/indexing/reference-points

        Returns reference point validation results for all sources.
        Includes both configured reference points and auto-discovered points.
        """
        try:
            from crawler.core.crawled_registry import get_registry
            from crawler.core.settings import REFERENCE_POINTS

            registry = get_registry()
            result = {}

            for source_id in FULL_SCAN_CONFIG:
                configured_points = REFERENCE_POINTS.get(source_id, [])

                # Validate configured reference points
                validated = []
                if configured_points:
                    validated = registry.validate_reference_points(
                        source_id, configured_points
                    )

                # Auto-discover reference points from existing data
                discovered = registry.discover_reference_points(source_id)

                # Summary counts
                found = sum(1 for v in validated if v["status"] == "found")
                confirmed_404 = sum(1 for v in validated if v["status"] == "confirmed_404")
                not_scanned = sum(1 for v in validated if v["status"] == "not_scanned")

                result[source_id] = {
                    "name": FULL_SCAN_CONFIG[source_id].get("name", source_id),
                    "configured": validated,
                    "discovered": discovered,
                    "summary": {
                        "total": len(validated),
                        "found": found,
                        "confirmed_404": confirmed_404,
                        "not_scanned": not_scanned,
                    },
                    "discovered_months": len(discovered),
                }

            return web.json_response(result)
        except Exception as e:
            logger.error(f"Failed to get reference points: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _detect_latest_id(self, source: str) -> Optional[int]:
        """Auto-detect the latest article ID for a sequential source."""
        try:
            parser = await self._get_parser(source)
            if not parser:
                return None
            from crawler.core.engine import CrawlerEngine
            engine = CrawlerEngine(parser=parser, auto_save=False)
            engine.session = await engine._create_session()
            try:
                latest = await asyncio.wait_for(
                    parser.get_latest_id(session=engine.session),
                    timeout=30
                )
                return latest
            finally:
                await engine.close()
        except asyncio.TimeoutError:
            logger.warning(f"Timeout detecting latest ID for {source}")
            return None
        except Exception as e:
            logger.warning(f"Failed to detect latest ID for {source}: {e}")
            return None

    def _get_watermark(self, source: str) -> Optional[dict]:
        """Read scan watermark from registry."""
        try:
            from crawler.core.crawled_registry import get_registry
            registry = get_registry()
            return registry.get_scan_watermark(source)
        except Exception as e:
            logger.warning(f"Failed to read watermark for {source}: {e}")
            return None

    def _task_to_dict(self, task: CrawlerTask) -> Dict[str, Any]:
        """Convert CrawlerTask to dict for JSON response"""
        result = {
            "task_id": task.task_id,
            "source": task.source,
            "mode": task.mode,
            "count": task.count,
            "status": task.status.value,
            "progress": task.progress,
            "total": task.total,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "error": task.error,
            "early_stop_reason": task.early_stop_reason,
            "stats": task.stats,
            "params": task.params,
            "duration_seconds": (task.finished_at or time.time()) - task.started_at if task.started_at else 0,
            "pid": task._pid,
        }

        # Add full scan specific fields
        if task.mode == "full_scan":
            result["last_scanned_id"] = task.last_scanned_id
            result["last_scanned_date"] = task.last_scanned_date
            result["scan_start"] = task.scan_start
            result["scan_end"] = task.scan_end

        return result


# Singleton instance
_api_instance: Optional[IndexingDashboardAPI] = None


def get_api() -> IndexingDashboardAPI:
    """Get singleton API instance"""
    global _api_instance
    if _api_instance is None:
        _api_instance = IndexingDashboardAPI()
    return _api_instance


def setup_routes(app: web.Application) -> None:
    """Setup API routes on the application"""
    api = get_api()

    # Statistics
    app.router.add_get("/api/indexing/stats", api.get_stats)
    app.router.add_get("/api/indexing/sources", api.get_sources)

    # Monthly stats
    app.router.add_get("/api/indexing/stats/monthly/{source_id}", api.get_monthly_stats)

    # Crawler control
    app.router.add_post("/api/indexing/crawler/start", api.start_crawler)
    app.router.add_get("/api/indexing/crawler/status", api.get_crawler_status)
    app.router.add_get("/api/indexing/crawler/status/{task_id}", api.get_crawler_status)
    app.router.add_post("/api/indexing/crawler/stop", api.stop_crawler)
    app.router.add_post("/api/indexing/crawler/resume", api.resume_crawler)

    # Failed URLs / Errors
    app.router.add_get("/api/indexing/errors", api.get_errors)
    app.router.add_post("/api/indexing/errors/clear", api.clear_errors)
    app.router.add_post("/api/indexing/errors/retry", api.retry_errors)

    # WebSocket
    app.router.add_get("/api/indexing/ws", api.websocket_handler)

    # Full Scan
    app.router.add_post("/api/indexing/fullscan/start", api.start_full_scan)
    app.router.add_get("/api/indexing/fullscan/status", api.get_full_scan_status)
    app.router.add_get("/api/indexing/watermarks", api.get_watermarks)

    # Reference Points (coverage validation)
    app.router.add_get("/api/indexing/reference-points", api.get_reference_points)

    # Auto-resume zombie full_scan tasks on startup
    async def on_startup(app):
        await api.schedule_auto_resume()
    app.on_startup.append(on_startup)
