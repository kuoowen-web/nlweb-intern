# Crawler Dashboard Specification

> Indexing Dashboard 前端規格文件

---

## 概述

Indexing Dashboard 是一個單頁應用程式，用於管理 NLWeb 的爬蟲系統和索引管線。提供即時監控、任務管理、錯誤追蹤三大核心功能。

**存取路徑**: `/static/indexing-dashboard.html`
**API Server**: `http://localhost:8001`

---

## 架構

```
┌─────────────────────────────────────────────────────────────┐
│                    Indexing Dashboard                        │
├─────────────┬─────────────────┬─────────────────────────────┤
│ Statistics  │    Crawler      │           Errors            │
│    Tab      │      Tab        │            Tab              │
├─────────────┴─────────────────┴─────────────────────────────┤
│                     WebSocket Layer                          │
│              (Real-time task status updates)                 │
├─────────────────────────────────────────────────────────────┤
│                      REST API Layer                          │
│         /api/indexing/* endpoints on port 8001               │
└─────────────────────────────────────────────────────────────┘
```

---

## Tab 功能規格

### 1. Statistics Tab

顯示系統整體統計數據。

#### 統計卡片

| 欄位 | 資料來源 | 說明 |
|------|----------|------|
| Total Articles | `registry.total_articles` | Registry 中的文章總數 |
| Vectors | `qdrant.vectors_count` | Qdrant 中的向量總數 |
| Sources | `/api/indexing/sources` 回傳 | 可用爬蟲來源數 |
| Last Updated | `timestamp` | 最後更新時間 |

#### 來源分類統計

每個來源顯示：
- **來源名稱** 和 **文章數量**
- **Oldest**: 最老文章的發布日期
- **Newest**: 最新文章的發布日期

按數量降序排列。

```
┌─────────────────────────────────────┐
│  ltn                         5,000  │
│  Oldest: 2024-01-15  Newest: 2026-02-04 │
└─────────────────────────────────────┘
```

#### 自動刷新

- 每 30 秒自動刷新統計數據
- 手動刷新按鈕

---

### 2. Crawler Tab

爬蟲任務控制介面。

#### 控制項

| 控制項 | 類型 | 選項/範圍 | 說明 |
|--------|------|----------|------|
| Source | Select | 動態載入 | 選擇爬蟲來源 |
| Mode | Select | `auto`, `list_page`, `full_scan`, `sitemap` | 爬取模式 |
| Max Count | Number | 1-5000 | auto 最大爬取數量 |
| Stop After Skips | Number | 1-100 | auto 模式：連續幾個已爬取後停止 |
| Max Pages | Number | 1-100 | list_page 模式：最大頁數 |
| Chunk Size | Number | 0-50000 | 每個檔案的最大文章數（0=不限制）|
| Chunk by Month | Checkbox | - | 按文章發布月份分檔 |

#### 爬取模式

**Auto Mode (預設) - 定期更新**
- 從最新文章往回爬
- **連續遇到 N 個已爬取的文章後自動停止**（預設 10 個）
- 適用於：每日/每小時定期更新最新新聞
- 流程：
  ```
  latest_id → latest_id-1 → ... → 遇到10個重複 → 停止
  ```

**List Page Mode - 列表頁爬取**
- 從分類列表頁逐頁爬取
- 適用於：moea 等僅有列表頁的來源

**Full Scan Mode - 全量掃描**
- 掃描指定 ID/日期範圍內的每一個 ID
- Sequential sources: `start_id` / `end_id`
- Date-based sources: `start_date` / `end_date`（YYYY-MM 或 YYYY-MM-DD）
- Watermark 機制：記錄上次掃描進度，重啟時自動跳過已掃描範圍
- 適用於：大量 backfill

