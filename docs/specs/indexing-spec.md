# M0 Indexing Module 規格文件

> **⚠️ 注意**：系統已於 2026-02 遷移至 PostgreSQL（pgvector + pg_bigm），Qdrant 路徑已於 2026-06 **徹底廢除**（retrieval/indexing/conversation storage 三層 + qdrant_uploader.py + qdrant_profile.py 全部移除）。Crawler 章節和演算法設計仍有效；下方殘留的 Qdrant 術語章節（The Map / Qdrant payload / Qdrant Collection）僅為歷史記錄，對應程式碼已不存在。
>
> **儲存 / Embedding 權威說明**：見下方「**儲存與 Embedding：單一現役路徑（PostgreSQL；Qdrant 路徑已廢除 2026-06）**」章節 — 現役路徑用 **Qwen3-Embedding-4B INT8（1024d）+ PostgreSQL**；Qdrant 路徑（bge-m3 / OpenAI）已於 2026-06 廢除，僅留歷史記錄。

## 概述

M0 Indexing Module 負責新聞文章的索引化處理，將原始 TSV 資料轉換為可搜尋的 chunks 並儲存。

### 核心設計理念

- **長度優先分塊**：170 字/chunk，在句號邊界切分（POC 驗證最佳參數）
- **儲存**：PostgreSQL（pgvector + pg_bigm）— 已從 Qdrant 遷移（2026-02）
- **斷點續傳**：大量資料處理時支援中斷恢復

> **⚠️ .indexing_done 注意**：~~舊 `.indexing_done` 是 Qdrant 時代殘留~~ → 已完成（2026-03）。現使用 `pg_batch.py` + `.pg_indexing_done` 追蹤 PG indexing 進度，checkpoint 為 `<tsv>.pg_checkpoint.json`。舊檔案保留但不再使用。

> **Cloud Embedding（2026-03，已完成）**：全量 indexing 由 GCP L4 VM 執行完畢。`cloud_embed.py` 做 TSV → chunking → Qwen3-4B embedding → .jsonl+.npy 輸出，`bulk_load.py` 灌入 VPS PG。最終結果：1,725,488 articles / 5,271,712 chunks。教訓：超大 TSV 需先 `split -l 20000` 拆分（bitsandbytes INT8 有 VRAM leak）；bulk load 用 `np.load(mmap_mode='r')` 避免 RAM spike；已處理的結果檔要即時清理避免 disk 滿。GCP 資源已清理（VM + bucket 已刪除）。

### 架構流程

```
TSV File → Ingestion Engine → Quality Gate → Chunking Engine → Dual Storage
              ↓                    ↓              ↓                ↓
             CDM              Pass/Fail     List[Chunk]      Map + Vault
                                  ↓
                            Buffer (logged)
```

---

## Crawler 系統（TSV 資料來源）

Crawler 系統負責從各新聞網站爬取文章，輸出符合 Indexing Module 格式的 TSV 檔案。

### 架構概述

```
News Sites → Parser → Schema.org NewsArticle → TSV Output
    ↓           ↓              ↓                   ↓
  HTML      Site-specific   Standardized      url<TAB>JSON-LD
            Extraction      Format
```

### 支援的新聞來源

| Parser | 來源 | ID 類型 | 掃描方法 | Session 類型 | 命中率 | 月均文章（外部驗證） |
|--------|------|---------|----------|--------------|--------|----------------------|
| `ltn` | 自由時報 | Sequential ID | `full_scan` | AIOHTTP | ~80% | **~27,000** |
| `udn` | 聯合報 | Sequential ID | `sitemap` (推薦) / `full_scan` | AIOHTTP | ~43% (full_scan) / 100% (sitemap) | **~28,000** |
| `cna` | 中央社 | Date-based ID | `full_scan` | CURL_CFFI | ~32% | **~5,700+** (suffix 最高 5004) |
| `moea` | 經濟部 | Sequential ID | `full_scan` | CURL_CFFI | TBD | ~30-50 |
| `einfo` | 環境資訊中心 | Sequential ID | `full_scan` | CURL_CFFI | ~6% | ~20-30 |
| `esg_businesstoday` | 今周刊 ESG | Date-based ID | `full_scan` | CURL_CFFI | ~2% | **~100-150** (已完成) |
| `chinatimes` | 中國時報 | Date-based ID | `full_scan` | CURL_CFFI | TBD | **~10,510** (月產量已驗證) |

> **外部驗證方法**（2026-02-11 更新）：CNA probe suffix 1-6000/天（最高 5004）；UDN/LTN 從 article ID→發布日期映射推算；Chinatimes 列表頁翻頁調查；ESG BT 直接 probe。詳見下方「外部驗證數據」及 `docs/crawler-discussion.md` 調查結果。

#### 掃描方法選擇理由（2026-02 確認）

| Source | 方法 | 理由 |
|--------|------|------|
| LTN | `full_scan` | Sitemap 只有最新 1000 篇。Sequential ID，完整掃描 start_id → end_id |
| UDN | `sitemap` (推薦) | Sitemap 可用 (343 子 sitemap, ~99 萬 URL)。Full scan 94% 空洞，效率極低 |
| CNA | `full_scan` | 無 sitemap。Date-based ID (YYYYMMDDXXXX)，逐日掃描所有 suffix |
| einfo | `full_scan` | 無 sitemap。Sequential ID，但極慢（5-10s/req, concurrent=1）|
| ESG BT | `full_scan` | Sitemap 自 2021 年停止更新。Date-based ID，逐日掃描 |
| Chinatimes | `full_scan` | Date-based ID (14 位，suffix_digits=6)。260402 不是萬用路徑——每篇文章只有其正確 category code 能存取。Top 40 categories 覆蓋 95.6%。max_candidate_urls=39 |
| MOEA | `full_scan` | Sequential ID (news_id)。直接 URL 存取，無需 ViewState。Soft-404 由 parser 處理 |

### Backfill 目標範圍

**目標期間**：2024-01-01 起至今。2024 年以前的資料不在收錄範圍。

- Sequential sources (UDN, LTN, einfo)：需設定 `start_id` 對應 2024-01 的 ID
- Date-based sources (CNA, Chinatimes, ESG BT)：`start_date=2024-01-01`

### 外部驗證數據（2026-02-11 實測）

透過直接 HTTP probe（非依賴已爬資料）獨立驗證各來源月產量：

| Source | 驗證方法 | 月增 ID 數 | Hit Rate | 預估月產量 | 驗證細節 |
|--------|---------|-----------|----------|-----------|---------|
| UDN | ID→日期映射 | ~65,000 | ~43% | ~28,000 | ID 8M=2024-05, 9.2M=2025-12, 9.3M=2026-01 |
| LTN | ID→日期映射 | ~33,000 | ~80% | ~27,000 | ID 3.5M=2021-04, 5M=2025-04, 5.3M=2026-01 |
| CNA | probe suffix 1-6000 | 6000/天 | ~32% | ~5,700+ | suffix 最高 5004（早安世界系列），max_suffix=6000 |
| ESG BT | 重新掃描中 | ~600/天 | ~2.4% | ~35 | ID 獨立，miss_limit=150，suffix 11-81 |
| einfo | Geo-blocked | N/A | ~6% | ~48 | 需台灣 IP，binary search high=270K |
| Chinatimes | 列表頁調查 | ~6000/天 | TBD | **~10,510** | suffix 59-5121，max_suffix=6000，candidate=6 |
| MOEA | Sequential ID | N/A | TBD | ~30-50 | news_id sequential，首次 full scan |

### Full Scan Configuration

engine.py 中的 `FULL_SCAN_CONFIG` 定義各來源的掃描參數：

```python
FULL_SCAN_CONFIG = {
    "udn":  {"type": "sequential", "default_start_id": 7_800_000},
    "ltn":  {"type": "sequential", "default_start_id": 4_550_000},
    "einfo": {"type": "sequential", "default_start_id": 230_000},
    "cna":  {"type": "date_based", "max_suffix": 6000},
    "esg_businesstoday": {"type": "date_based", "max_suffix": 600, "date_scan_miss_limit": 150},
    "chinatimes": {"type": "date_based", "max_suffix": 6000, "suffix_digits": 6, "date_scan_miss_limit": 700},
    "moea": {"type": "sequential", "default_start_id": 110_000},
}
```

- **Sequential sources**: 需指定 `start_id` 和 `end_id`，掃描每一個 ID
- **Date-based sources**: 使用 `YYYYMMDDXXXX` 格式，逐日嘗試所有 suffix

### 覆蓋率參考點（Reference Points）

Full Scan 完成後用於驗證是否有遺漏文章的檢查點機制。

**設定檔**: `settings.py` 的 `REFERENCE_POINTS`

