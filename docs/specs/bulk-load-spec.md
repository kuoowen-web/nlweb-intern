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

### 主流程（批次寫入，2026-07-23 改造）

跨洲寫入（GPU embed VM 新加坡 → prod PG 芬蘭 RTT ~200ms）逐篇 commit 每篇 ≥2 次
round-trip。改為每 `COMMIT_BATCH_SIZE`（300，可調 200-500）篇一批：

```python
1. 掃描 results_dir/ 下所有 .jsonl + .npy 配對
2. 讀取 .bulk_load_done 跳過已完成的檔案（load_file_pair）：
   a. np.load(npy_path, mmap_mode='r')  — memory-mapped
   b. 逐行讀取 .jsonl，parse 階段隔離壞資料（JSONDecodeError / 缺 url / zero-chunk
      → errors+1、不進 batch，不毒化批次 transaction）
   c. 累積至 COMMIT_BATCH_SIZE 篇 → _flush_batch（快路徑）：
      - _dedup_batch：批次內 url 去重保留最後一筆（防 ON CONFLICT DO UPDATE
        cardinality violation；快/降級路徑共用 helper 統一 stats 計數）
      - 多列 INSERT INTO articles VALUES (...),(...) ON CONFLICT (url) DO UPDATE
        RETURNING url, id → 建 url→id map（不依賴多列 RETURNING 順序）
      - 批次 orphan 防護：DELETE FROM chunks WHERE article_id = ANY(ids) 再批次
        INSERT chunks（executemany 每 500 分片）— 全在同一 transaction
      - conn.commit()（整批一次），stats 累加必在 commit 後（errors gate 依賴）
   d. 批次 DB 失敗 → rollback → 降級 _flush_batch_per_article 逐篇重試（隔離壞篇，
      好篇 land、壞篇 errors+1）
3. errors==0 才追加檔名到 .bulk_load_done（有 errors 不寫，下次重跑）
```

**連線層防護（tunnel 逾時，2026-07-23）**：`psycopg.connect` 帶 `connect_timeout=15` +
TCP keepalive（`keepalives_idle=30/interval=10/count=3`，~60s 判死半開連線）+
`options="-c statement_timeout=300s"`（**必用連線層 options 非連上後 SQL SET**——後者
綁隱式 transaction，rollback 會回滾成 0 使降級路徑防護失效）。

**wall-clock 兜底（orchestrator 層，`indexing_orchestrator.sh`）**：`timeout
--signal=TERM --kill-after=30 2400 python3 bulk_load.py`——不管內部卡在哪，超過
`BULK_LOAD_TIMEOUT_SEC`（2400s）SIGTERM 掉 → rc=124 → 該檔不記 done、下次重跑。

**效能現實**：批次化攤的是跨洲 RTT，但真瓶頸是 **PG 端 chunks 表（82GB/600萬行）的
HNSW 向量索引逐筆寫入放大**（每 chunk 更新 HNSW 圖 ~100-200ms 物理成本，攤不掉）。
indexing 寫入慢是這個 workload 的真實速度上限，接受慢 + wall-clock 兜底防無限卡。
見 auto-memory `reference_postgres_scaling_lessons`。

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
- **只有 `stats["errors"]==0` 才寫 done**（2026-07-23 收緊；原本 errors>0 也寫→含壞資料的檔被永久跳過漏資料）。errors>0 或 load_file_pair raise（BulkLoadError / wall-clock 逾時 rc=124）→ 不寫 done、下次重跑

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

JSON 解碼錯誤與文章層級 rollback 共用同一個 `stats["errors"]` 計數器（見下方「部分成功計算」）。

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

`stats["errors"]` 是單一計數器，同時涵蓋 JSON 解碼錯誤（`bulk_load.py:77`）與文章層級的 rollback（`bulk_load.py:116`、`bulk_load.py:160`）——兩者皆 `stats["errors"] += 1`，並無分開計數。`errors` 不為 0 但 `load_file_pair` 成功返回時，仍會寫入 `.bulk_load_done`。

### psycopg v3

使用 `psycopg`（v3），非舊版 `psycopg2`。連線為同步（非 async），適合批次腳本使用場景。
