# Analytics 系統規格

## 1. 概述

### 系統目的

Analytics 系統負責記錄每次搜尋的完整執行資料，包含 query 元資料、retrieval 結果、ranking 分數，以及使用者互動行為。資料用途有二：

1. **XGBoost 訓練資料收集**：記錄 LLM ranking 分數、retrieval 分數、使用者點擊行為，供未來訓練 XGBoost 排序模型替代 LLM ranking（預計降低 80% 成本、60% 延遲）
2. **產品分析**：查詢量、延遲、成本、點擊率等，供 dashboard 監控

### 設計原則

- **非阻斷寫入**：analytics 寫入失敗不影響搜尋主路徑。`log_query_start` 同步執行是唯一例外（確保 parent row 先寫入），其餘均走非同步 queue
- **雙資料庫支援**：本地開發用 SQLite，production 用 PostgreSQL，同一套程式碼透過環境變數切換
- **隱私保護**：client IP 以 SHA-256 + salt hash 後儲存，不保留原始 IP
- **動態 INSERT 安全性**：table 名稱與 column 名稱均有 whitelist 驗證，防止 injection

---

## 2. 架構圖

```
搜尋請求 (baseHandler.py)
    │
    ├─ log_query_start() ─── 同步寫入 ──→ queries 表（確保 FK 先建立）
    │
    ├─ [搜尋執行中]
    │       │
    │       ├─ log_retrieved_document() ─→ queue ──→ retrieved_documents 表
    │       ├─ log_ranking_score()       ─→ queue ──→ ranking_scores 表
    │       ├─ log_xgboost_scores()      ─→ queue ──→ ranking_scores 表（method='xgboost_shadow'）
    │       └─ log_mmr_score()           ─→ queue ──→ ranking_scores 表（method='mmr'）
    │
    └─ log_query_complete() ─── 同步 UPDATE ──→ queries 表（補完 latency/counts）

前端事件 (AnalyticsHandler)
    ├─ POST /api/analytics/event ────→ log_user_interaction() ──→ queue ──→ user_interactions 表
    └─ POST /api/analytics/event/batch

Tier 6 (reasoning agents)
    └─ log_tier_6_enrichment() ──→ queue ──→ tier_6_enrichment 表

使用者回饋 (api.py POST /api/feedback)
    └─ log_feedback() ──→ queue ──→ user_feedback 表

AnalyticsDB (core/analytics_db.py)
    ├─ SQLite：asyncio.to_thread 包裝同步 sqlite3
    └─ PostgreSQL：psycopg_pool.AsyncConnectionPool（min=1, max=5）

讀取 API
    ├─ AnalyticsHandler (/api/analytics/*)
    └─ RankingAnalyticsHandler (/api/ranking/*)
```

---

## 3. 元件說明

### 3.1 AnalyticsDB（`core/analytics_db.py`）

**職責**：資料庫連線管理與 SQL 執行的抽象層。

**Singleton 模式**：透過 `AnalyticsDB.get_instance()` 取得全域唯一實例，避免多個 connection pool 同時存在。

**DB 類型判斷**（優先順序由高到低）：
1. `POSTGRES_CONNECTION_STRING` 環境變數
2. `DATABASE_URL` 環境變數
3. `ANALYTICS_DATABASE_URL` 環境變數（legacy）
4. 以上均未設定 → SQLite（`data/analytics/query_logs.db`）

**PostgreSQL 連線 Pool**：
- 實作：`psycopg_pool.AsyncConnectionPool`
- 參數：`min_size=1, max_size=5`
- Lazy init：首次查詢時建立，使用 `asyncio.Lock` 確保 thread safety
- Placeholder 轉換：自動將 `?` 轉為 `%s`（psycopg 語法）

**SQLite 包裝**：
- 使用 `asyncio.to_thread()` 包裝同步 `sqlite3` 呼叫，避免 blocking event loop
- 每次查詢建立新連線（無 pool）

**公開 Async API**：
- `fetchone(query, params)` → `Optional[Dict]`
- `fetchall(query, params)` → `List[Dict]`
- `execute(query, params)` → 無回傳（INSERT/UPDATE/DELETE）

**Lifecycle（由 `aiohttp_server.py` 管理）**：
- `on_startup`：呼叫 `AnalyticsDB.get_instance().initialize()`，建立所有表（CREATE IF NOT EXISTS）
- `on_cleanup`：呼叫 `analytics_db.close()`，關閉 connection pool

**Schema 來源**：`analytics_db.py` 與 `query_logger.py` 均從 `core/schema_definitions.py` import schema（Single Source of Truth）。兩者使用同一套 Schema v2 定義。