**原理**：預先定義各來源在特定日期的已知文章 ID，Full Scan 後比對 registry 確認是否有爬到。

```python
# Date-based sources: article_id = YYYYMMDD * multiplier + suffix
# CNA/ESG_BT: 4位 suffix (multiplier=10000)
# Chinatimes: 6位 suffix (multiplier=1000000)
REFERENCE_POINTS = {
    "cna": [
        {"id": 202401020010, "date": "2024-01", "note": "2024-01-02 第10篇"},
        {"id": 202403040010, "date": "2024-03", "note": "2024-03-04 第10篇"},
        # ... 每兩個月一個，共 12 個
    ],
    "chinatimes": [...],      # 12 個參考點
    "esg_businesstoday": [...], # 8 個參考點
    # Sequential sources: 透過 auto-discover 或手動填入
    "udn": [], "ltn": [], "einfo": [],
}
```

**驗證狀態**：
| 狀態 | 意義 |
|------|------|
| `found` | 已爬取，該時段覆蓋正常 |
| `confirmed_404` | 文章不存在（參考點需更換） |
| `not_scanned` | 尚未掃描到該區間 |

**Auto-discover**：對 sequential sources，從 `crawled_articles` 自動找出每月一篇代表性文章，建立覆蓋地圖。

**API**: `GET /api/indexing/reference-points`
**Dashboard**: Coverage tab 顯示驗證結果 + 月份覆蓋熱力圖

### 爬取模式說明

#### full_scan（完整 ID 掃描）

掃描指定範圍內的每一個 ID，不做 interpolation，不做 early-stop（僅 `FULL_SCAN_BLOCKED_LIMIT=50`，較 auto mode 的 5 寬鬆）。Full scan 模式下：
- Blocked cooldown 為 120s（normal=20s），給伺服器充足恢復時間
- 每個 source 有 `max_candidate_urls` 限制 404 fallback 的 HTTP 請求數（LTN=0, chinatimes=39, 其他=0）

```bash
# 透過 Dashboard API 啟動
curl -X POST http://localhost:8001/api/indexing/fullscan/start \
  -H "Content-Type: application/json" \
  -d '{"sources":["ltn","udn","cna"]}'

# 透過 CLI
python backfill.py --source udn --start-id 7800000 --end-id 9313000
python backfill.py --source cna --start-date 2024-01 --end-date 2026-02
```

#### Auto Mode（定期更新）
從最新 ID 往回爬，連續遇到已爬取的文章後自動停止。

```bash
python -m crawler.main --source ltn --auto-latest --count 100
```

#### List-based（moea）
從分類列表頁分頁爬取，能力受限於網站分頁深度。

#### Sitemap Mode（URL 型掃描）

從網站 sitemap XML 提取 URL 列表，直接爬取。命中率 100%（所有 URL 都指向存在的文章）。

```bash
# 透過 Dashboard API 啟動
curl -X POST http://localhost:8001/api/indexing/crawler/start \
  -H "Content-Type: application/json" \
  -d '{"source":"udn", "mode":"sitemap", "date_from":"202401"}'

# 支援參數
# date_from: 起始年月（YYYYMM），過濾 sitemap 中的 URL
# date_to: 結束年月（YYYYMM），可選
# limit: 限制爬取數量（0=不限），測試用
# sitemap_offset: 從第幾個 sub-sitemap 開始（0-based，多機分工用）
# sitemap_count: 處理幾個 sub-sitemap（0=全部，多機分工用）
```

**適用來源**：UDN（343 個子 sitemap，~99 萬 URL）、Chinatimes（1000 個子 sitemap，~15M URL）。
**優勢**：UDN full_scan 命中率僅 6%，sitemap 100%，效率 ~16 倍。Chinatimes sitemap 避免 Cloudflare 封鎖。
**引擎實作**：`engine.run_sitemap()` 支援 date filtering、dedup、batch processing、multi-machine offset/count slicing。
**日期過濾**：優先從 URL 提取發布日期（YYYYMMDD），lastmod 僅作 fallback（Chinatimes lastmod 不可靠）。

### 來源特殊處理

#### LTN — 子網域 HTML 結構差異

LTN 有多個子網域（news, health, ec, ent, sports, def, art），HTML 結構各不相同。Parser 使用 cascading selectors + `<p>` validation：

```python
# 用 select()（非 select_one()）找所有匹配元素，取第一個有 <p> 的
candidates = ['.whitecon.article .text', 'article .text', '.article_content', '.text', 'article']
for selector in candidates:
    for div in soup.select(selector):
        if div.find('p'):
            content_div = div
            break
```

**注意**: health.ltn.com.tw 有多個 `.text` div，第一個是空的。必須用 `select()` 而非 `select_one()`。

#### UDN — ID 空間特性

- URL 中的 category 不影響文章存取（UDN 內部會 resolve）
- IDs < 7,800,000 大量 404（UDN 清除了舊文）
- ID 空間稀疏：~5% 命中率，但月均 ~50K IDs = ~3,000 篇/月

#### ESG BT — Redirect 偵測

不存在的文章 301 redirect 到首頁。在 `_fetch()` 的 curl_cffi 和 aiohttp 兩個分支都需要偵測：

```python
def is_not_found_redirect(self, request_url: str, response_url: str) -> bool:
    return '/post/' in request_url and '/post/' not in response_url
```

### 核心模組

#### Parser Factory

```python
from crawler.parsers import CrawlerFactory, list_available_sources

# 列出可用來源
sources = list_available_sources()  # ['ltn', 'udn', 'cna', 'moea', 'einfo', 'esg_businesstoday', 'chinatimes']

# 取得 Parser 實例
parser = CrawlerFactory.get_parser('ltn')
parser = CrawlerFactory.get_parser('moea', count=100)  # 帶參數
```

#### BaseParser 介面

所有 Parser 必須實作以下方法：

```python
class BaseParser(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str:
        """來源代號，如 'ltn', 'udn'"""
        pass

    @abstractmethod
    def get_url(self, article_id: int) -> str:
        """根據 ID 構建文章 URL"""
        pass

    @abstractmethod
    async def get_latest_id(self, session=None) -> Optional[int]:
        """取得當前最新文章 ID"""
        pass

    @abstractmethod
    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """解析 HTML，回傳 Schema.org NewsArticle 格式"""
        pass

    @abstractmethod
    async def get_date(self, article_id: int) -> Optional[datetime]:
        """取得文章發布日期（輕量級）"""
        pass
```

#### 輸出格式 (Schema.org NewsArticle)

```json
{
  "@type": "NewsArticle",
  "headline": "文章標題",
  "articleBody": "文章內文...",
  "author": "記者姓名",
  "datePublished": "2026-01-28T10:30:00",
  "publisher": "自由時報",
  "inLanguage": "zh-TW",
  "url": "https://news.ltn.com.tw/...",
  "keywords": ["關鍵字1", "關鍵字2"]
}
```

### 共用工具類

#### TextProcessor

```python
from crawler.utils.text_processor import TextProcessor

# 文字清理
cleaned = TextProcessor.clean_text(raw_text)

# 智慧摘要
summary = TextProcessor.smart_extract_summary(paragraphs)

# 作者名稱標準化
author = TextProcessor.clean_author(raw_author)

# 關鍵字提取（多策略）
keywords = TextProcessor.extract_keywords_from_soup(soup, title)

# 簡易關鍵字提取（從標題）
keywords = TextProcessor.simple_keyword_extraction(title)

# 日期解析
date = TextProcessor.parse_iso_date("2026-01-28T10:30:00+08:00")
date = TextProcessor.parse_date_string("2026-01-28 10:30")

# 段落過濾
cleaned = TextProcessor.filter_paragraph(text, min_length=20)
```

### 設定檔

`code/python/crawler/core/settings.py` 集中管理所有設定：

