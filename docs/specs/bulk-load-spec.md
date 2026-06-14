# Bulk Load Pipeline 規格文件

## 概述

Bulk Load Pipeline 是全量 Indexing 的最後一步：從 GCS 下載 cloud_embed.py 的輸出（`.jsonl` + `.npy` 檔案對），bulk insert 到 VPS PostgreSQL（pgvector）。

**主腳本**：`code/python/indexing/bulk_load.py`

---

## 架構總覽

全量 Indexing 完整流程如下：

```
桌機 TSV
    ↓
split -l 20000（超大 TSV 需先拆分）
    ↓
GCP L4 VM: cloud_embed.py
    → chunking（170 chars, 句號邊界）
    → Qwen3-4B embedding（1024-dim）
    → 輸出 {name}.jsonl + {name}.npy
    ↓
GCS（gs://bucket/results/）
    ↓
VPS: bulk_load.py
    → 下載 .jsonl + .npy
    → INSERT articles + chunks
    → 更新 .bulk_load_done
    ↓
PostgreSQL（nlweb DB）
```

**分工說明**：
- `cloud_embed.py`：只做 TSV → chunking → embedding → 檔案輸出，不碰 DB
- `bulk_load.py`：只做檔案讀取 → DB INSERT，不做 embedding

---

## bulk_load.py 核心流程

### 輸入格式

每個批次由一對檔案組成：

| 檔案 | 格式 | 說明 |
|------|------|------|
| `{name}.jsonl` | JSON Lines | 每行一篇文章：url, title, author, source, date_published, content, metadata, chunks |
| `{name}.npy` | NumPy binary | shape: `(N, 1024)`，float32，每列對應一個 chunk 的 embedding |

`.jsonl` 中每個 chunk 物件包含：
```json
{
  "chunk_index": 0,
  "chunk_text": "...",
  "embedding_offset": 0
}
```
`embedding_offset` 是該 chunk 在 `.npy` 陣列中的列索引。

### 主流程

```python
1. 掃描 results_dir/ 下所有 .jsonl + .npy 配對
2. 讀取 .bulk_load_done 跳過已完成的檔案
3. 逐對處理（load_file_pair）：
   a. np.load(npy_path, mmap_mode='r')  — memory-mapped，不一次載入 RAM
   b. 逐行讀取 .jsonl
   c. INSERT INTO articles ... ON CONFLICT (url) DO UPDATE
   d. 組建 chunk_rows，每 500 筆一個 batch
   e. INSERT INTO chunks ... ON CONFLICT (article_id, chunk_index) DO UPDATE
   f. conn.commit()（每篇文章 article + chunks 一起 commit）
4. 成功後追加檔名到 .bulk_load_done
```

---

## Checkpoint 機制

### .bulk_load_done

**位置**：`{results_dir}/.bulk_load_done`

**格式**：純文字，每行一個已完成的 `.jsonl` 檔名（不含路徑）

```
cna_2025_01.jsonl
cna_2025_02.jsonl
```

**行為**：
- 啟動時讀取，建立 `done_set`
- 每對檔案成功處理後，追加到檔案
- 重新執行時，已在 `done_set` 的檔案直接跳過
- 即使部分文章有 errors，只要 `load_file_pair` 未 raise，該批次視為完成

### .pg_indexing_done（舊機制，已棄用）

舊版 `pg_batch.py` 使用 `.pg_indexing_done` 和 `<tsv>.pg_checkpoint.json`，現已被 `bulk_load.py` 的 `.bulk_load_done` 取代。舊檔案可能仍存在於 crawled/ 目錄，但 `bulk_load.py` 不讀取它們。

---

## DB Schema

### articles 表

```sql
CREATE TABLE articles (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT UNIQUE NOT NULL,
    title       TEXT,
    author      TEXT,
    source      TEXT,
    date_published TIMESTAMPTZ,
    content     TEXT,
    metadata    JSONB
);
```

ON CONFLICT 策略：`ON CONFLICT (url) DO UPDATE SET title, author, source, date_published, content, metadata = EXCLUDED.*`

### chunks 表

```sql
CREATE TABLE chunks (
    id          BIGSERIAL PRIMARY KEY,
    article_id  BIGINT REFERENCES articles(id),
    chunk_index INT,
    chunk_text  TEXT,
    embedding   vector(1024),  -- pgvector
    tsv         TEXT,          -- pg_bigm 全文索引用（= chunk_text）
    UNIQUE (article_id, chunk_index)
);
```

ON CONFLICT 策略：`ON CONFLICT (article_id, chunk_index) DO UPDATE SET chunk_text, embedding, tsv = EXCLUDED.*`

**重要**：`tsv` 欄位目前直接儲存 `chunk_text`（供 pg_bigm 索引），不是 PostgreSQL 原生 `tsvector` 型別。