**Sitemap Mode - Sitemap 爬取**
- 從 sitemap index 取得所有文章 URL，命中率 100%
- 參數：`date_from`（YYYYMM）、`date_to`、`limit`
- 自動 dedup（跳過 registry 已有的 URL）
- 目前僅 UDN 支援（1,051 子 sitemap，~58 萬 URL）
- 適用於：UDN backfill（vs full_scan 僅 6% 命中率）

#### 輸出檔案切塊

支援兩種切塊方式（可同時使用）：

**Chunk Size（按數量）**
- 每 N 篇文章存成一個新檔案
- 檔名格式：`{source}_{timestamp}_part000.tsv`, `_part001.tsv`, ...
- 適用於：大量 backfill 時控制檔案大小

**Chunk by Month（按月份）**
- 根據文章的 `datePublished` 月份分檔
- 檔名格式：`{source}_2025-01.tsv`, `{source}_2025-02.tsv`, ...
- 適用於：按時間組織歷史資料

#### Task List

顯示爬蟲任務列表，按開始時間降序排列。

**Task Card 結構:**
```
┌──────────────────────────────────────────┐
│ [Source Name]  [Mode Badge]   [Status]   │
│ ─────────────────────────────────────────│
│ Progress Bar: ████████░░ 80%             │
│ 800 / 1000                    2m 30s     │
│ ─────────────────────────────────────────│
│ Success: 750  Failed: 30  Skipped: 20    │
│ ─────────────────────────────────────────│
│ [Stop Button] (only when running)        │
└──────────────────────────────────────────┘
```

**Task 狀態:**

| Status | 顏色 | 說明 |
|--------|------|------|
| `running` | 黃色 | 進行中 |
| `completed` | 綠色 | 已完成 |
| `failed` | 紅色 | 失敗 |
| `stopping` | 黃色 | 停止中 |
| `early_stopped` | 橘色 | 提前停止（連續 N 個 not_found 或已爬過）|

#### Full Scan Tab（完整 ID 掃描）

Dashboard 的 Full Scan tab 支援對多個來源同時啟動完整 ID 範圍掃描。

**控制項:**

| 控制項 | 類型 | 說明 |
|--------|------|------|
| Sources | Multi-select | 選擇要掃描的來源（含 ID 範圍資訊）|
| Start Date | Text (YYYY-MM-DD) | 日期型來源的起始日期（可選）|
| End Date | Text (YYYY-MM-DD) | 日期型來源的結束日期（可選）|

**Full Scan 流程:**

```
POST /api/indexing/fullscan/start
  → 為每個 source 建立一個 task
  → Sequential: 自動偵測 end_id (parser.get_latest_id())
  → Date-based: 使用 start_date/end_date（預設 2024-01-01 ~ today）
  → 掃描每一個 ID，不做 early-stop
  → AutoThrottle: 根據伺服器回應速度動態調整 delay（EWMA 平滑）
  → 唯一停止條件: BLOCKED_CONSECUTIVE_LIMIT (5) 或到達 end
```

**Full Scan Task Card 結構:**
```
┌──────────────────────────────────────────────┐
│ UDN [Full Scan]                   [Running]  │
│ ─────────────────────────────────────────────│
│ ████████████░░░░░░░░ 62%                     │
│ ID: 8,870,000 / 7,800,000 → 9,313,000       │
│ ─────────────────────────────────────────────│
│ Found: 52,300   Skipped: 8,200               │
│ 404: 880K       Failed: 12                   │
│ Speed: 2.5 req/s                             │
│ Latency: 0.34s   Delay: 0.85s               │
│ ─────────────────────────────────────────────│
│ [Stop Button]                                │
└──────────────────────────────────────────────┘
```

**AutoThrottle 即時指標**：
- `avg_latency`: 最近 50 次請求的平均回應時間（秒）
- `current_delay`: AutoThrottle 計算的當前延遲值（秒）
- 這些值由 engine `_report_progress()` 自動注入 stats，Dashboard 可直接顯示

#### 進程隔離（Subprocess Per Crawler）