```python
# HTTP 請求設定
REQUEST_TIMEOUT = 10
MAX_RETRIES = 2

# Session 類型（curl_cffi vs aiohttp）
# 注意：Parser 的 preferred_session_type 必須與 CURL_CFFI_SOURCES 一致
# 若不一致，engine 會 raise RuntimeError（fail fast，不做 silent fallback）
CURL_CFFI_SOURCES = ['cna', 'chinatimes', 'einfo', 'esg_businesstoday', 'moea']
# AIOHTTP sources: ['ltn', 'udn']（未列入 CURL_CFFI_SOURCES 的自動使用 aiohttp）

# 來源專屬併發設定（2026-02 調校）
NEWS_SOURCES = {
    "ltn":  {"concurrent_limit": 5, "delay_range": (0.5, 1.5)},  # ~2.4 req/s
    "udn":  {"concurrent_limit": 5, "delay_range": (0.5, 1.5)},  # ~2.7 req/s
    "cna":  {"concurrent_limit": 4, "delay_range": (0.8, 2.0)},  # ~1.5 req/s
    "einfo": {"concurrent_limit": 1, "delay_range": (5.0, 10.0)}, # ~0.1 req/s（站方限制）
    "esg_businesstoday": {"concurrent_limit": 3, "delay_range": (1.0, 2.5)},
    "chinatimes": {"concurrent_limit": 5, "delay_range": (0.8, 2.0), "max_candidate_urls": 39},
    "moea": {"concurrent_limit": 2, "delay_range": (2.0, 4.0)},  # 低併發避免 429
}

# 停止條件
BLOCKED_CONSECUTIVE_LIMIT = 5           # Auto/normal mode
FULL_SCAN_BLOCKED_LIMIT = 50            # Full scan 容許更多次 blocked
FULL_SCAN_BLOCKED_COOLDOWN = 120.0      # Full scan blocked 後冷卻 120s（normal=20s）
AUTO_DEFAULT_STOP_AFTER_SKIPS = 10

# AutoThrottle（Scrapy 風格自適應延遲）
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

# 通用文本處理
MIN_PARAGRAPH_LENGTH = 20
MIN_ARTICLE_LENGTH = 50
MAX_KEYWORDS = 10
```

### 併發調校記錄

| 設定 | 之前（被封鎖） | 之前（太慢） | 目前（穩定） |
|------|---------------|-------------|-------------|
| LTN concurrent | 8 | 3 | **5** |
| LTN delay | 0.3-1.0s | 1.0-2.5s | **0.5-1.5s** |
| UDN concurrent | 8 | 3 | **5** |
| UDN delay | 0.3-1.0s | 1.0-2.5s | **0.5-1.5s** |

- concurrent=8 + delay=0.3s → 403/429 rate limiting
- concurrent=3 + delay=1.0s → ~1.1 req/s（太慢，一個月要 7-12 小時）
- concurrent=5 + delay=0.5s → ~2.5 req/s（穩定，零 blocked）

### CLI 使用

```bash
# 基本爬取
python -m crawler.main ltn --count 100

# 指定日期範圍
python -m crawler.main udn --start 2026-01-01 --end 2026-01-28

# 輸出到指定目錄
python -m crawler.main cna --count 50 --output data/crawler/
```

### 檔案結構

```
code/python/crawler/
├── __init__.py
├── main.py                 # CLI 入口
├── core/
│   ├── __init__.py
│   ├── interfaces.py       # BaseParser 介面
│   ├── engine.py          # CrawlerEngine
│   ├── pipeline.py        # 爬取流程
│   ├── settings.py        # 集中設定
│   └── crawled_registry.py # SQLite 註冊表（WAL mode）
├── parsers/
│   ├── __init__.py
│   ├── factory.py         # CrawlerFactory
│   ├── ltn_parser.py      # 自由時報
│   ├── udn_parser.py      # 聯合報
│   ├── cna_parser.py      # 中央社
│   ├── moea_parser.py     # 經濟部
│   ├── einfo_parser.py    # 環境資訊中心
│   └── esg_businesstoday_parser.py  # 今周刊 ESG
├── utils/
│   └── text_processor.py  # 文字處理工具
└── tests/
    ├── test_parsers.py    # 單元測試 (34 tests)
    └── test_e2e.py        # E2E 測試（Dry Run + Live）
```

### 與 Indexing Module 整合

Crawler 輸出的 TSV 直接作為 Indexing Module 的輸入：

```
[Crawler]                      [Indexing]
    ↓                              ↓
  TSV 檔案  ─────────────→  IngestionEngine
(url<TAB>JSON-LD)                  ↓
                              QualityGate
                                   ↓
                             ChunkingEngine
                                   ↓
                              Dual Storage
```

### E2E 測試

#### 測試類型總覽

| 測試類型 | 指令 | 用途 |
|----------|------|------|
| **單元測試** | `pytest code/python/crawler/tests/test_parsers.py` | Parser 邏輯驗證 |
| **Dry Run** | `pytest ... -k "dry"` | 實例化、URL 生成 |
| **Live 測試** | `pytest ... --run-live` | 實際網路爬取 |
| **CLI Dry Run** | `python -m crawler.main --dry-run` | 快速手動測試 |
| **完整爬取** | `python -m crawler.main --count 10` | 實際產出 TSV |

#### 快速驗證（無需網路）

```bash
# 測試 Parser 實例化、URL 生成、ID 日期提取
python -m pytest code/python/crawler/tests/test_e2e.py -v -k "dry"

# 執行所有單元測試 (34 tests)
python -m pytest code/python/crawler/tests/test_parsers.py -v
```

#### Live 測試（實際爬取）

```bash
# 測試 LTN 和 UDN 實際爬取
python -m pytest code/python/crawler/tests/test_e2e.py -v --run-live
```

#### CLI 手動測試

**注意**：需從 `code/python/` 目錄執行

```bash
cd code/python

# Dry run（不儲存，測試解析邏輯）
python -m crawler.main --source ltn --auto-latest --count 3 --dry-run -v

# 實際爬取（不自動儲存）
python -m crawler.main --source ltn --auto-latest --count 5 --no-auto-save -v

# 完整爬取（儲存 TSV）
python -m crawler.main --source ltn --auto-latest --count 100
```

#### Python 手動測試

```python
import asyncio
from crawler.tests.test_e2e import manual_e2e_test

# 測試 LTN 爬取 2 篇
asyncio.run(manual_e2e_test('ltn', 2))

# 測試其他來源
asyncio.run(manual_e2e_test('udn', 2))
asyncio.run(manual_e2e_test('moea', 5))
```

#### 完整 Pipeline 測試（Crawler → Indexing）

```bash
cd code/python

# Step 1: 爬取並輸出 TSV
python -m crawler.main --source ltn --auto-latest --count 10

# Step 2: 將 TSV 送入 Indexing
python -m indexing.pipeline data/crawler/articles/ltn_*.tsv --site ltn
```

#### 測試輸出範例

```
2026-01-28 21:01:54 - LtnParser - INFO - Fetching latest ID from: https://news.ltn.com.tw/list/breakingnews
2026-01-28 21:01:54 - LtnParser - INFO - Latest ID: 5325467
2026-01-28 21:01:55 - LtnParser - INFO - Successfully parsed: https://news.ltn.com.tw/.../5325467
2026-01-28 21:01:56 - main - INFO - ============================================================
2026-01-28 21:01:56 - main - INFO - Dry Run Completed!
2026-01-28 21:01:56 - main - INFO -    Total:     2
2026-01-28 21:01:56 - main - INFO -    Success:   1
2026-01-28 21:01:56 - main - INFO -    Success Rate: 50.00%
2026-01-28 21:01:56 - main - INFO - ============================================================
```

---

## 模組說明

### 1. Source Manager (`source_manager.py`)

管理新聞來源的可信度分級。

```python
from indexing import SourceManager, SourceTier

manager = SourceManager()
tier = manager.get_tier('udn.com')      # SourceTier.VERIFIED (2)
label = manager.get_tier_label('udn.com')  # 'verified'
```

**來源分級**：

| Tier | 名稱 | 說明 | 範例 |
|------|------|------|------|
| 1 | AUTHORITATIVE | 官方、通訊社 | cna.com.tw, gov.tw |
| 2 | VERIFIED | 主流媒體 | udn.com, ltn.com.tw |
| 3 | STANDARD | 一般新聞（預設） | 未知來源 |
| 4 | AGGREGATOR | 聚合站 | - |

---

### 2. Ingestion Engine (`ingestion_engine.py`)

解析 TSV 檔案為標準資料模型 (CDM)。

**輸入格式**：`url<TAB>JSON-LD`

```python
from indexing import IngestionEngine

engine = IngestionEngine()

# 解析單行
cdm = engine.parse_tsv_line('https://example.com/news\t{"headline": "標題", "articleBody": "內容"}')

# 解析整個檔案
for cdm in engine.parse_tsv_file(Path('data.tsv')):
    print(cdm.headline, cdm.source_id)
```

**CDM 欄位**：

| 欄位 | 類型 | 說明 |
|------|------|------|
| url | str | 文章 URL |
| headline | str | 標題 |
| article_body | str | 內文 |
| source_id | str | 來源域名 |
| author | Optional[str] | 作者 |
| date_published | Optional[datetime] | 發布日期 |
| keywords | list[str] | 關鍵字 |
| is_valid | bool | 解析是否成功 |

---

### 3. Quality Gate (`quality_gate.py`)

驗證文章品質，過濾不合格內容。

```python
from indexing import QualityGate, QualityStatus

gate = QualityGate()
result = gate.validate(cdm)

if result.passed:
    # 處理文章
    pass
else:
    print(f"拒絕原因: {result.failure_reasons}")
```

**檢查項目**：