**已廢棄的同步介面**（保留向後相容）：
- `connect()` → 同步連線物件
- `execute(conn, query, params)` → 同步執行
- `executemany(conn, query, params_list)` → 批次同步執行
- `adapt_query(query)` → ? 轉 %s

---

### 3.2 QueryLogger（`core/query_logger.py`）

**職責**：搜尋流程中的 analytics 寫入入口。採用 singleton 模式（`get_query_logger()`）。

**寫入架構**：
- 後台 worker thread（daemon thread）監聽 `Queue`，消費寫入請求
- 寫入失敗會以指數退避重試（最多 5 次，延遲 0.5s → 1s → 2s → 4s → 8s）
- Foreign key constraint 錯誤觸發重試（等待 parent row 寫入完成）

**安全性**：
- `ALLOWED_TABLES`：table 名稱 whitelist（8 個表）
- `ALLOWED_COLUMNS`：column 名稱 whitelist（防止 SQL injection）

**Schema 版本管理**：
- `_check_schema_migration_needed()`：檢查是否需要從 v1 遷移至 v2
- `_migrate_schema_v2()`：ALTER TABLE 新增 v2 ML 欄位（idempotent）
- `_ensure_org_id_column()`：確保 `org_id` 欄位存在（P2 預備）

**寫入 Methods**：

| Method | 觸發時機 | 寫入方式 | 目標表 |
|--------|----------|----------|--------|
| `log_query_start()` | 搜尋開始，`baseHandler.run()` 最前端 | **同步**（確保 FK parent 先存在） | `queries` |
| `log_query_complete()` | 搜尋完成或錯誤時 | 同步 UPDATE | `queries` |
| `log_retrieved_document()` | Retrieval 完成，每個 document 各一次 | 非同步 queue | `retrieved_documents` |
| `log_ranking_score()` | LLM ranking 完成 | 非同步 queue | `ranking_scores`（method='llm'） |
| `log_xgboost_scores()` | XGBoost shadow mode 計算完成 | 非同步 queue | `ranking_scores`（method='xgboost_shadow'） |
| `log_mmr_score()` | MMR re-ranking 完成 | 非同步 queue | `ranking_scores`（method='mmr'） |
| `log_user_interaction()` | 前端使用者點擊/停留事件 | 非同步 queue | `user_interactions` |
| `log_tier_6_enrichment()` | Tier 6 API 呼叫完成 | 非同步 queue | `tier_6_enrichment` |
| `log_feedback()` | POST /api/feedback | 非同步 queue | `user_feedback` |

**`log_query_start()` 參數**：

| 參數 | 型別 | 說明 |
|------|------|------|
| `query_id` | str | `f"query_{int(time.time() * 1000)}"` 格式 |
| `user_id` | str | 使用者 ID，無則 "anonymous" |
| `query_text` | str | 原始查詢文字 |
| `site` | str | 查詢的 site |
| `mode` | str | list / generate / summarize |
| `decontextualized_query` | str | 去脈絡化後的查詢 |
| `session_id` | str | Session ID |
| `conversation_id` | str | 對話 ID |
| `model` | str | 使用的 LLM model |
| `parent_query_id` | str | 父查詢 ID（generate 跟隨 summarize 時使用） |
| `org_id` | str | 組織 ID（B2B analytics） |

**`log_query_complete()` 參數**：

| 參數 | 型別 | 說明 |
|------|------|------|
| `query_id` | str | 對應的 query ID |
| `latency_total_ms` | float | 總延遲（ms） |
| `latency_retrieval_ms` | float | Retrieval 階段延遲 |
| `latency_ranking_ms` | float | Ranking 階段延遲 |
| `latency_generation_ms` | float | Generation 階段延遲 |
| `num_results_retrieved` | int | Retrieval 數量 |
| `num_results_ranked` | int | Ranking 後數量 |
| `num_results_returned` | int | 回傳給使用者的數量 |
| `cost_usd` | float | 估計 LLM 費用 |
| `error_occurred` | bool | 是否發生錯誤 |
| `error_message` | str | 錯誤訊息 |

**`async get_query_stats(days=7)`**：讀取近 N 天統計，回傳 total_queries, avg_latency_ms, total_cost_usd, error_rate, click_through_rate。

---

### 3.3 AnalyticsHandler（`webserver/analytics_handler.py`）

**職責**：提供 dashboard 讀取 analytics 資料的 REST API。

所有讀取均透過 `AnalyticsDB` 的 async API 執行，不阻斷 event loop。

IP 隱私保護：`_hash_ip()` 取 `X-Forwarded-For` header 或 socket peername，以 SHA-256 加鹽 hash 後截取 16 字元。鹽值由 `ANALYTICS_SALT` 環境變數設定，預設 `nlweb-analytics-salt-default`。