每個爬蟲任務在獨立的 Python subprocess 中執行，實現真正的 GIL 隔離。

**架構**：
```
Dashboard (parent process)
  ├── asyncio.create_subprocess_exec("python", "-m", "crawler.subprocess_runner", ...)
  │     └── Crawler A (own process, own event loop, own GIL)
  ├── asyncio.create_subprocess_exec(...)
  │     └── Crawler B (own process, own event loop, own GIL)
  └── reads stdout (JSON lines) for progress, checks process exit for completion
```

**IPC Protocol**: Subprocess stdout 輸出 JSON lines
```jsonl
{"type": "progress", "stats": {"total": 1000, "success": 50, ...}}
{"type": "completed", "stats": {"total": 1000, "success": 146, ...}}
{"type": "error", "error": "Unknown mode: xyz"}
```

**Stop Mechanism**:
1. Dashboard 建立 signal file: `data/crawler/signals/.stop_{task_id}`
2. Engine 在 `_report_progress()` 中檢查 signal file
3. 偵測到 → `raise asyncio.CancelledError` → graceful shutdown
4. 若 10 秒內未結束，parent 呼叫 `proc.terminate()`

> **注意**：`_evaluate_batch_results()` 使用 `isinstance(result, BaseException)`（非 `Exception`）判斷例外。Python 3.9+ 的 `CancelledError` 繼承自 `BaseException`，若只用 `Exception` 會漏接，導致 tuple unpack 失敗。

**Key files**:
- `crawler/subprocess_runner.py` — subprocess entry point
- `indexing/dashboard_api.py` — `_run_crawler_subprocess()` launches & monitors

**SQLite Concurrency**: CrawledRegistry 使用 WAL mode，每個 subprocess 有獨立 connection，安全並行寫入。

#### Task 持久化

任務狀態持久化到 `data/crawler/crawler_tasks.json`，支援 server 重啟後恢復。

- **Save throttling**: 最多每 5 秒存檔一次，terminal 狀態（completed/failed/early_stopped）立即存
- **Zombie detection**: Server 重啟時，仍在 running/stopping 狀態的任務自動標記為 failed
- **scan_start/scan_end**: 由 `start_crawler` 和 `start_full_scan` 初始化，progress handler 可從 engine stats backfill 缺失值

#### Task Resume（Checkpoint-based）

失敗或停止的任務可透過 Resume 從 checkpoint 繼續。

**Full Scan Resume 邏輯**：
- **Sequential sources**: 從 `last_scanned_id + 1` 繼續（更新 params.start_id）
- **Date-based sources**: 從 `last_scanned_date` 繼續（更新 params.start_date）
- `crawled_registry` 自動跳過已爬取的文章
- Checkpoint 在每個 batch 完成後更新，確保 crash 不會跳過 ID

**範例**：
```
原始任務：start_id=7,800,000, end_id=9,313,000
         → 掃描到 8,500,000 時中斷 (last_scanned_id=8,500,000)

Resume：  start_id=8,500,001, end_id=9,313,000
         → 從 checkpoint 繼續
```

#### stderr 處理（2026-02-12 更新）

Subprocess 的 stderr 改為 **file redirect**（非 asyncio.PIPE），避免 event loop starvation：

```python
stderr_log_path = log_dir / f"{task.task_id}.stderr.log"
stderr_log_file = open(stderr_log_path, "w", encoding="utf-8")
proc = await asyncio.create_subprocess_exec(
    ...,
    stdout=asyncio.subprocess.PIPE,   # JSON protocol, low-frequency
    stderr=stderr_log_file,            # file redirect, bypasses event loop
)
```

**原因**：6 個 subprocess 同時運行時，12 concurrent pipe readers（6 stdout + 6 stderr）搶佔 Windows ProactorEventLoop，HTTP handlers 永遠排不到。stderr 改為 file redirect 後完全不經 event loop，Dashboard 穩定運行 4+ 小時。