| 檢查 | 條件 | 預設值 |
|------|------|--------|
| 內容長度 | `article_body` > N 字元 | 50 |
| 標題存在 | `headline` 非空 | - |
| HTML 比例 | HTML 標籤 < N% | 30% |
| 中文比例 | 中文字 > N% | 20% |
| Script 偵測 | 無 JavaScript 語法 | - |

**配置** (`config/config_indexing.yaml`)：

```yaml
quality_gate:
  min_body_length: 50
  min_chinese_ratio: 0.2
  max_html_ratio: 0.3
```

---

### 4. Chunking Engine (`chunking_engine.py`)

將文章切分為適當大小的 chunks。

```python
from indexing import ChunkingEngine

chunker = ChunkingEngine()
chunks = chunker.chunk_article(cdm)

for chunk in chunks:
    print(f"Chunk {chunk.chunk_index}: {len(chunk.full_text)} chars")
    print(f"Summary: {chunk.summary[:100]}...")
```

**分塊策略**（POC 驗證）：

| 參數 | 值 | 說明 |
|------|-----|------|
| target_length | 170 | 目標字數/chunk |
| min_length | 100 | 最小字數（避免過碎） |
| short_article_threshold | 200 | 短文整篇作為 1 chunk |

**Chunk 結構**：

```python
@dataclass
class Chunk:
    chunk_id: str       # "{url}::chunk::{index}"
    article_url: str
    chunk_index: int
    sentences: list[str]
    full_text: str
    summary: str        # headline + 代表句
    char_start: int
    char_end: int
```

**Chunk ID 格式**：

```python
from indexing import make_chunk_id, parse_chunk_id

chunk_id = make_chunk_id("https://example.com/news", 0)
# "https://example.com/news::chunk::0"

url, index = parse_chunk_id(chunk_id)
# ("https://example.com/news", 0)
```

---

### 5. Dual Storage (`dual_storage.py`)

雙層儲存架構。

#### The Vault (SQLite)

儲存 Zstd 壓縮的原文。

```python
from indexing import VaultStorage, VaultConfig

# 使用預設路徑
vault = VaultStorage()

# 自訂路徑
config = VaultConfig(db_path=Path('data/vault/my_vault.db'))
vault = VaultStorage(config)

# 儲存
vault.store_chunk(chunk)
vault.store_chunks(chunks)  # 批次儲存

# 取回
text = vault.get_chunk("url::chunk::0")
all_texts = vault.get_article_chunks("url")

# 軟刪除
vault.soft_delete_chunks(["url::chunk::0"])

vault.close()
```

#### The Map (Qdrant Payload)

> **（已廢除 2026-06）** 本節描述的 Qdrant 儲存路徑與 `qdrant_uploader.py` 已移除。現役儲存為 PostgreSQL（見上方主路徑）。

```python
from indexing import MapPayload

payload = MapPayload.from_chunk(
    chunk=chunk,
    site="udn",
    headline="文章標題",
    date_published="2026-01-28T10:30:00",
    author="記者姓名",
    publisher="聯合報",
    keywords=["關鍵字1"],
    description="文章前200字...",
    task_id="backfill_udn_1_1706400000",
)
qdrant_payload = payload.to_dict()
# {
#     'url': 'https://example.com/news',          # article URL
#     'name': '文章標題。第一句話。',               # chunk summary
#     'site': 'udn',
#     'schema_json': '{"@type":"NewsArticle",...}', # article metadata
#     'chunk_id': 'https://...::chunk::0',
#     'article_url': 'https://example.com/news',
#     'chunk_index': 0,
#     'char_start': 0, 'char_end': 170,
#     'keywords': ['關鍵字1'],
#     'indexed_at': '2026-01-28T12:00:00',
#     'task_id': 'backfill_udn_1_1706400000',
#     'version': 2
# }
```

---

### 6. Rollback Manager (`rollback_manager.py`)

管理遷移記錄，支援回滾。

```python
from indexing import RollbackManager

rm = RollbackManager()

# 開始遷移
migration_id = rm.start_migration(site="udn")

# 備份舊資料
rm.record_old_points(migration_id, old_point_ids)
rm.backup_payloads(migration_id, [{'point_id': '...', 'payload': {...}}])

# 完成遷移
rm.complete_migration(migration_id, new_chunk_ids)

# 查詢遷移記錄
record = rm.get_migration(migration_id)
records = rm.get_migrations_by_site("udn")

# 標記回滾
rm.mark_rolled_back(migration_id)

# 清理舊備份（30 天前）
deleted = rm.cleanup_old_backups(days=30)

rm.close()
```

---

### 7. Pipeline (`pipeline.py`)

主流程，整合所有模組。

#### 基本使用

```python
from indexing import IndexingPipeline
from pathlib import Path

pipeline = IndexingPipeline()

# 處理 TSV 檔案
result = pipeline.process_tsv(Path('data.tsv'), site_override='udn')

print(f"成功: {result.success}")
print(f"失敗: {result.failed}")
print(f"緩衝: {result.buffered}")
print(f"總 chunks: {result.total_chunks}")

pipeline.close()
```

#### 斷點續傳

```python
# 支援中斷恢復
result = pipeline.process_tsv_resumable(
    Path('large_data.tsv'),
    checkpoint_file=Path('checkpoint.json'),
    site_override='udn'
)
```

#### CLI 使用

```bash
# 基本處理
python -m indexing.pipeline data.tsv

# 指定 site
python -m indexing.pipeline data.tsv --site udn

# 斷點續傳
python -m indexing.pipeline data.tsv --resume

# 自訂 checkpoint 檔案
python -m indexing.pipeline data.tsv --checkpoint my_checkpoint.json

# 上傳到 Qdrant
python -m indexing.pipeline data.tsv --site ltn --upload

# Reconciliation（比對 Vault 與 Qdrant，補上缺失的 chunks）
python -m indexing.pipeline --reconcile
python -m indexing.pipeline --reconcile --site ltn
```

#### Reconciliation 工具

`pipeline.py --reconcile` 比對 Vault (SQLite) 與 Qdrant 的 chunk 一致性：

1. 批次迭代 Vault 中所有 chunk_ids（每批 10,000）
2. 對每批呼叫 `qdrant.check_exists()` 檢查 point 是否存在
3. 缺失的 chunk 重新 embed 並上傳

```python
pipeline = IndexingPipeline(upload_to_qdrant=True)
result = pipeline.reconcile(site="ltn")
print(f"Re-uploaded: {result['missing_fixed']}")
```

#### Overlap 與 Payload 的關係

- `embedding_text`（含 overlap 30 字）用於 Qdrant 向量生成
- `full_text`（不含 overlap）用於 Vault 儲存和摘要
- `summary` 用於 Qdrant payload 的 `name` 欄位
- `char_start`/`char_end` 記錄原始位置（不含 overlap）

---

### 8. Vault Helpers (`vault_helpers.py`)

提供 async 介面，供 retriever/reasoning 模組使用。

```python
from indexing import get_full_text_for_chunk, get_full_article_text, get_chunk_metadata, close_vault

# Async 取得 chunk 原文
text = await get_full_text_for_chunk("url::chunk::0")

# Async 取得整篇文章
full_article = await get_full_article_text("https://example.com/news")

# Sync 解析 chunk metadata
meta = get_chunk_metadata("url::chunk::0")
# {'article_url': 'url', 'chunk_index': 0}

# 關閉連線
close_vault()
```

---

## 配置檔案

`config/config_indexing.yaml`：

```yaml
# 品質閘門
quality_gate:
  min_body_length: 50
  min_chinese_ratio: 0.2
  max_html_ratio: 0.3

# 分塊參數
chunking:
  strategy: "length_based"
  target_length: 170
  min_length: 100
  short_article_threshold: 200
  summary_max_length: 400
  extractive_summary_sentences: 3

# 來源分級
source_mappings:
  cna.com.tw: 1      # 中央社
  gov.tw: 1          # 政府
  udn.com: 2         # 聯合報
  ltn.com.tw: 2      # 自由時報
  # 未列出的來源預設為 3

# Pipeline
pipeline:
  checkpoint_interval: 10
  batch_size: 100
```

---

## 資料流向

```
輸入
├── TSV 檔案 (url + JSON-LD)

處理
├── IngestionEngine → CDM
├── QualityGate → Pass/Buffer
├── ChunkingEngine → List[Chunk]

輸出
├── Vault (SQLite)
│   └── data/vault/full_texts.db
├── Buffer (品質不合格)
│   └── data/indexing/buffer.jsonl
├── Checkpoint (斷點續傳)
│   └── {tsv_path}.checkpoint.json
└── Migration DB (回滾記錄)
    └── data/indexing/migrations.db
```

---

## 與現有系統整合

### Retriever 整合