---

### 3.4 RankingAnalyticsHandler（`webserver/ranking_analytics_handler.py`）

**職責**：提供 ranking 管線的詳細追蹤 API，供 ranking dashboard 使用。

讀取系統設定（ranking prompt、LLM model、BM25/XGBoost/MMR 參數）以及個別 query 的管線執行細節。

---

## 4. 資料流

一次完整搜尋觸發的 analytics 寫入順序：

```
1. baseHandler.run() 開始
   └─ log_query_start()  [同步，寫入 queries 表]
   └─ asyncio.sleep(0.15)  [等待 DB commit，避免 FK race]

2. prepare() / route_query_based_on_tools()
   └─ retrieval 執行完成後，ranking.py 或 retriever.py 呼叫：
      └─ log_retrieved_document()  [非同步 queue，每個 doc 各一次]

3. ranking 完成
   └─ log_ranking_score()  [非同步 queue，method='llm']
   └─ log_xgboost_scores()  [非同步 queue，method='xgboost_shadow'，若 XGBoost 啟用]
   └─ log_mmr_score()  [非同步 queue，method='mmr'，MMR re-ranking 後]

4. 搜尋完成（PostRanking 結束）
   └─ log_query_complete()  [同步 UPDATE，補充 latency/counts/cost]

5. 使用者查看結果後（前端非同步回報）
   └─ POST /api/analytics/event 或 /api/analytics/event/batch
      └─ log_user_interaction()  [非同步 queue，click/dwell 事件]

6. Tier 6 API 呼叫（若有）
   └─ log_tier_6_enrichment()  [非同步 queue]

7. 使用者按讚/踩（選擇性）
   └─ POST /api/feedback
      └─ log_feedback()  [非同步 queue]
```

**ranking_scores 表的 Multiple INSERT 模式**：
同一個 `(query_id, doc_url)` 組合可能有多列，分別記錄不同 ranking 方法的分數：
- `ranking_method='llm'`：LLM ranking 分數
- `ranking_method='xgboost_shadow'`：XGBoost shadow mode 預測分數
- `ranking_method='mmr'`：MMR diversity 分數

---

## 5. Schema

以 `query_logger.py` Schema v2 為 source of truth。`analytics_db.py` 中另有一套較早期的精簡 schema，兩者存在差異（見各表說明）。

### 5.1 queries

主查詢記錄表。每次搜尋一列。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `query_id` | TEXT PK | VARCHAR(255) PK | `query_{epoch_ms}` 格式 |
| `timestamp` | REAL NOT NULL | DOUBLE PRECISION NOT NULL | Unix epoch（秒） |
| `user_id` | TEXT NOT NULL | VARCHAR(255) NOT NULL | 使用者 ID，無則 "anonymous" |
| `org_id` | TEXT | VARCHAR(255) | 組織 ID（B2B，nullable） |
| `session_id` | TEXT | VARCHAR(255) | Session ID |
| `conversation_id` | TEXT | VARCHAR(255) | 對話 ID |
| `query_text` | TEXT NOT NULL | TEXT NOT NULL | 原始查詢文字 |
| `decontextualized_query` | TEXT | TEXT | 去脈絡化後的查詢 |
| `site` | TEXT NOT NULL | VARCHAR(100) NOT NULL | 查詢的 site |
| `mode` | TEXT NOT NULL | VARCHAR(50) NOT NULL | list / generate / summarize |
| `model` | TEXT | VARCHAR(100) | 使用的 LLM model |
| `parent_query_id` | TEXT | VARCHAR(255) | 父查詢 ID（generate 模式） |
| `latency_total_ms` | REAL | DOUBLE PRECISION | 總延遲（ms），完成後 UPDATE |
| `latency_retrieval_ms` | REAL | DOUBLE PRECISION | Retrieval 延遲 |
| `latency_ranking_ms` | REAL | DOUBLE PRECISION | Ranking 延遲 |
| `latency_generation_ms` | REAL | DOUBLE PRECISION | Generation 延遲 |
| `num_results_retrieved` | INTEGER | INTEGER | Retrieval 數量 |
| `num_results_ranked` | INTEGER | INTEGER | Ranking 後數量 |
| `num_results_returned` | INTEGER | INTEGER | 回傳數量 |
| `cost_usd` | REAL | DOUBLE PRECISION | 估計 LLM 費用 |
| `error_occurred` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 0/1 |
| `error_message` | TEXT | TEXT | 錯誤訊息 |
| `query_length_words` | INTEGER | INTEGER | 查詢字數（v2 ML 欄位） |
| `query_length_chars` | INTEGER | INTEGER | 查詢字元數（v2 ML 欄位） |
| `has_temporal_indicator` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 是否含時間詞（v2 ML 欄位） |
| `embedding_model` | TEXT | VARCHAR(100) | 使用的 embedding 模型（v2 ML 欄位） |
| `schema_version` | INTEGER DEFAULT 2 | INTEGER DEFAULT 2 | Schema 版本 |