**stderr log 位置**：`data/crawler/signals/{task_id}.stderr.log`

> **規則**：高頻輸出（logging）用 file redirect，只有低頻結構化輸出（JSON progress）才用 asyncio.PIPE。

#### Force Kill & Pipe Cleanup（2026-02-12）

Windows 上 `proc.kill()` 不會自動關閉 pipes，`async for line in proc.stdout` 可能永遠 hang。修復：

```python
async def _force_kill_after(proc, timeout=10):
    await asyncio.sleep(timeout)
    if proc.returncode is None:
        proc.kill()
        # Cancel stdout reader to prevent hang
        if hasattr(task, '_reader_task') and task._reader_task:
            task._reader_task.cancel()
```

#### Auto-Restart for Early-Stopped Sources（2026-02-12）

某些 source（如 MOEA）因 rate limit 觸發 `BLOCKED_CONSECUTIVE_LIMIT` 而 early_stop，等待一段時間後可恢復。

**配置**：
```python
AUTO_RESTART_DELAY = {
    "moea": 900,  # 15 minutes
}
```

**流程**：
1. `_run_crawler_subprocess` 偵測到 `early_stopped`
2. 查 `AUTO_RESTART_DELAY` 是否有該 source
3. 有 → `asyncio.create_task(_delayed_restart(task_id, delay))`
4. `_delayed_restart()`: sleep → 檢查無同 source running → `_auto_resume_task()`

**安全機制**：
- 若使用者已手動重啟，delay 期間偵測到同 source running → 跳過
- 複用既有 `_auto_resume_task()` 邏輯（從 checkpoint 建新 task）

#### 404 Skip 加速（2026-02-10）

Full Scan 啟動時載入三層 skip 資料，避免重複 HTTP request：

1. **Watermark skip**：`id <= watermark` 的 ID 全部跳過（上一輪已掃過）
   - Sequential: `current_id <= last_scanned_id`（O(1) int 比較）
   - Date-based: `current_day <= last_scanned_date`（整天跳過）
2. **Known 404 skip**：article_id 在 `not_found_articles` 表中（`Set[int]` in-memory lookup）
3. **Crawled skip**：URL 已在 `crawled_articles` 表中（既有邏輯）

**持久化**：404 記錄隨 batch checkpoint 一起 flush（`flush_not_found()`），非逐筆 commit。最壞丟失一個 batch 的 404 記錄（下次重掃即恢復）。

**效果**：重啟後重掃已覆蓋範圍，watermark 以下的 ID 全部秒跳過，無 HTTP request。

---

### 3. Coverage Tab

Full Scan 完成後的覆蓋率驗證。用預先定義的參考點（已知文章 ID）比對 registry，確認是否有遺漏。

#### 參考點驗證表

| 欄位 | 說明 |
|------|------|
| Date | 參考點所屬月份 (YYYY-MM) |
| Article ID | 已知存在的文章 ID |
| Note | 參考點描述 |
| Status | `found` / `confirmed_404` / `not_scanned` |
| Published | 實際發布日期（僅 found 狀態有值） |

**狀態標示**：
- 綠底 `found`：已成功爬取
- 黃底 `confirmed_404`：文章不存在（參考點需更換）
- 紅底 `not_scanned`：尚未掃描到該區間

#### Auto-discover 月份覆蓋圖

從 `crawled_articles` 自動取得每月一篇代表性文章，以色塊視覺化顯示哪些月份有資料。適用於 sequential sources（UDN/LTN/einfo）無法預設參考點的情況。

#### 資料來源

- 設定檔：`settings.py` → `REFERENCE_POINTS`
- API：`GET /api/indexing/reference-points`
- 驗證邏輯：`crawled_registry.py` → `validate_reference_points()` / `discover_reference_points()`

---

### 4. Errors Tab

錯誤追蹤與重試介面。

#### 過濾器