Qdrant payload 的 `url` 欄位是**文章 URL**（非 chunk_id）。Retriever 的 `_deduplicate_by_url` 方法會自動依文章 URL 合併同篇的多個 chunks（B+ merge 策略）：

```python
# 取得原文（使用 chunk_id，非 url）
from indexing import get_full_text_for_chunk

async def enrich_search_result(result):
    chunk_id = result.get('chunk_id')  # payload 的 chunk_id 欄位
    full_text = await get_full_text_for_chunk(chunk_id)
    return full_text
```

### Qdrant Payload 結構 (Version 2)

> **（已廢除 2026-06）** 本節描述的 Qdrant 儲存路徑與 `qdrant_uploader.py` 已移除。現役儲存為 PostgreSQL（見上方主路徑）。

```json
{
  "url": "https://example.com/news",
  "name": "標題。第一句話。最後一句話。",
  "site": "udn",
  "schema_json": "{\"@context\":\"https://schema.org\",\"@type\":\"NewsArticle\",\"headline\":\"文章標題\",\"datePublished\":\"2026-01-28T10:30:00\",\"author\":\"記者姓名\",\"publisher\":\"聯合報\"}",
  "chunk_id": "https://example.com/news::chunk::0",
  "article_url": "https://example.com/news",
  "chunk_index": 0,
  "char_start": 0,
  "char_end": 170,
  "keywords": ["關鍵字1", "關鍵字2"],
  "indexed_at": "2026-01-28T12:00:00",
  "task_id": "backfill_udn_1_1706400000",
  "version": 2
}
```

**欄位說明**：
- `url`: **文章 URL**（非 chunk_id），供 retriever 引用/去重
- `name`: chunk 摘要（headline + 代表句）
- `schema_json`: 文章級 Schema.org NewsArticle metadata（JSON 字串）
- `chunk_id`: chunk 唯一識別碼 (`{article_url}::chunk::{index}`)
- `task_id`: 資料溯源（對應 crawler task ID）

---

## POC 驗證結果 (2026-01-28)

### 語義分塊 vs 長度分塊

| 策略 | 結果 |
|------|------|
| 語義分塊 | 中文新聞相鄰句子相似度 < 0.5，導致每句切一塊 |
| 長度分塊 | 170 字/chunk，區別度 ~0.56（理想範圍 0.4-0.6）|

**結論**：採用長度優先策略，在句號邊界切分。

### 區別度評估

| 範圍 | 評價 |
|------|------|
| > 0.8 | 太相似，檢索難區分 |
| 0.4-0.6 | 理想範圍 |
| < 0.4 | 太碎，上下文丟失 |

---

## 檔案結構

```
code/python/indexing/
├── __init__.py           # 模組匯出
├── source_manager.py     # 來源分級
├── ingestion_engine.py   # TSV 解析
├── quality_gate.py       # 品質驗證
├── chunking_engine.py    # 分塊引擎
├── postgresql_uploader.py # 【主路徑】PG 上傳 + Qwen3-4B INT8 embedding + GPU thermal
├── pg_batch.py           # 【主路徑】PG 批次 indexer（PGCheckpoint 續傳）
├── cloud_embed.py        # 【主路徑】GCP L4 VM：chunking + Qwen3-4B → .jsonl/.npy
├── bulk_load.py          # 【主路徑】.jsonl+.npy bulk insert 到 VPS PG（見 bulk-load-spec.md）
├── dual_storage.py       # 【Qdrant 路徑】雙層儲存（MapPayload + VaultStorage）
├── embedding.py          # 【Qdrant 路徑】本地 Embedding（bge-m3 / OpenAI）
├── qdrant_uploader.py    # 【Qdrant 路徑】Qdrant 向量上傳 + reconciliation
├── rollback_manager.py   # 回滾管理
├── pipeline.py           # 【Qdrant 路徑】主流程 + CLI + reconcile
├── vault_helpers.py      # 【Qdrant 路徑】Async helpers
└── poc_*.py              # POC 驗證腳本（保留）

config/
└── config_indexing.yaml  # 配置檔

data/
├── vault/
│   └── full_texts.db     # Vault 資料庫
└── indexing/
    ├── buffer.jsonl      # 品質不合格緩衝
    └── migrations.db     # 遷移記錄
```

---

## Backfill 與即時更新規劃（2026-02）

### 概述

本章節記錄一年 backfill 與每日即時更新的規劃決策。

### 最終決策

| 項目 | 決策 | 理由 |
|------|------|------|
| **Qdrant Hosting** | 本地 Docker | 開發機 40GB RAM 足夠，不需租 VPS |
| **去重策略** | udn + money_udn 都爬，URL 自動去重 | money_udn 有獨特財經報導 |
| **Backfill 順序** | 最近月份往回爬 | 最新資料先可用，用戶可更快使用系統 |
| **crawled_ids** | 遷移到 SQLite | 支援 dateModified 判斷更新 |

---

### 1. Overlap 30 字 — Embedding 優化

目前 chunking 是純切割，加 overlap 可提升跨 chunk 邊界的檢索品質。

**設計**：
```
原始切割：  [chunk0] [chunk1] [chunk2]
加 overlap：[chunk0 + 後30字] [前30字 + chunk1 + 後30字] [前30字 + chunk2]
```

**實作要點**：
- overlap 文字只進入 `embedding_text`（用於 embedding），不進入 `summary`（避免摘要重複）
- `char_start` / `char_end` 仍記錄原始位置，方便還原
- 第一個 chunk 沒有前 overlap，最後一個 chunk 沒有後 overlap
- 文章太短（只有 1 個 chunk）保持原樣

**Qdrant 影響**：embedding 文字從 ~170 chars → ~230 chars，token 增加 ~35%。成本影響可忽略（一年 ~$4.3 → ~$5.8）。

**實作範例**：
```python
def add_overlap(chunks: list[Chunk], overlap_chars: int = 30) -> list[Chunk]:
    for i, chunk in enumerate(chunks):
        prefix = ""
        suffix = ""

        if i > 0:
            prev_text = chunks[i-1].full_text
            prefix = prev_text[-overlap_chars:] if len(prev_text) >= overlap_chars else prev_text

        if i < len(chunks) - 1:
            next_text = chunks[i+1].full_text
            suffix = next_text[:overlap_chars] if len(next_text) >= overlap_chars else next_text

        chunk.embedding_text = prefix + chunk.full_text + suffix

    return chunks
```

---

### 2. Backfill 一年 — 批次管理策略

#### TSV 命名規範

```
{source}_{year}-{month}[_part{N}].tsv

範例：
├── udn_2025-03.tsv           # < 5000 篇，不分段
├── udn_2025-12_part1.tsv     # > 5000 篇，分段
├── udn_2025-12_part2.tsv
```

#### 一年 Backfill 估算（2026-02 實測更新）

| 來源 | 月均文章 | 一年文章 | 命中率 | IDs/月 | 掃描時間/月 |
|------|----------|----------|--------|--------|-------------|
| ltn | ~4,700 | ~56K | 15% | ~30K | ~3.4h |
| udn | ~3,000 | ~36K | 5% | ~50K | ~5.1h |
| cna | ~9,500 | ~114K | 89% | ~10K | ~1.8h |
| einfo | ~500 | ~6K | 6% | ~8K | 很慢（concurrent=1）|
| esg_bt | ~100 | ~1.2K | 2% | ~5K | ~2.8h |
| **合計** | **~17,800** | **~213K** | | | |

> 注意：LTN/UDN 的實際文章數比原始估算低，因為 ID 空間稀疏（不是每個 sequential ID 都對應文章）。UDN 的 2024-01 之前文章已被刪除。

#### Backfill 速度估算

| 來源 | 吞吐量 | 26 個月所需時間 |
|------|--------|----------------|
| LTN | ~2.4 req/s | ~3.7 天 |
| UDN | ~2.7 req/s | ~5.5 天 |
| CNA | ~1.5 req/s | ~1.2 天 |
| einfo | ~0.1 req/s | ~23 天 |
| ESG BT | ~0.5 req/s | ~2.9 天 |

#### Full Scan Resume（Checkpoint-based）

失敗或停止的 full scan 任務可從中斷點繼續：

- **Sequential sources**: 從 `last_scanned_id + 1` 繼續
- **Date-based sources**: 從 `last_scanned_date` 繼續
- Checkpoint 在每個 batch 完成後更新（不是 gather 前），避免 crash 跳過未完成 ID
- `crawled_registry` 自動跳過已爬取的文章

#### Full Scan 404 Skip（三層加速機制，2026-02-10，watermark skip 修復 2026-02-12）

重掃已覆蓋範圍時，透過三層 skip 避免重複 HTTP request：