**Indexes**：`timestamp`, `user_id`, `mode`, `org_id`

**database-spec.md 差異**：database-spec 未列出 `conversation_id`, `model`, `parent_query_id`, `org_id`, `error_message` 及所有 v2 ML 欄位（`query_length_*`, `has_temporal_indicator`, `embedding_model`, `schema_version`）。

---

### 5.2 retrieved_documents

Retrieval 階段取回的文件及其分數。每次查詢每個文件一列。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `id` | INTEGER PK AUTOINCREMENT | SERIAL PK | |
| `query_id` | TEXT NOT NULL FK | VARCHAR(255) NOT NULL FK | → queries.query_id（CASCADE DELETE） |
| `doc_url` | TEXT NOT NULL | TEXT NOT NULL | 文件 URL |
| `doc_title` | TEXT | TEXT | 文件標題 |
| `doc_description` | TEXT | TEXT | 文件摘要（最多 500 字） |
| `doc_published_date` | TEXT | VARCHAR(50) | 發佈日期 |
| `doc_author` | TEXT | VARCHAR(255) | 作者 |
| `doc_source` | TEXT | VARCHAR(255) | 來源（chinatimes 等） |
| `retrieval_position` | INTEGER NOT NULL | INTEGER NOT NULL | Retrieval 排序位置 |
| `vector_similarity_score` | REAL | DOUBLE PRECISION | 向量相似度 |
| `keyword_boost_score` | REAL | DOUBLE PRECISION | 關鍵字 boost 分數 |
| `bm25_score` | REAL | DOUBLE PRECISION | BM25 分數 |
| `temporal_boost` | REAL | DOUBLE PRECISION | 時間 boost |
| `domain_match` | INTEGER | INTEGER | 0/1，domain 是否匹配 |
| `final_retrieval_score` | REAL | DOUBLE PRECISION | 綜合 retrieval 分數 |
| `query_term_count` | INTEGER | INTEGER | 查詢詞數量（v2 ML 欄位） |
| `doc_length` | INTEGER | INTEGER | 文件長度（v2 ML 欄位） |
| `title_exact_match` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 標題精確匹配（v2 ML 欄位） |
| `desc_exact_match` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 摘要精確匹配（v2 ML 欄位） |
| `keyword_overlap_ratio` | REAL | DOUBLE PRECISION | 關鍵字重疊比例（v2 ML 欄位） |
| `recency_days` | INTEGER | INTEGER | 距今天數（v2 ML 欄位） |
| `has_author` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 是否有作者（v2 ML 欄位） |
| `retrieval_algorithm` | TEXT | VARCHAR(50) | 使用的 retrieval 演算法（v2 ML 欄位） |
| `schema_version` | INTEGER DEFAULT 2 | INTEGER DEFAULT 2 | Schema 版本 |

**Indexes**：`query_id`

**database-spec.md 差異**：database-spec 使用不同欄位名稱（`doc_snippet`, `keyword_boost_score` 無 bm25/temporal 細項）。實際程式碼欄位更豐富。

---

### 5.3 ranking_scores

Ranking 階段的各項分數。同一 `(query_id, doc_url)` 可有多列（不同 `ranking_method`）。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `id` | INTEGER PK AUTOINCREMENT | SERIAL PK | |
| `query_id` | TEXT NOT NULL FK | VARCHAR(255) NOT NULL FK | → queries.query_id（CASCADE DELETE） |
| `doc_url` | TEXT NOT NULL | TEXT NOT NULL | 文件 URL |
| `ranking_position` | INTEGER NOT NULL | INTEGER NOT NULL | 排序後位置 |
| `llm_final_score` | REAL | DOUBLE PRECISION | LLM 綜合分數（已合併為單一分數，原 llm_relevance_score, llm_keyword_score, llm_semantic_score, llm_freshness_score, llm_authority_score 五個子分數已移除） |
| `llm_snippet` | TEXT | TEXT | LLM 生成的摘要片段（最多 200 字） |
| `xgboost_score` | REAL | DOUBLE PRECISION | XGBoost 預測分數 |
| `xgboost_confidence` | REAL | DOUBLE PRECISION | XGBoost 信心值 |
| `mmr_diversity_score` | REAL | DOUBLE PRECISION | MMR diversity 分數 |
| `final_ranking_score` | REAL | DOUBLE PRECISION | 最終排序分數 |
| `ranking_method` | TEXT | VARCHAR(50) | llm / xgboost_shadow / mmr |
| `relative_score` | REAL | DOUBLE PRECISION | 相對分數（v2 ML 欄位） |
| `score_percentile` | REAL | DOUBLE PRECISION | 分數百分位（v2 ML 欄位） |
| `schema_version` | INTEGER DEFAULT 2 | INTEGER DEFAULT 2 | Schema 版本 |