| 過濾器 | 類型 | 說明 |
|--------|------|------|
| Source | Select | 按來源過濾 |
| Error Types | Dropdown (可展開) | 多選錯誤類型，顯示各類型數量 |

#### Error Types Dropdown

- 點擊展開下拉選單
- 每個類型有獨立 checkbox
- 顯示各類型的錯誤數量
- 選擇/取消選擇後即時過濾下方表格
- Dropdown label 顯示: "All Types" / "N Types Selected" / "None Selected"

#### 錯誤類型

| 類型 | Badge 顏色 | 說明 |
|------|------------|------|
| `blocked` | 紅色 | 被封鎖 (403/429) |
| `parse_error` | 黃色 | 解析失敗 |
| `parse_exception` | 黃色 | 解析異常 |
| `fetch_error` | 藍色 | 抓取失敗 |
| `save_error` | 粉色 | 儲存失敗 |

#### 過濾邏輯

**Client-side Filtering:**
1. 載入時從 API 取得所有錯誤 (`loadAllErrors`)
2. 儲存到 `allErrors` 陣列
3. 過濾器變更時在前端即時過濾 (`applyFilters`)
4. 過濾結果存入 `filteredErrors` 陣列
5. 顯示過濾後數量 "N URLs"

#### 錯誤統計卡片

- **Total Failed**: 失敗 URL 總數 (全部，非過濾後)
- **By Source**: 按來源統計
- **By Error Type**: 按錯誤類型統計

#### 錯誤表格

| 欄位 | 說明 |
|------|------|
| URL | 失敗的 URL (截斷顯示，hover 顯示完整) |
| Source | 來源 ID |
| Error Type | 錯誤類型 Badge |
| Message | 錯誤訊息 |
| Retries | 重試次數 |
| Failed At | 失敗時間 |

#### 操作按鈕

- **Retry All**: 重試所有符合過濾條件的 URL（不受前端載入限制）
  - 使用 `retry_all: true` 模式呼叫後端 API
  - 後端會處理所有符合 source + error_types 條件的 URL
  - 顯示確認對話框，說明將處理的總數
  - 自動切換到 Crawler tab 查看進度
- **Clear All**: 清除符合過濾條件的錯誤記錄 (需確認)
  - 使用與 Retry All 相同的 `getSelectedErrorTypes()` 過濾邏輯
  - 傳送 `error_types` 到後端 `clear_failed(error_types=...)`
  - 確認對話框顯示過濾條件（source + error types）

#### 前端載入限制

- 前端最多載入 1000 筆錯誤用於顯示
- 若總數超過 1000，會顯示 "Showing X of Y URLs"
- "Retry All" 按鈕不受此限制，會處理後端所有符合條件的錯誤

---

## API 端點

### Statistics

#### GET `/api/indexing/stats`

取得系統統計數據。

**Response:**
```json
{
  "timestamp": 1706000000,
  "registry": {
    "total_articles": 15000,
    "by_source": {
      "ltn": 5000,
      "udn": 4000,
      "cna": 3000,
      "einfo": 2000,
      "esg_businesstoday": 500,
      "moea": 500
    },
    "date_ranges": {
      "ltn": {
        "oldest": "2024-01-15T08:30:00",
        "newest": "2026-02-04T14:20:00",
        "count": 5000
      },
      "udn": {
        "oldest": "2024-02-01T10:00:00",
        "newest": "2026-02-04T13:45:00",
        "count": 4000
      }
    }
  },
  "qdrant": {
    "vectors_count": 45000
  }
}
```

#### GET `/api/indexing/sources`

取得可用爬蟲來源列表。

**Response:**
```json
{
  "count": 6,
  "sources": [
    { "id": "ltn", "name": "自由時報" },
    { "id": "udn", "name": "聯合報" },
    { "id": "cna", "name": "中央社" },
    { "id": "moea", "name": "經濟部" },
    { "id": "einfo", "name": "環境資訊中心" },
    { "id": "esg_businesstoday", "name": "今周刊 ESG" },
    { "id": "chinatimes", "name": "中國時報" }
  ]
}
```