| 層級 | 檢查 | 成本 | 說明 |
|------|------|------|------|
| **Watermark** | `id <= last_scanned_id AND id NOT IN blocked_ids` | O(1) int + O(1) set | 低於 watermark 且非 blocked 的 ID 才跳過 |
| **not_found_ids** | `id in Set[int]` | O(1) set | 本輪或歷史確認的 404 article ID（持久化到 `not_found_articles` 表）|
| **crawled_ids** | URL 生成 + set lookup | O(k) | 已成功爬取的文章 URL |

**Watermark Skip Bug 修復（2026-02-12）**：

原始實作中，watermark skip 條件為 `current_id <= watermark_id`，會將所有低於 watermark 的 ID 視為「已掃描」直接跳過。但 HTTP 429 blocked URLs 雖然記錄在 `failed_urls` 表中，實際上從未成功抓取。這導致 4,581 個 blocked URLs（跨 7 個 source）被永久跳過。

修復方式：
- **Sequential sources**：載入 `_blocked_ids`（從 `failed_urls` 表中 `error_type='blocked'` 的 URL 解析出 article ID），skip 條件改為 `id <= watermark AND id NOT IN blocked_ids`
- **Date-based sources**：載入 `_blocked_dates`（從 blocked URL 解析出日期），skip 條件改為 `day <= watermark_date AND day NOT IN blocked_dates`
- 新增 `crawled_registry.load_blocked_ids()` 和 `load_blocked_dates()` 方法
- 使用 regex cascade 解析各 source URL 格式（MOEA `news_id=`, LTN `/breakingnews/`, einfo `/node/`, UDN `/story/`, CNA/chinatimes/ESG BT date-based ID）

**資料流**：
- `_apply_full_scan_overrides()` 啟動時從 DB 載入 watermark + not_found_ids + blocked_ids/blocked_dates
- `_process_article()` 在真正 404 路徑記錄 article_id 到記憶體 set + DB
- `flush_not_found()` 在每個 batch 結束時與 watermark 一起 commit（非逐筆 commit）
- Date-based sources 以整天為單位跳過（`current_day <= watermark_date AND day NOT IN blocked_dates`）

**新增 DB table**：
```sql
CREATE TABLE IF NOT EXISTS not_found_articles (
    source_id TEXT NOT NULL,
    article_id INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (source_id, article_id)
);
```

**預期效果**：UDN 重掃 7.8M→9.3M，若 watermark=9.0M，則 7.8M~9.0M 全部秒跳過（int 比較，無 HTTP request）。重掃時 blocked URLs 不會被跳過，可透過正常 full_scan 流程自動回補。

#### 多機協作與資料合併

多台機器（桌機/筆電/GCP）分散爬取時，各機器產出獨立的 `crawled_registry.db` 和 `articles/` TSV 檔案。完成後需合併回桌機。

**合併工具**：`crawler/remote/merge_registry.py`

```bash
# 合併遠端 registry 到本地（dry-run 預覽）
python crawler/remote/merge_registry.py <remote-registry.db> data/crawler/crawled_registry.db --dry-run

# 實際合併
python crawler/remote/merge_registry.py <remote-registry.db> data/crawler/crawled_registry.db
```

**合併策略**：

| Table | 策略 | 說明 |
|-------|------|------|
| `crawled_articles` | INSERT OR IGNORE | 不覆蓋已有的成功記錄 |
| `not_found_articles` | INSERT OR IGNORE | 不重複記錄 404 |
| `failed_urls` | INSERT OR IGNORE + 排除已成功 | 遠端失敗但本地已成功的 URL 不匯入 |
| `scan_watermarks` | 取較大值 | 確保 watermark 不倒退 |

**Retry 流程**：合併後在桌機執行 `retry` mode，統一重試所有機器的暫時性失敗（timeout、blocked 等）。

**部署指南**：`docs/crawler-deployment-prompt.md`（筆電/GCP 操作步驟）

---

#### Qdrant 儲存估算

去重後 ~600K 文章，~3 chunks/article = 1.8M 向量：
- 向量：1.8M × 6 KB (1536d float32) ≈ **10.8 GB**
- Payload：~500 bytes/point ≈ **0.9 GB**
- **總計：~12 GB**（需至少 16 GB RAM）

#### Embedding API 成本

1.8M chunks × ~120 tokens ≈ 216M tokens × $0.02/M = **~$4.3**

---

### 3. 即時更新 — 分級頻率

#### 更新頻率設計

| 等級 | 來源 | 頻率 | 理由 |
|------|------|------|------|
| Tier 1 | cna, ltn, udn | 每 1 小時 | 主流新聞，即時性重要 |
| Tier 2 | nextapple, money_udn | 每 3 小時 | 次要即時來源 |
| Tier 3 | einfo, moea, esg_bt | 每 6 小時 | 更新頻率本來就低 |

#### 去重流程（URL + dateModified）

```
爬取文章 → 檢查 URL 是否存在於 crawled_articles DB
├─ 不存在 → 新文章，直接 index
└─ 存在 → 比較 dateModified
   ├─ dateModified 更新 → 重新 index（soft delete 舊 chunks → 建新 chunks）
   └─ dateModified 相同或沒有 → 跳過
```

#### 爬取停止條件

所有停止條件常數統一定義於 `crawler/core/settings.py`：

| 常數 | 值 | 適用模式 | 說明 |
|------|----|----------|------|
| `BLOCKED_CONSECUTIVE_LIMIT` | 5 | 全部 | 連續 N 次 403/429 後停止 |
| `AUTO_DEFAULT_STOP_AFTER_SKIPS` | 10 | Auto | 連續 N 個已爬取後停止 |
| `AUTOTHROTTLE_ENABLED` | True | 全部 | Scrapy 風格自適應延遲（False 時回退固定 delay）|
| `AUTOTHROTTLE_TARGET_CONCURRENCY` | 1.0 | 全部 | AutoThrottle 目標並行數 |

Full Scan 設計原則：**不做 404 early-stop**。連續 404 只降速，不停止掃描。唯一停止條件是 `BLOCKED_CONSECUTIVE_LIMIT`（被封鎖）或到達 end_id/end_date。

---

### 3.5 Engine 層級 Fallback 與自適應機制（2026-02-10）

#### AutoThrottle（Scrapy 風格自適應延遲）

取代固定 `random.uniform(min_delay, max_delay)`，根據伺服器回應速度動態調速。

**公式**（EWMA 平滑）：
```
target_delay = avg_latency / TARGET_CONCURRENCY
new_delay = (old_delay + target_delay) / 2.0
new_delay = clamp(new_delay, min_delay, max_delay)  # per-source 硬邊界不變
```

**特性**：
- 開關：`AUTOTHROTTLE_ENABLED = True`（False 時回退到固定 random delay）
- ±10% jitter 避免同步爆發，jitter 後仍 clamp 到 `[min_delay, max_delay]` 確保不超限
- 錯誤回應（403/429/5xx）自動 `_throttle_backoff()` 加倍 delay
- 4 個呼叫點全部替換：`run_auto()`、`_full_scan_sequential`、`_full_scan_date_based`、`run_retry_urls()`

**效果**：LTN（快源）delay 收斂到 min_delay 附近；einfo（慢源）維持在 max_delay 附近。

#### Response Latency 追蹤

- `_latencies`：rolling window 50 筆
- `_avg_latency`：滾動平均
- `_current_delay`：當前自適應延遲值
- `_report_progress()` 回報 `stats['avg_latency']` 和 `stats['current_delay']`

#### htmldate 通用 Fallback

任何來源的 parser 漏掉日期時，engine 自動用 `htmldate.find_date()` 補上。

```python
def _ensure_date(self, data, html, url=""):
    if data.get('datePublished'):
        return data  # 已有日期，不動
    hd = find_date(html, outputformat='%Y-%m-%d')
    if hd:
        data['datePublished'] = f"{hd}T00:00:00"
        return data
    # url 參數用於 warning log，方便追蹤被丟棄的文章
    return None  # 無日期 → 丟棄
```

**放 engine 而非 BaseParser 的原因**：`find_date()` 需要原始 HTML，而 `BaseParser.parse()` 只拿到結構化 data。engine 是同時持有 html + data 的唯一地方。

#### Trafilatura 通用 Fallback

任何來源的 parser 回傳 None 時，用 `trafilatura.bare_extraction(favor_precision=True)` 做最後嘗試。

**呼叫順序**：
```
custom parse → None
  → trafilatura fallback → 成功？回傳（標記 _source: "trafilatura_fallback"）
  → 失敗 → 嘗試 candidate URLs（既有邏輯不變）
```

**不會重複處理 einfo**：einfo parser 已內建 trafilatura，`parse()` 不會回傳 None（除非兩者都失敗）。engine fallback 只在 `parse()` 整體回傳 None 時觸發。

**追蹤**：`stats['trafilatura_fallbacks']` 計數器。