**Indexes**：`query_id`

**database-spec.md 差異（🪦 已失效，2026-07-10 校正）**：本段指涉的是**已被 supersede 的舊版** database-spec（2026-03-12 版）——現行 database-spec（2026-05-13 全面重寫）不再列 analytics 欄位、直接轉指本 spec，舊欄名（`llm_score` / `text_search_score` 等）零命中。`bm25_score` → `text_search_score` rename（P3）仍未執行（analytics DB 中 `bm25_score` 欄仍存，現裝 pg_bigm similarity，見 `docs/archive/specs/bm25-spec.md` 檔頭註）。

---

### 5.4 user_interactions

使用者與搜尋結果的互動行為。每次互動一列。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `id` | INTEGER PK AUTOINCREMENT | SERIAL PK | |
| `query_id` | TEXT NOT NULL FK | VARCHAR(255) NOT NULL FK | → queries.query_id（CASCADE DELETE） |
| `doc_url` | TEXT NOT NULL | TEXT NOT NULL | 文件 URL |
| `interaction_type` | TEXT NOT NULL | VARCHAR(50) NOT NULL | click / dwell / scroll 等 |
| `interaction_timestamp` | REAL NOT NULL | DOUBLE PRECISION NOT NULL | Unix epoch（秒） |
| `result_position` | INTEGER | INTEGER | 結果顯示位置 |
| `dwell_time_ms` | REAL | DOUBLE PRECISION | 停留時間（ms） |
| `scroll_depth_percent` | REAL | DOUBLE PRECISION | 捲動深度百分比 |
| `clicked` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 0/1 |
| `client_user_agent` | TEXT | TEXT | User Agent |
| `client_ip_hash` | TEXT | VARCHAR(255) | SHA-256 hash 後的 IP（16 字元） |
| `schema_version` | INTEGER DEFAULT 2 | INTEGER DEFAULT 2 | Schema 版本 |
| `user_id` | TEXT | VARCHAR(255) | 使用者 ID（B2B analytics，nullable） |
| `org_id` | TEXT | VARCHAR(255) | 組織 ID（B2B analytics，nullable） |

**Indexes**：`query_id`, `doc_url`, `user_id`, `org_id`

**database-spec.md 差異**：database-spec 的欄位名稱與型別不同（`timestamp` vs `interaction_timestamp`，`position` vs `result_position`，`interaction_metadata` 不存在於 v2 schema）。

---

### 5.5 tier_6_enrichment

Tier 6 外部 API 呼叫記錄（Stock/Weather/Wikipedia 等）。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `id` | INTEGER PK AUTOINCREMENT | SERIAL PK | |
| `query_id` | TEXT NOT NULL FK | VARCHAR(255) NOT NULL FK | → queries.query_id（CASCADE DELETE） |
| `source_type` | TEXT NOT NULL | VARCHAR(50) NOT NULL | google_search / wikipedia / llm_knowledge 等 |
| `cache_hit` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 0/1 |
| `latency_ms` | INTEGER | INTEGER | API 延遲（ms） |
| `timeout_occurred` | INTEGER DEFAULT 0 | INTEGER DEFAULT 0 | 0/1 |
| `result_count` | INTEGER | INTEGER | 回傳結果數量 |
| `timestamp` | REAL NOT NULL | DOUBLE PRECISION NOT NULL | Unix epoch（秒） |
| `metadata` | TEXT | TEXT | JSON 字串，額外資訊 |
| `schema_version` | INTEGER DEFAULT 2 | INTEGER DEFAULT 2 | 固定為 2 |

**Indexes**：`query_id`, `source_type`

---

### 5.6 feature_vectors

XGBoost 訓練用的預計算 feature vector。schema 定義於 `core/schema_definitions.py`（SSoT），SQLite 與 PostgreSQL 皆建立。

表共 **45 欄**（不含 `id`），分為以下幾類（欄名以 `schema_definitions.py:190-240` SQLite 定義為準）：