> **注意**：moea 支援 `auto`、`list_page`、`full_scan` 模式。Full scan 使用 sequential ID（news_id）。

---

### Crawler Control

#### POST `/api/indexing/crawler/start`

啟動爬蟲任務。

**Request (Auto Mode):**
```json
{
  "source": "ltn",
  "mode": "auto",
  "count": 100
}
```

**Request (List Page Mode):**
```json
{
  "source": "moea",
  "mode": "list_page",
  "max_pages": 10
}
```

**Response:**
```json
{
  "success": true,
  "task_id": "task-uuid-1234",
  "message": "Crawler started"
}
```

#### POST `/api/indexing/crawler/stop`

停止爬蟲任務。

**Request:**
```json
{
  "task_id": "task-uuid-1234"
}
```

**行為**：
1. 建立 signal file `data/crawler/signals/.stop_{task_id}`
2. Subprocess engine 偵測後 graceful shutdown
3. 若 10 秒內未結束，parent 呼叫 `proc.terminate()`

**Response:**
```json
{
  "success": true,
  "message": "Stop signal sent"
}
```

#### GET `/api/indexing/crawler/status`

取得所有任務狀態。

**Response:**
```json
{
  "tasks": [
    {
      "task_id": "task-uuid-1234",
      "source": "ltn",
      "mode": "auto",
      "status": "running",
      "progress": 50,
      "total": 100,
      "started_at": 1706000000,
      "duration_seconds": 120,
      "stats": {
        "success": 45,
        "failed": 3,
        "skipped": 2,
        "avg_latency": 0.342,
        "current_delay": 0.85
      },
      "error": null
    }
  ]
}
```

#### POST `/api/indexing/crawler/resume`

從中斷點恢復失敗/停止的任務（True Resume）。

**Request:**
```json
{
  "task_id": "backfill_ltn_87_1770379631"
}
```

**行為**：
- Full Scan 任務：從 `last_scanned_id + 1` 或 `last_scanned_date` 繼續
- 一般爬蟲任務：使用原始參數重新啟動

**Response:**
```json
{
  "success": true,
  "new_task_id": "backfill_ltn_88_1770380000",
  "resumed_from": "backfill_ltn_87_1770379631"
}
```

**錯誤情境**：
| HTTP Status | 原因 |
|-------------|------|
| 400 | task_id 缺失、任務仍在運行、無 params |
| 404 | 找不到任務 |
| 409 | 同來源已有執行中任務 |

---

### Full Scan

#### POST `/api/indexing/fullscan/start`

啟動多來源 full scan。

**Request:**
```json
{
  "sources": ["ltn", "udn", "cna", "einfo", "esg_businesstoday", "chinatimes"],
  "start_date": "2024-01-01",
  "end_date": "2026-02-07"
}
```

**Request (指定 ID 範圍 — Sequential sources):**
```json
{
  "sources": ["moea"],
  "start_id": 100000,
  "end_id": 122000
}
```

> **重要**：`start_id`、`end_id`、`start_date`、`end_date` 必須放在 JSON body 的**最外層**。`dashboard_api.py` 使用 `body.get("start_id")` 直接讀取，**不支援** nested `overrides` 欄位。

**Response:**
```json
{
  "task_ids": [
    "fullscan_ltn_87_1770379631",
    "fullscan_udn_88_1770379631",
    "fullscan_cna_89_1770379631"
  ],
  "sources": ["ltn", "udn", "cna"]
}
```

**行為**：
- Sequential sources: 若有 `start_id` 則使用，否則使用 `FULL_SCAN_CONFIG.default_start_id`；`end_id` 同理，否則自動偵測 (`parser.get_latest_id()`)
- Date-based sources: 使用 `start_date`/`end_date`（預設 2024-01-01 ~ today）
- Watermark 機制：若無明確指定 `start_id`/`start_date`，系統會從 watermark 自動續跑

