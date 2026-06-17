# Private Documents 規格文件

> **Owner**: 讀豹 Team
> **Last Updated**: 2026-04-27
> **Status**: 完成（Qdrant → PostgreSQL 遷移後已上線；pgvector cosine search 運作中）

---

## 目的與範圍

Private Documents 系統允許已登入使用者上傳個人文件（PDF、DOCX、TXT、MD），系統將文件 chunking + embedding 後存入 PostgreSQL，並在搜尋時與公開新聞結果合併回傳。

**核心價值**：讓使用者在公開新聞搜尋之外，能同時查詢自己的私人知識庫（研究報告、內部文件等）。

**範圍**：
- 上傳處理：`core/user_data_processor.py`
- 向量儲存與搜尋：`retrieval_providers/user_postgres_provider.py`
- 搜尋整合：`core/user_data_retriever.py`
- 設定檔：`config/user_data.yaml`
- DB Schema：`infra/init.sql`

---

## 架構總覽

```
使用者上傳文件
      │
      ▼
user_data_manager（驗證 + 儲存原始檔）
      │
      ▼
UserDataProcessor.process_file()
      │
  ┌───┼───────────────────────────────┐
  │   │                               │
  ▼   ▼                               ▼
[1] 解析文件       [2] Chunking        [3] 建立 doc 記錄
parse_file()      chunk_text()        create_document_record()
      │
      ▼
[4] Embedding + 寫入 PG
_index_chunks()
  ├─ get_embedding()（Qwen3-Embedding-4B，1024 維）
  └─ UserPostgresProvider.insert_chunks()
              │
              ▼
        user_document_chunks（PostgreSQL + pgvector）

搜尋時：
  query → get_embedding() → pgvector cosine <=> → top-k 結果
  └─ baseHandler 手動串接（formatted_private + items）→ 回傳給 ranking pipeline
     （註：merge_public_and_private_results() 已定義但未被呼叫，見「整合方式」段）
```

---

## DB Schema

資料表定義在 `infra/init.sql`，於容器首次啟動時自動建立。

### `user_document_chunks` 資料表

```sql
CREATE TABLE IF NOT EXISTS user_document_chunks (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    TEXT NOT NULL,
    org_id     TEXT,
    source_id  TEXT NOT NULL,
    doc_id     TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    total_chunks  INTEGER NOT NULL,
    content    TEXT NOT NULL,
    metadata   JSONB DEFAULT '{}',
    embedding  vector(1024) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

| 欄位 | 說明 |
|------|------|
| `id` | UUID 主鍵，PostgreSQL 自動產生 |
| `user_id` | 使用者識別符（強制隔離過濾） |
| `org_id` | 組織識別符（企業版隔離，可 NULL） |
| `source_id` | 檔案來源識別符，對應 `user_data_manager` 的 source 記錄 |
| `doc_id` | 文件識別符，由 `create_document_record()` 生成 |
| `chunk_index` | chunk 在文件中的位置（0-based） |
| `total_chunks` | 該文件的總 chunk 數 |
| `content` | chunk 純文字內容 |
| `metadata` | JSONB，包含 chunk_index、total_chunks 及來自 parse_file() 的 file_metadata |
| `embedding` | 1024 維向量（Qwen3-Embedding-4B） |
| `created_at` | 寫入時間戳 |

### 索引

```sql
CREATE INDEX IF NOT EXISTS idx_udc_user_id   ON user_document_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_udc_source_id ON user_document_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_udc_user_org  ON user_document_chunks(user_id, org_id);
```

**注意**：目前無 IVFFlat 或 HNSW 向量索引（不同於公開 `chunks` 資料表）。私人文件數量預期較少，使用 sequential scan 即可；若日後規模擴大需另行評估是否加 vector index。

---

## Chunking 參數

設定來源：`config/user_data.yaml` → `processing` 區段，由 `user_data_manager.config` 讀取。

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `chunk_size` | 500 tokens | 每個 chunk 的目標 token 數 |
| `chunk_overlap` | 50 tokens | 相鄰 chunk 的重疊 token 數 |
| `max_text_length` | 1,000,000 字元 | 解析後文字長度上限（防濫用） |
| `processing_timeout` | 300 秒 | 處理逾時時間 |

**實作細節**（`core/chunking.py`）：
- 優先使用 tiktoken（`cl100k_base` encoding）進行 token-aware 切割
- 若 tiktoken 不可用，fallback 至字元模式（1 token ≈ 4 字元）
- 字元模式下嘗試在句子邊界（`. ` / `。` / `! ` / `? ` / `\n\n`）切割，取最後 20% 範圍內最接近的邊界
- `chunk_overlap >= chunk_size` 時自動收斂為 `chunk_size - 1`（防止無限迴圈）

### 與主線 Indexing 的異同

| 面向 | Private Docs | 主線 Indexing（新聞） |
|------|-------------|----------------------|
| chunk_size | 500 tokens | 見 `indexing/chunking_engine.py` |
| embedding model | Qwen3-Embedding-4B（API，非本機） | Qwen3-Embedding-4B（本機 INT8 量化） |
| 向量維度 | 1024 | 1024 |
| 儲存表 | `user_document_chunks` | `chunks` |
| 向量索引 | 無（sequential scan） | IVFFlat（`idx_chunks_embedding_ivf`） |
| BM25 搜尋 | 無 | 有（`pg_bigm` + `idx_chunks_tsv_bigm`） |

---

## Embedding 流程

實作在 `UserDataProcessor._get_embedding_with_retry()` 與 `_index_chunks()`。

### 單次 Embedding

呼叫 `core/embedding.get_embedding(text, timeout=60)`，由 `config_embedding.yaml` 決定 provider（production 為 `qwen3`）。

### Retry 機制

```
_get_embedding_with_retry(text, max_retries=3, base_delay=2.0, timeout=60)
  attempt 0 → 失敗（timeout/ConnectionError）→ 等待 2s
  attempt 1 → 失敗 → 等待 4s
  attempt 2 → 失敗 → 等待 8s
  attempt 3 → 失敗 → 拋出 exception（不再重試）