- **識別/時間欄**：`query_id`, `doc_url`, `created_at`, `schema_version`
- **Query features**：`query_length_chars`, `query_length_words`, `has_quotes`, `has_numbers`, `has_question_words`, `keyword_count`, `query_type`, `has_temporal_indicator`, `has_brand_mention`, `detected_intent`
- **Document features**：`doc_length_words`, `doc_length_chars`, `recency_days`, `has_author`, `has_publication_date`, `schema_completeness`, `title_length`, `description_length`, `url_length`, `domain_authority`
- **Query-document features**：`vector_similarity_score`, `bm25_score`, `keyword_boost`, `temporal_boost`, `final_retrieval_score`, `keyword_overlap_ratio`, `title_exact_match`, `query_term_coverage`, `domain_match`, `entity_match_count`, `partial_match_count`
- **Ranking features**：`retrieval_position`, `ranking_position`, `llm_final_score`, `relative_score_to_top`, `score_percentile`, `position_change`, `mmr_diversity_score`
- **Labels**：`clicked`, `dwell_time_ms`, `relevance_grade`

> 注意：欄名以實際 schema 為準。例如 query-document 相似度欄名為 `vector_similarity_score`（非 `vector_similarity`）、ranking LLM 分數欄名為 `llm_final_score`（非 `llm_score`）。

**目前狀態**：表結構已定義，但填充邏輯（`populate_feature_vectors()`）尚未實作（見 xgboost-spec.md Phase B）。

**Indexes**：`query_id`, `doc_url`, `clicked`

---

### 5.7 user_feedback（僅存在於 query_logger.py schema）

使用者對搜尋結果的主觀評價（讚/踩）。`analytics_db.py` 的 schema 中不存在此表。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `id` | INTEGER PK AUTOINCREMENT | SERIAL PK | |
| `query` | TEXT | TEXT | 對應的搜尋查詢 |
| `answer_snippet` | TEXT | TEXT | 答案前 500 字 |
| `rating` | TEXT NOT NULL | VARCHAR(20) NOT NULL | 'positive' / 'negative' |
| `comment` | TEXT | TEXT | 使用者評論（最多 2000 字） |
| `session_id` | TEXT | VARCHAR(255) | Session ID |
| `created_at` | REAL NOT NULL | DOUBLE PRECISION NOT NULL | Unix epoch（秒） |
| `query_id` | TEXT | VARCHAR(255) | FK → queries.query_id（B2B，nullable） |
| `user_id` | TEXT | VARCHAR(255) | 使用者 ID（B2B analytics，nullable） |
| `org_id` | TEXT | VARCHAR(255) | 組織 ID（B2B analytics，nullable） |

**Indexes**：`created_at`, `rating`, `query_id`

---

### 5.8 guardrail_events

Guardrail（內容防護 / 濫用偵測）事件記錄。schema 定義於 `core/schema_definitions.py`（`:271-282` SQLite / `:472-483` PG），SQLite 與 PostgreSQL 皆建立。此表無 `query_id` FK（獨立事件，不掛在單次查詢下）。

| 欄位 | SQLite 型別 | PostgreSQL 型別 | 說明 |
|------|-------------|-----------------|------|
| `id` | INTEGER PK AUTOINCREMENT | SERIAL PK | |
| `timestamp` | REAL NOT NULL | DOUBLE PRECISION NOT NULL | Unix epoch（秒） |
| `event_type` | TEXT NOT NULL | VARCHAR(100) NOT NULL | 事件類型 |
| `severity` | TEXT NOT NULL DEFAULT 'info' | VARCHAR(20) NOT NULL DEFAULT 'info' | 嚴重度 |
| `user_id` | TEXT | VARCHAR(255) | 使用者 ID（nullable） |
| `client_ip` | TEXT | VARCHAR(45) | client IP |
| `details` | TEXT | TEXT | 額外資訊（JSON 字串） |
| `schema_version` | INTEGER DEFAULT 2 | INTEGER DEFAULT 2 | Schema 版本 |

**Indexes**：`timestamp`, `event_type`, `severity`, `user_id`

---

## 6. API Endpoints

### 6.1 Analytics Endpoints（AnalyticsHandler）

所有端點目前無 auth 要求（待 P2 完成後應加 JWT 驗證）。

#### GET /api/analytics/stats

整體統計概況。

Query params：
- `days`（int, 預設 7）：回溯天數

Response：
```json
{
    "total_queries": 1234,
    "queries_per_day": 176.3,
    "avg_latency_ms": 2450.5,
    "total_cost_usd": 12.34,
    "cost_per_query": 0.01,
    "error_rate": 0.02,
    "click_through_rate": 0.35,
    "training_samples": 45678,
    "days": 7
}
```

---

#### GET /api/analytics/queries

近期查詢列表（top-level 查詢，`parent_query_id IS NULL`）。

Query params：
- `days`（int, 預設 7）：回溯天數
- `limit`（int, 預設 50）：最多回傳筆數