### Embedding 格式

embedding 以字串形式傳入 psycopg，再 CAST 為 pgvector：

```python
emb_str = "[" + ",".join(f"{v:.8f}" for v in emb.tolist()) + "]"
# SQL: %s::vector
```

---

## OOM 防護策略

### 大 TSV 拆分（cloud_embed.py 端）

超大 TSV（> 100,000 行）必須在送進 `cloud_embed.py` 之前先拆分：

```bash
split -l 20000 large.tsv chunk_
```

**原因**：`bitsandbytes` INT8 量化有 VRAM leak，sub-batching 無法解決。拆分後每批 20,000 行獨立處理，VRAM 可正確釋放。

### mmap_mode='r'（bulk_load.py 端）

```python
embeddings = np.load(npy_path, mmap_mode='r')
```

使用 memory-mapped I/O 載入 `.npy`。大型檔案（例如 20,000 chunks × 1024 dim × 4 bytes ≈ 80MB）不會一次複製到 RAM，OS 按需載入分頁。

### Batch Insert（500 chunks/transaction）

```python
CHUNK_INSERT_BATCH = 500
for i in range(0, len(chunk_rows), CHUNK_INSERT_BATCH):
    batch = chunk_rows[i:i + CHUNK_INSERT_BATCH]
    cur.executemany(chunk_sql, batch)
```

避免單篇超長文章（大量 chunks）產生過大的單一 transaction。

---

## Transaction 邊界與錯誤處理

### 成功路徑

```
INSERT article → RETURNING id
→ 組建 chunk_rows
→ executemany chunks (batched)
→ conn.commit()   ← article + 所有 chunks 在同一 transaction
```

### 錯誤路徑（文章層級）

```python
except Exception as e:
    logger.error(f"  Error processing {url}: {e}")
    conn.rollback()
    stats["errors"] += 1
    continue  # 跳至下一篇文章
```

單篇文章失敗不影響其他文章。

### 錯誤路徑（檔案對層級）

```python
except Exception as e:
    logger.error(f"  FATAL: {e}")
    conn.rollback()
    continue  # 跳至下一個 .jsonl/.npy 對
```

`load_file_pair` 本身拋出（例如 embedding 維度不符、檔案損壞）時，整個檔案對被跳過，**不寫入 `.bulk_load_done`**，下次重跑會重試。

### JSON 解碼錯誤

```python
except json.JSONDecodeError:
    stats["errors"] += 1
    continue  # 跳過該行，繼續下一篇
```

---

## 監控與進度追蹤

### 啟動時摘要

```
=== Bulk Load ===
Results dir: /path/to/results
File pairs: 42
Done: 10, Remaining: 32
```

### 每對檔案進度

```
[11/42] cna_2025_11.jsonl
  Loaded 18432 embeddings from cna_2025_11.npy
  OK: 1240 articles, 18432 chunks, 3 errors (47s)
```

格式：`[已完成+目前/總計]`，含 articles 數、chunks 數、errors 數、耗時秒數。

### 最終統計

```
=== Complete ===
Total: 42000 articles, 630000 chunks, 12 errors
```

### DSN 設定

優先順序：
1. `--pg-dsn` 命令列參數
2. `POSTGRES_CONNECTION_STRING` 環境變數
3. 預設值：`postgresql://nlweb@localhost:5432/nlweb`

---

## 執行方式

```bash
# 基本用法
python bulk_load.py /path/to/gcs_results/

# 指定 DSN
python bulk_load.py /path/to/gcs_results/ --pg-dsn "postgresql://nlweb:pass@vps:5432/nlweb"

# 使用環境變數
POSTGRES_CONNECTION_STRING="postgresql://..." python bulk_load.py /path/to/results/
```

---

## 已知限制與注意事項

### Embedding 維度硬編碼

```python
if embeddings.shape[1] != 1024:
    raise ValueError(f"Expected 1024-dim embeddings, got {embeddings.shape[1]}")
```

維度固定為 1024（Qwen3-4B 輸出），如更換模型需同步修改此檢查及 DB schema。

### GCS 下載未整合

`bulk_load.py` **不**自動從 GCS 下載檔案。需先手動或以腳本將 GCS 結果同步到本機目錄，再執行 `bulk_load.py`。

### 無 Retry 機制

網路或 DB 短暫故障會導致當前檔案對被跳過（不寫入 done）。重跑腳本即可重試未完成的檔案。

### 部分成功計算

`stats["errors"]` 只計算文章層級的 rollback，不計算 JSON 解碼錯誤（另外計數）。`errors` 不為 0 但 `load_file_pair` 成功返回時，仍會寫入 `.bulk_load_done`。

### psycopg v3

使用 `psycopg`（v3），非舊版 `psycopg2`。連線為同步（非 async），適合批次腳本使用場景。