#### GET `/api/indexing/fullscan/status`

取得所有 full scan 任務狀態。

**Response:**
```json
{
  "tasks": [
    {
      "task_id": "fullscan_udn_88_1770379631",
      "source": "udn",
      "mode": "full_scan",
      "status": "running",
      "progress": 870000,
      "total": 1513000,
      "last_scanned_id": 8670000,
      "scan_start": "7800000",
      "scan_end": "9313000",
      "stats": {
        "success": 52300,
        "failed": 12,
        "skipped": 8200
      },
      "duration_seconds": 86400
    }
  ]
}
```

---

### Coverage / Reference Points

#### GET `/api/indexing/reference-points`

取得所有來源的參考點驗證結果。

**Response:**
```json
{
  "cna": {
    "name": "Central News Agency",
    "configured": [
      {
        "id": 202401020010,
        "date": "2024-01",
        "note": "2024-01-02 第10篇",
        "status": "found",
        "url": "https://www.cna.com.tw/news/aall/202401020010.aspx",
        "date_published": "2024-01-02T..."
      }
    ],
    "discovered": [
      {"url": "...", "date": "2024-01", "date_published": "2024-01-02"}
    ],
    "summary": {"total": 12, "found": 8, "confirmed_404": 0, "not_scanned": 4},
    "discovered_months": 26
  }
}
```

---

### Error Management

#### GET `/api/indexing/errors`

取得錯誤列表。

**Query Parameters:**
| 參數 | 類型 | 說明 |
|------|------|------|
| `source` | string | 過濾來源 (可選) |
| `error_types` | string | 逗號分隔的錯誤類型 (可選) |
| `limit` | number | 回傳數量限制 (預設 1000) |

**Response:**
```json
{
  "errors": [
    {
      "url": "https://example.com/article/123",
      "source_id": "ltn",
      "error_type": "blocked",
      "error_message": "403 Forbidden",
      "retry_count": 2,
      "failed_at": "2025-01-23T10:30:00Z"
    }
  ],
  "stats": {
    "total": 150,
    "by_source": {
      "ltn": 50,
      "udn": 100
    },
    "by_error_type": {
      "blocked": 80,
      "parse_error": 70
    }
  }
}
```

#### POST `/api/indexing/errors/retry`

重試失敗的 URL。支援三種模式：

**Mode 1 - Retry All (推薦)**
```json
{
  "retry_all": true,
  "source": "ltn",                    // optional
  "error_types": ["blocked", "parse_error"]  // optional
}
```

**Mode 2 - By Source**
```json
{
  "source": "ltn",
  "max_retries": 3,
  "limit": 50
}
```

**Mode 3 - Specific URLs**
```json
{
  "urls": ["https://example.com/1", "https://example.com/2"],
  "sources": {
    "ltn": ["https://example.com/1"],
    "udn": ["https://example.com/2"]
  }
}
```

**Response:**
```json
{
  "task_ids": ["retry-task-uuid-1", "retry-task-uuid-2"],
  "mode": "retry_selected",
  "count": 738,
  "status": "running"
}
```

#### Retry 保守策略

當 `run_retry()` 偵測到來源有 `error_type == "blocked"` 的失敗記錄時，自動切換為保守模式：

| 設定 | 正常模式 | 保守模式 |
|------|----------|----------|
| Concurrent | 來源預設值 | **1**（單線程）|
| Delay | 來源預設範圍 | **加倍** |
| 批次間隔 | 無 | **額外 5 秒** |

這避免了 retry 再次觸發 403/429 封鎖。

#### Retry 三層退避機制

系統使用三層退避策略處理爬取失敗：