Response：array of：
```json
{
    "query_id": "query_1234567890000",
    "query_text": "台積電最新財報",
    "timestamp": 1710000000.0,
    "site": "all",
    "mode": "list",
    "latency_total_ms": 2300.5,
    "num_results_returned": 10,
    "cost_usd": 0.012,
    "clicks": 3,
    "ctr": 0.3
}
```

---

#### GET /api/analytics/top_clicks

點擊數最高的文件排行。

Query params：
- `days`（int, 預設 7）
- `limit`（int, 預設 20）

Response：array of：
```json
{
    "doc_url": "https://...",
    "doc_title": "...",
    "click_count": 42,
    "avg_position": 2.3,
    "avg_dwell_time": 15000.0
}
```

---

#### GET /api/analytics/export_training_data

匯出 XGBoost 訓練資料為 CSV。JOIN 4 張表（queries, retrieved_documents, ranking_scores, user_interactions）。

Query params：
- `days`（int, 預設 7）

Response：CSV 檔案（UTF-8 BOM，供 Excel 正確顯示中文），Content-Disposition 附件格式。

CSV 欄位（29 欄）：query_id, query_text, query_length_words, query_length_chars, has_temporal_indicator, doc_url, doc_title, doc_length, title_exact_match, desc_exact_match, keyword_overlap_ratio, recency_days, has_author, vector_similarity_score, keyword_boost_score, bm25_score, final_retrieval_score, retrieval_position, retrieval_algorithm, llm_final_score, relative_score, score_percentile, ranking_position, ranking_method, clicked, dwell_time_ms, mode, query_latency_ms, schema_version

---

#### POST /api/analytics/event

單一前端 analytics 事件。

Request body：
```json
{
    "type": "analytics_event",
    "event_type": "result_clicked",
    "timestamp": 1710000000,
    "data": {
        "query_id": "query_1234567890000",
        "doc_url": "https://...",
        "result_position": 2,
        "client_user_agent": "Mozilla/5.0..."
    }
}
```

支援的 `event_type`：
- `result_clicked`：寫入 click 互動，`interaction_type='click'`, `clicked=True`
- `dwell_time`：寫入停留時間，`interaction_type='dwell'`
- `query_start`：目前無操作（placeholder）
- `result_displayed`：目前無操作（placeholder）

Response：`{"status": "ok"}`

---

#### POST /api/analytics/event/batch

批次前端 analytics 事件。

Request body：
```json
{
    "events": [
        { "event_type": "result_clicked", "data": {...} },
        { "event_type": "dwell_time", "data": {...} }
    ]
}
```

Response：`{"status": "ok", "processed": 2}`

---

#### POST /api/feedback（api.py 實作）

使用者對搜尋結果的評分。

Request body：
```json
{
    "rating": "positive",
    "query": "台積電最新財報",
    "answer_snippet": "根據台積電2025年Q4財報...",
    "comment": "很有幫助",
    "session_id": "sess_..."
}
```

- `rating` 必填，必須為 `'positive'` 或 `'negative'`，否則回傳 400
- `comment` 最多 2000 字

Response：`{"status": "ok"}`

---

### 6.2 Ranking Analytics Endpoints（RankingAnalyticsHandler）

#### GET /api/ranking/config

取得目前系統設定（ranking 規則、模型、參數）。

Response：
```json
{
    "llm_config": {
        "system_prompt": "...",
        "model": "gpt-4o (high) / gpt-4o-mini (low)"
    },
    "bm25_params": {...},
    "xgboost_params": {...},
    "mmr_params": {...},
    "ranking_constants": {
        "num_results_to_send": 10,
        "early_send_threshold": 0.8
    }
}
```

---

#### GET /api/ranking/pipeline/{query_id}

取得特定查詢的管線執行細節。

Path param：`query_id`
Query param：`limit`（int, 預設 10）

Response：
```json
{
    "query": { ...queries 表資料... },
    "stats": {
        "retrieved_count": 50,
        "ranked_count": 150,
        "returned_count": 10
    },
    "top_results": [
        {
            "doc_url": "...",
            "llm_final_score": 0.85,
            "llm_snippet": "...",
            "mmr_diversity_score": 0.72,
            "xgboost_score": 0.0,
            "final_ranking_score": 0.85
        }
    ]
}
```

top_results 以 `MAX(score)` GROUP BY doc_url，取前 K 筆。

---

## 7. 環境變數

| 環境變數 | 必填 | 說明 |
|----------|------|------|
| `POSTGRES_CONNECTION_STRING` | 生產環境 | PostgreSQL 連線字串（優先），同時供 Auth/Search/Analytics 使用 |
| `DATABASE_URL` | 選填 | PostgreSQL 連線字串（次要 fallback） |
| `ANALYTICS_DATABASE_URL` | 選填 | Analytics 專用 PostgreSQL（legacy，已棄用，改用上方） |
| `ANALYTICS_DB_PATH` | 選填 | SQLite 檔案路徑（預設 `data/analytics/query_logs.db`） |
| `ANALYTICS_SALT` | 選填 | IP hash 用鹽值（預設 `nlweb-analytics-salt-default`，生產應設強隨機值） |