```

- 可重試錯誤：`asyncio.TimeoutError`、error name 含 `Timeout` 或 `ConnectionError`
- 不可重試錯誤（非網路問題）：直接拋出，不等待
- 所有 chunks 以序列方式逐一 embed（非 batch），每 10 個 chunk 記錄一次進度 log

### Embedding 後寫入 PG

`provider.insert_chunks(rows)` 以序列 INSERT 逐一寫入，所有 rows 在同一個 connection transaction 內提交（`await conn.commit()`）。

embedding 向量在傳入前轉型為 `[float(v) for v in embedding]`，以 `%s::vector` 參數化寫入 pgvector 欄位，使用 `Jsonb()` 包裝 metadata。

---

## Search 流程

### 搜尋入口

`core/user_data_retriever.search_user_documents(query, user_id, top_k, query_params, org_id)`

此函式是對 `UserPostgresProvider.search_user_documents()` 的薄封裝，負責：
- 檢查 `user_id` 非空（空則直接回傳 `[]`）
- 捕捉並 log exception，失敗時回傳 `[]`（優雅降級，但會 log exception 確保可追查）

### 向量搜尋 SQL

```sql
SELECT
    id, user_id, org_id, source_id, doc_id,
    chunk_index, total_chunks, content, metadata,
    1 - (embedding <=> %s::vector) AS score
FROM user_document_chunks
WHERE user_id = %s [AND org_id = %s] [AND source_id IN (...)]
ORDER BY embedding <=> %s::vector
LIMIT %s
```

- `<=>` 為 pgvector cosine distance operator（值越小越相似）
- `score = 1 - distance`，範圍 [0, 1]，值越大越相似
- query embedding 出現兩次：SELECT 計算 score 與 ORDER BY 排序

**注意**：目前無 cosine similarity threshold 過濾，所有結果均回傳（由 top_k 控制數量）。

### 結果格式

`_format_results()` 將 DB row 轉為下游相容格式：

| 欄位 | 說明 |
|------|------|
| `content` | chunk 純文字 |
| `source_id` | 來源 ID |
| `doc_id` | 文件 ID |
| `user_id` | 使用者 ID |
| `chunk_index` / `total_chunks` | chunk 位置資訊 |
| `metadata` | JSONB metadata dict |
| `score` | cosine similarity float |
| `url` | `private://{user_id}/{source_id}/{doc_id}`（虛擬 URL，供 analytics 用） |
| `source_type` | 固定值 `'private'` |

---

## 與主線搜尋的整合方式

> **⚠️ Spec drift 註記（2026-06-17）**：`merge_public_and_private_results()` 函式雖已定義於 `core/user_data_retriever.py`，但**目前未被任何呼叫端使用**（除本 spec 外無其他引用）。實際整合改由 `core/baseHandler.py` **手動串接**完成，下方函式描述為設計意圖，非執行路徑。實作細節見「實際整合路徑」一節。