| 層級 | 名稱 | 時機 | 策略 |
|------|------|------|------|
| Layer 1 | Per-Request Retry | 單個請求失敗時立即重試 | 指數退避（`settings.RETRY_DELAY` × 2^n），最多 `settings.MAX_RETRIES` 次 |
| Layer 2 | Task-Level Retry | 任務完成後，對失敗 URL 再次嘗試 | `run_retry()` 配合保守模式，batch 10 個 URL |
| Layer 3 | Manual Retry | 使用者透過 Dashboard Errors Tab 發起 | 可選 source/error_types 過濾，自動偵測 blocked 啟用保守模式 |

#### POST `/api/indexing/errors/clear`

清除錯誤記錄。

**Request (Clear All):**
```json
{}
```

**Request (Clear by Source):**
```json
{
  "source": "ltn"
}
```

**Request (Clear by Error Types):**
```json
{
  "source": "ltn",
  "error_types": ["blocked", "parse_error"]
}
```

**Request (Clear specific URLs):**
```json
{
  "urls": ["https://example.com/1", "https://example.com/2"]
}
```

**Response:**
```json
{
  "success": true,
  "cleared": 50
}
```

---

### WebSocket

#### WS `/api/indexing/ws`

即時任務狀態更新。

**連線後初始訊息:**
```json
{
  "type": "init",
  "tasks": [/* 所有任務列表 */]
}
```

**狀態更新訊息:**
```json
{
  "type": "status_update",
  "task": {
    "task_id": "task-uuid",
    "source": "ltn",
    "status": "running",
    "progress": 51,
    "total": 100,
    "stats": { "success": 46, "failed": 3, "skipped": 2 },
    "duration_seconds": 125
  }
}
```

**重連機制:**
- 最多嘗試 5 次
- 間隔遞增: 2s, 4s, 6s, 8s, 10s

**效能優化（2026-02）:**

- **前端 debounce**: WebSocket `status_update` 觸發的 `loadFullScanStatus()` 有 3 秒 debounce，避免高頻 WS 更新導致 HTTP 洪水
- **後端 save throttle**: `_save_tasks()` 最多每 5 秒執行一次（terminal 狀態例外，立即存檔）
- 批次 backfill 時每秒可能有數十次 WS 更新，不 debounce 會嚴重影響 UI 效能

---

## UI/UX 規格

### 色彩系統

| 用途 | 顏色代碼 |
|------|----------|
| Primary (深藍) | `#1e3a5f` |
| Primary Hover | `#2d5a7b` |
| Success (綠) | `#10b981` / `#065f46` |
| Warning (黃) | `#fef3c7` / `#92400e` |
| Error (紅) | `#ef4444` / `#991b1b` |
| Background | `linear-gradient(135deg, #1e3a5f, #2d5a7b)` |

### 連線狀態指示

| 狀態 | 顯示 |
|------|------|
| Connected | 綠色圓點 + 脈動動畫 + "Connected" |
| Disconnected | 紅色圓點 + "Disconnected" |

### 響應式設計

- Stats Grid: `repeat(auto-fit, minmax(200px, 1fr))`
- Source List: `repeat(auto-fit, minmax(150px, 1fr))`
- 控制項支援 wrap

---

## 錯誤處理

### 全域錯誤 Banner

- 位於 header 下方
- 紅色背景
- 顯示 API 錯誤訊息
- API 成功時自動隱藏

### 連線中斷

- 狀態指示器變紅
- 自動重試連線 (最多 5 次)
- 重試間隔遞增

---

## 未來擴充預留

1. **Indexing Pipeline Tab** - 索引管線監控
2. **Logs Viewer** - 即時 log 顯示
3. **Scheduler** - 排程爬取設定
4. **Source Configuration** - 來源參數調整
5. **Performance Metrics** - 效能指標圖表

---

*更新：2026-02-12（scan_start/scan_end 初始化修復、MOEA rate limiting 調整、Task cleanup 最佳化）*