**注意**：`analytics_db.py` 中以 `POSTGRES_CONNECTION_STRING` → `DATABASE_URL` → `ANALYTICS_DATABASE_URL` 順序讀取。若三者均未設定，自動降級為 SQLite。

---

## 8. 已知限制與待做

### ~~P2：Login 整合~~ → 大致完成（2026-03）

- ✅ Login 系統 + B2B 已完成，前端 auth guard 強制登入，正常使用下 `user_id` / `org_id` 皆為真實 UUID
- `queries.user_id` 技術上仍接受 "anonymous"（`log_query_start()` 預設值），但 auth guard 確保前端不會發生
- `queries.org_id` 保持 nullable — 目前僅 B2B 使用者模型，未來可能開放 API 讓第三方整合至自家產品，屆時 org_id 語意會不同
- **待做**：若開放 API 模式，需重新定義 user_id / org_id 語意與 NOT NULL 策略

### ~~P3：Schema cleanup~~ → 已無實際問題

- `export_training_data` SQL 使用 `rd.bm25_score`（正確欄位），不存在 `text_search_score` 問題
- `ranking_scores` 表本就無 `bm25_score` 欄位，database-spec.md 提到的 rename 不適用
- 無需額外 cleanup

### ~~P4：DB 整合~~ → 已完成（2026-03）

- ✅ Analytics 已遷移至 VPS PostgreSQL（`POSTGRES_CONNECTION_STRING`），與 Auth / Search 共用同一個 `nlweb` database
- `ANALYTICS_DATABASE_URL` 保留為 legacy fallback，但 production 不再使用 Neon.tech

### ~~兩套 Schema 並存問題~~ → 已解決（2026-03）

- ✅ `core/schema_definitions.py` 作為 Single Source of Truth
- `analytics_db.py` 和 `query_logger.py` 均 import from `schema_definitions`
- 8 張表 + 22 個 index + ALLOWED_COLUMNS whitelist 統一管理

### Worker Thread vs Async（仍存在）

- `QueryLogger` 仍使用 `threading.Thread` + `Queue` 的 worker 模式，寫入走同步 `AnalyticsDB.connect()`（deprecated 介面）
- `AnalyticsHandler` / `RankingAnalyticsHandler` 使用 async `AnalyticsDB.fetchone/fetchall/execute`
- 兩套接口並存，QueryLogger 應遷移至 async（async queue + async write）
- **優先級低**：目前 worker thread 運作穩定，不影響功能

### E2E 測試（部分覆蓋）

- ✅ `docs/e2etest.md` 有完整 A1-C2 checklist（手動 E2E）
- ✅ `queries` 表寫入已驗證（A6：user_id, org_id, query_length, embedding_model）
- ✅ Click event 前端→後端已驗證（A11a-f：startQuery + click tracking）
- ✅ B2B 欄位存在性已驗證（C1-C2）
- ❌ `retrieved_documents` / `ranking_scores` 子表寫入 — 待全量 indexing 完成後自然驗證（A7-A9）
- ❌ `tier_6_enrichment` — checklist 未覆蓋
- ❌ Dashboard 讀取 API 無自動化 pytest（B1-B4 為手動）

### 前端 Analytics 修復（2026-03-25）

- ✅ `beforeunload` 改用 `navigator.sendBeacon()`，避免 async `flushEvents()` 在頁面關閉時來不及送出
- ✅ `/api/feedback` POST 加 `credentials: 'same-origin'`，修復 B2B user_id/org_id 為 None 的問題
- `result_displayed` 和 `query_start` 後端仍為 placeholder（`pass`），暫不實作

### XGBoost 訓練資料收集狀態

- Phase A（infrastructure）完成
- Phase B 需 500+ 點擊才能開始訓練
- `feature_vectors` 表已建立但 `populate_feature_vectors()` 尚未實作
- `ranking_scores` 中 `xgboost_shadow` 寫入路徑已接線：shadow 比較流程逐筆呼叫 `query_logger.log_xgboost_scores(...)`（`xgboost_ranker.py:398`，標 `ranking_method='xgboost_shadow'`）。實際是否有資料寫入仍取決於 XGBoost model 是否載入（需 indexed data），但 call site 已存在

---

*Created: 2026-03-16*
*Updated: 2026-07-10（Batch 2 docs review：database-spec 差異段標失效；先前實質更新 2026-03-25 前端 Analytics 修復段）*