`core/user_data_retriever.merge_public_and_private_results()` 設計上負責合併：

```python
async def merge_public_and_private_results(
    public_results, private_results, private_first=True
)
```

- `private_first=True`（預設）：私人文件結果排在公開新聞之前
- 結果為純串接（concatenation），無分數重新正規化
- 設計上合併後的 list 進入主線 ranking pipeline（LLM → XGBoost → MMR）

### 實際整合路徑（baseHandler 手動串接）

實際線上整合**不走 `merge_public_and_private_results()`**，而是在 `core/baseHandler.py` 內手動完成：

- 直接 import `search_user_documents` 與 `format_private_result_for_display`（非 merge 函式）
- 逐筆呼叫 `format_private_result_for_display()` 組出 `formatted_private` list
- 兩條路徑分別串接：
  - **Free conversation 路徑**：`self.final_retrieved_items = formatted_private`（僅私人文件）
  - **一般搜尋路徑**：`items = formatted_private + items`（私人在前手動串接公開結果，等同 `private_first=True` 行為）

亦即「private-first 純串接、無分數正規化」的行為由 baseHandler 手動 list 相加實現，`merge_public_and_private_results()` 為 dead code（保留設計參考）。`methods/generate_answer.py` 亦走相同的手動串接 pattern。

顯示格式化輔助函式 `format_private_result_for_display()` 將 chunk 轉為展示格式（title 為「私人文件（片段 N/M）」），用於需要對外呈現的場景。

---

## 安全考量

### 使用者隔離

- `search_user_documents()` 的 `WHERE user_id = %s` 為**強制**過濾條件，任何搜尋都必須帶入 `user_id`
- 若 `user_id` 為空，函式直接回傳空陣列，不執行任何 DB 查詢
- 企業版透過 `org_id` 進行額外隔離（可選）

### 認證

- 文件上傳路由（`/api/user-data/...`）由 `auth_middleware` 保護，需要有效 JWT
- `user_id` 由 server 端從 JWT 提取，不信任 client 端傳入的 user_id

### 檔案類型限制

設定於 `config/user_data.yaml`：

| 限制項目 | 值 |
|----------|-----|
| 支援副檔名 | `.pdf`、`.docx`、`.txt`、`.md` |
| 支援 MIME type | application/pdf、docx、text/plain、text/markdown |
| 單檔大小上限 | 20 MB |
| 每人總容量上限 | 100 MB |
| 每人最大檔案數 | 50 個 |
| 每小時上傳限制 | 20 次 |

MIME type 驗證由 `user_data_manager` 執行（`validate_file_type: true`）。

### 向量隔離

`user_document_chunks` 的向量索引（`idx_udc_user_id`、`idx_udc_user_org`）確保 DB-level 快速過濾，不會跨使用者掃描向量。

---

## 已知限制

1. **無 cosine threshold 過濾**：搜尋結果不設相似度下限，低相關度 chunk 也會回傳（由 top_k 和後續 ranking 控制）
2. **Sequential embedding**：chunks 逐一 embed，不支援 batch；大型文件（chunks 多）處理較慢
3. **無 IVFFlat 向量索引**：私人文件量少時效能可接受，但規模擴大後（每使用者 > 數千 chunk）搜尋速度可能下降
4. **無 BM25/全文搜尋**：不同於公開新聞，私人文件僅支援向量搜尋，無 pg_bigm 全文搜尋
5. **Merge 無分數正規化**：public + private 分數分佈可能不一致，直接串接可能導致排序偏差
6. **`format_private_result_for_display()` 包含 emoji**：title 格式為「📄 私人文件...」，不符合專案的 no-emoji 規範（應在下次修改時一併清理）
7. **原始檔案儲存為 local**：`config/user_data.yaml` 的 `storage.backend: local`，VPS 部署需確保持久化 volume；Cloud storage（Azure/S3/GCS）預留介面但未實作
8. ~~**Docker 路徑不相容**~~ → 已修復（2026-04-27）：`user_data_manager.py`、`user_file_storage.py`、`user_data_db.py` 的路徑解析已改為 env var 優先（`NLWEB_CONFIG_DIR`、`NLWEB_DATA_DIR`），Docker 部署不再因目錄層數差異而找不到 config/data