#### charset_normalizer 編碼偵測

`_fetch()` 中使用 `charset_normalizer.from_bytes()` 自動偵測編碼，取代 `response.text` 避免 Big5/cp950 解碼錯誤。

#### BaseException 例外處理

`_evaluate_batch_results()` 使用 `isinstance(result, BaseException)` 而非 `Exception`。Python 3.9+ 的 `asyncio.CancelledError` 繼承自 `BaseException`，若只用 `Exception` 判斷，stop 操作觸發的 CancelledError 會漏過例外判斷，導致 tuple unpack 失敗。

---

### 4. crawled_ids 遷移到 SQLite

#### 現有設計（檔案系統）

```
data/crawled_ids/
├── udn.txt           # 每行一個 URL
├── cna.txt
└── ...
```

**限制**：
- 只能判斷「有沒有爬過」，無法判斷文章是否更新
- 94 萬 URL × ~100 bytes ≈ 94 MB 常駐記憶體
- 無法跨來源去重
- 無法查詢統計

#### 新設計（SQLite）

```sql
CREATE TABLE crawled_articles (
    url TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    date_published TEXT,
    date_modified TEXT,
    date_crawled TEXT NOT NULL,
    content_hash TEXT,  -- 前 500 字的 hash，用於跨來源去重
    task_id TEXT,       -- 資料溯源：crawler task ID
    batch_id TEXT       -- 資料溯源：batch ID
);

CREATE INDEX idx_source ON crawled_articles(source_id);
CREATE INDEX idx_date ON crawled_articles(date_published);
CREATE INDEX idx_hash ON crawled_articles(content_hash);
CREATE INDEX idx_task ON crawled_articles(task_id);
```

**注意**：SQLite 使用 WAL 模式（`PRAGMA journal_mode=WAL`），超時 30 秒，避免併發寫入衝突。

**新增能力**：
- `date_modified` 不同 → 重新 index
- `content_hash` 相同 → 跨來源去重（udn vs money_udn）
- 可查詢「某天爬了多少篇」、「某來源有多少篇」

---

### 5. 儲存大小總結

#### 單篇文章儲存開銷

| 存儲層 | 每文章 | 說明 |
|--------|--------|------|
| 原始 TSV | ~3.2 KB | 含完整 JSON-LD metadata |
| Vault (Zstd) | ~0.9 KB | 3 chunks × ~300 bytes |
| Qdrant 向量 | ~19.5 KB | 3 chunks × 6.5 KB |
| **合計** | **~23.6 KB** | |

#### 一年總儲存

| 存儲 | 大小 | 存放位置 |
|------|------|----------|
| 原始 TSV 備份 | ~2.9 GB | 本地/冷儲存（gzip 後 ~900 MB） |
| Vault SQLite | ~540 MB | 本地 SSD |
| Qdrant | ~12 GB | 本地 Docker |
| crawled_articles | ~50 MB | 本地 SQLite |
| **合計** | **~15.5 GB** | |

---

### 6. 本地 Qdrant 啟動

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v ./qdrant_data:/qdrant/storage \
  qdrant/qdrant:latest
```

管理介面：`http://localhost:6333/dashboard`

---

### 7. 實作優先順序

1. ✅ **crawled_ids SQLite 遷移** — 前置依賴，即時更新需要 dateModified
2. ✅ **Overlap 30 字** — 改 chunking_engine.py
3. **小規模 backfill 測試** — 驗證 pipeline 正確性
4. **完整 backfill** — 從 2026-01 往回爬

---

### 8. 實作驗證結果（2026-02-03）

#### 新增檔案

| 檔案 | 說明 |
|------|------|
| `crawler/core/crawled_registry.py` | SQLite 註冊表，取代 txt 檔案系統 |
| `tests/test_indexing_updates.py` | 兩功能的單元測試（11 tests） |

#### 修改檔案

| 檔案 | 修改內容 |
|------|----------|
| `indexing/chunking_engine.py` | 新增 `embedding_text` 欄位、`_add_overlap()` 方法 |
| `crawler/core/engine.py` | 使用 `CrawledRegistry`，`_mark_as_crawled()` 記錄元數據 |
| `crawler/core/pipeline.py` | 移除舊的 txt 檔案寫入邏輯 |
| `config/config_indexing.yaml` | 新增 `overlap_chars: 30` 設定 |

#### 測試結果

```
$ python -m pytest tests/test_indexing_updates.py -v

TestCrawledRegistry::test_basic_operations PASSED
TestCrawledRegistry::test_needs_update PASSED
TestCrawledRegistry::test_content_hash_dedup PASSED
TestCrawledRegistry::test_statistics PASSED
TestCrawledRegistry::test_migrate_from_txt PASSED
TestChunkingOverlap::test_overlap_basic PASSED
TestChunkingOverlap::test_overlap_first_chunk PASSED
TestChunkingOverlap::test_overlap_last_chunk PASSED
TestChunkingOverlap::test_single_chunk_no_overlap PASSED
TestChunkingOverlap::test_overlap_disabled PASSED
TestChunkingOverlap::test_overlap_size PASSED

============================= 11 passed in 0.58s ==============================
```

#### 向後兼容性

- 現有 crawler 測試（34 tests）全部通過
- 舊的 txt 檔案會自動遷移到 SQLite，並重命名為 `.txt.bak`
- `chunk_article()` 預設啟用 overlap，可透過 `add_overlap=False` 關閉

#### CrawledRegistry 功能驗證

| 功能 | 狀態 | 說明 |
|------|------|------|
| 基本 CRUD | ✅ | `mark_crawled()`, `is_crawled()` |
| dateModified 比較 | ✅ | `needs_update()` 判斷是否需重爬 |
| content_hash 去重 | ✅ | `find_duplicate_by_hash()` 跨來源去重 |
| 統計查詢 | ✅ | `get_stats()`, `get_count_by_source()` |
| txt 遷移 | ✅ | `migrate_from_txt()` 自動遷移舊資料 |

#### Overlap 功能驗證

| 情境 | 狀態 | 說明 |
|------|------|------|
| 多 chunk 文章 | ✅ | 中間 chunk 有前後 overlap |
| 第一個 chunk | ✅ | 無前綴，有後綴 |
| 最後一個 chunk | ✅ | 有前綴，無後綴 |
| 單一 chunk | ✅ | `embedding_text == full_text` |
| 關閉 overlap | ✅ | `add_overlap=False` 時 `embedding_text` 為空 |

---

## 儲存與 Embedding：單一現役路徑（PostgreSQL；Qdrant 路徑已廢除 2026-06）

> **⚠️ 重要（2026-06 更新）**：現役儲存 + embedding 為**單一路徑 — PostgreSQL（pgvector + pg_bigm）+ Qwen3-Embedding-4B INT8（1024d）**。spec 早期章節描述的 Qdrant 路徑已於 2026-06 **徹底廢除**（`qdrant_uploader.py` / `qdrant_profile.py` / `indexing/embedding.py` 已移除），下方「§9 本地 Embedding + Qdrant 上傳實作」僅留作歷史細節。
>
> 架構已收斂為單一現役路徑（PostgreSQL）；Qdrant 路徑已廢除，下方歷史章節僅供參考。

| 維度 | **PostgreSQL 路徑（主）** | Qdrant 路徑（optional / 歷史） |
|------|--------------------------|-------------------------------|
| 狀態 | 現行 production 路徑（2026-02 遷移完成） | 早期實作，現作可選 / 開發參考 |
| Embedding 模型 | **`Qwen/Qwen3-Embedding-4B`（INT8 量化，本機 GPU）** | `text-embedding-3-small`（OpenAI 1536d）/ `BAAI/bge-m3`（本機 1024d） |
| 向量維度 | **1024**（`truncate_dim=1024`） | 1536（OpenAI）/ 1024（bge-m3）|
| 向量儲存 | PostgreSQL `chunks.embedding`（pgvector） | Qdrant collection `nlweb` |
| 原文儲存 | PostgreSQL `articles.content` | The Vault（SQLite + Zstd 壓縮）|
| BM25 / 全文 | `chunks.tsv` + pg_bigm（`idx_chunks_tsv_bigm`） | 無 |
| 上傳模組 | `postgresql_uploader.py`（即時）、`cloud_embed.py` + `bulk_load.py`（全量）| `qdrant_uploader.py` + `embedding.py` |
| 批次驅動 | `pg_batch.py`（PGCheckpoint 續傳） | `pipeline.py --upload` |

> **同一 chunk 在兩路嵌入的模型不同**（Qwen3-4B vs OpenAI/bge-m3），向量空間不可互通。這是已知狀態，是否收斂為另案。

### PostgreSQL Schema（主路徑）

實作於 `postgresql_uploader.py`（取代 `qdrant_uploader.py` + `VaultStorage`）。兩張表：

```sql
-- 文章級 metadata + 原文
articles (
    id            SERIAL PRIMARY KEY,
    url           TEXT UNIQUE,        -- ON CONFLICT (url) DO UPDATE 冪等
    title         TEXT,
    author        TEXT,
    source        TEXT,
    date_published TIMESTAMPTZ,
    content       TEXT,               -- 完整原文（取代 Vault）
    metadata      JSONB               -- keywords / publisher / raw_schema_json[:500]
)

-- chunk 級 embedding + 全文索引
chunks (
    id          SERIAL PRIMARY KEY,
    article_id  INT REFERENCES articles(id),
    chunk_index INT,
    chunk_text  TEXT,
    embedding   vector(1024),         -- pgvector，Qwen3-4B INT8
    tsv         TEXT                  -- = chunk_text，供 pg_bigm BM25 檢索
    -- UNIQUE (article_id, chunk_index)：ON CONFLICT DO UPDATE 冪等
)
```

- **去重兩層**：URL 層 `ON CONFLICT (url)`；同篇不同 URL 由 `(title, source)` title dedup（`postgresql_uploader.py` `_insert_article`）。
- **向量索引**：pgvector IVFFlat（`idx_chunks_embedding_ivf`）。
- **BM25 / 中文全文**：pg_bigm（bigram）建在 `tsv` 上（`idx_chunks_tsv_bigm`）；`tsv` 內容直接等於 `chunk_text`（不含 overlap）。

### Embedding 模型（主路徑）

| 項目 | 值 |
|------|-----|
| 模型 | `Qwen/Qwen3-Embedding-4B` |
| 量化 | INT8（`BitsAndBytesConfig(load_in_8bit=True)`） |
| 維度 | 1024（`truncate_dim=1024`） |
| 載入 | lazy singleton，首次載入約 35 秒 |
| 執行 | 本機 / GCP L4 VM GPU（sentence-transformers） |

- 即時 indexing：`postgresql_uploader.py` 的 `_get_embedding_model()` / `_embed_texts()`。
- 全量 indexing：`cloud_embed.py` 在 GCP L4 VM 上跑同一模型，輸出 `.jsonl` + `.npy`，由 `bulk_load.py` 灌入 VPS PG（詳見 `bulk-load-spec.md`）。

### PGCheckpoint（斷點續傳格式）

PG indexing 由 `pg_batch.py` 驅動，checkpoint 檔為 `<tsv>.pg_checkpoint.json`，由 `PGCheckpoint` dataclass 序列化（JSON）：

```python
@dataclass
class PGCheckpoint:
    tsv_path: str                       # 來源 TSV 路徑
    processed_urls: set[str]            # 已處理 URL（序列化為 list）
    failed_urls: dict[str, str]         # URL → 失敗原因
    started_at: str                     # ISO timestamp
    updated_at: str                     # ISO timestamp
```

- **原子寫入**：先寫 `.tmp` 再 `replace()`，避免中斷時 checkpoint 損毀。
- **儲存頻率**：每 `CHECKPOINT_INTERVAL = 10` 篇存一次，KeyboardInterrupt / 例外時也會強制存。
- **載入容錯**：JSON 損毀或缺 key 時 `load()` 回 None（log warning），視為重新開始。
- 全量 batch 另用 `.pg_indexing_done`（每行一個完成的 TSV basename）追蹤檔案級進度；`cloud_embed.py` 用 `.done` 同義機制。

### GPU 溫度保護（Thermal Protection）

embedding 為本機 GPU 推論，`postgresql_uploader.py` 內建溫度保護避免長跑過熱降頻：

- 透過 `nvidia-smi --query-gpu=temperature.gpu` 讀 GPU 溫度（`_get_gpu_temp()`）。
- 每 `EMBED_BLOCK_SIZE = 50` 筆文字檢查一次溫度（`_wait_for_gpu_cooldown()`）。
- 超過 `GPU_TEMP_LIMIT = 78°C` 暫停，每 15 秒輪詢，降到 `GPU_TEMP_RESUME = 70°C` 才恢復。
- 讀不到溫度（如無 nvidia-smi）時不阻擋、log warning 後繼續（優雅降級，不 silent）。
- 其他批次常數：`EMBED_BATCH_SIZE = 8`（每次 `encode()` 文字數）、`DB_INSERT_BATCH_SIZE = 500`（每筆 DB transaction 的 chunk 數）。

---

### 9. 本地 Embedding + Qdrant 上傳實作（2026-02-03，Qdrant 路徑 — 已廢除 2026-06）

> **（已廢除 2026-06）** 本節描述的 Qdrant 上傳路徑（`qdrant_uploader.py` / `qdrant_profile.py` / `indexing/embedding.py`）已整批移除，僅留作歷史記錄。主路徑（PostgreSQL + Qwen3-4B）見上方「儲存與 Embedding：雙路徑」章節。

#### 架構

```
TSV → Ingestion → QualityGate → Chunking → Vault (SQLite)
                                    ↓
                          Embedding (bge-m3 / OpenAI)
                                    ↓
                              Qdrant (Docker)
```

#### 模組（Qdrant 路徑）

| 檔案 | 說明 |
|------|------|
| `indexing/embedding.py` | 本地 Embedding 封裝（sentence-transformers） |
| `indexing/qdrant_uploader.py` | Qdrant 向量上傳邏輯（UUID5 Point ID） |

#### Embedding 模型（Qdrant 路徑）

| 環境 | 模型 | 維度 | 說明 |
|------|------|------|------|
| Production | `text-embedding-3-small`（OpenAI） | 1536 | 透過 Azure OpenAI 或 OpenAI API |
| Local Dev | `BAAI/bge-m3` | 1024 | 多語言、零 API 成本、本地推論 |

> **注意**：此表僅適用 Qdrant 路徑。主路徑（PostgreSQL）用 Qwen3-Embedding-4B INT8（1024d）— 見上方雙路徑章節。Qdrant 路徑 Production 與 Local Dev 維度不同，Qdrant Collection 需對應設定。

#### 使用方式

```bash
# 只存 Vault（不上傳 Qdrant）
python -m indexing.pipeline data.tsv --site ltn

# 上傳到 Qdrant
python -m indexing.pipeline data.tsv --site ltn --upload
```

#### Qdrant Point ID（UUID5）

Point ID 使用 UUID5 字串格式（基於 SHA-1），確保：
- **確定性**：相同 chunk_id 永遠生成相同 UUID
- **無碰撞**：128-bit SHA-1 hash，實際碰撞機率為零
- **Qdrant 原生支援**：字串型 ID，無需 int 截斷

```python
_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "nlweb.chunk")

def _generate_point_id(self, chunk_id: str) -> str:
    return str(uuid.uuid5(self._UUID_NAMESPACE, chunk_id))
```

> 歷史：原設計使用 MD5 截斷 64-bit int，存在碰撞風險。2026-02-10 遷移至 UUID5（C3 migration，詳見 `docs/c3-qdrant-point-id-migration.md`）。

#### Qdrant Collection 設定

```python
VectorParams(
    size=get_embedding_dimension(),  # 依環境自動取得（OpenAI 1536 或 bge-m3 1024）
    distance=Distance.COSINE,
)
```

#### Payload 結構

與上方「Qdrant Payload 結構 (Version 2)」一致：
- `url` = 文章 URL（非 chunk_id）
- `schema_json` = Schema.org NewsArticle metadata
- 完整欄位見 `dual_storage.py` 的 `MapPayload` dataclass

#### E2E 測試結果

```
$ python -m crawler.main --source ltn --auto-latest --count 10
Success: 3 articles

$ python -m indexing.pipeline ltn_*.tsv --site ltn --upload
Success: 3
Total chunks: 8
Qdrant vectors: 8

$ curl http://localhost:6333/collections/nlweb
{
    "status": "green",
    "points_count": 8,
    "config": {
        "vectors": {"size": 1024, "distance": "Cosine"}
    }
}
```

#### 向量搜索測試

```python
from indexing.embedding import embed_text
from qdrant_client import QdrantClient

client = QdrantClient('http://localhost:6333')
vector = embed_text('天氣').tolist()

results = client.query_points(
    collection_name='nlweb',
    query=vector,
    limit=3
)
# Returns 3 results with similarity scores
```

#### 環境變數（可選）

```bash
QDRANT_URL=http://localhost:6333    # 預設
QDRANT_API_KEY=                      # 雲端用
QDRANT_COLLECTION=nlweb              # 預設
```

---

*更新：2026-02-11（Sitemap mode 串接、MOEA curl_cffi 修正、curl_cffi fail-fast 機制、Dashboard 支援 6 種爬取模式：auto/full_scan/sitemap/list_page/retry/retry_urls）*
