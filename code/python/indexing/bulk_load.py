"""
Bulk Load Script — loads cloud_embed.py output (.jsonl + .npy) into PostgreSQL.

Reads article metadata from .jsonl and embeddings from .npy,
inserts into articles + chunks tables with pgvector.

Usage:
    python bulk_load.py /path/to/results_dir [--pg-dsn DSN]

Expects pairs of files: {name}.jsonl + {name}.npy
Uses ON CONFLICT for idempotency (safe to re-run).
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_DSN = "postgresql://nlweb@localhost:5432/nlweb"
CHUNK_INSERT_BATCH = 500  # chunks per executemany 分片（單次 SQL 上限，非 commit 邊界）
COMMIT_BATCH_SIZE = 300   # 每 N 篇文章 commit 一次（攤跨洲 RTT）。200-500 皆可調
STATEMENT_TIMEOUT = "300s"  # 單條 SQL 執行上限（server-side）：限「SQL 已到 PG、PG 還活著」
                            # 時的執行時間，是 keepalive（治半開連線本身）之外的第二道——
                            # keepalive 偵測不到但 SQL 執行過久時止血。正常批次 <10s，300s
                            # 有充足餘裕；超時 → PG 拋 QueryCanceled，走 flush 降級。


class BulkLoadError(Exception):
    """檔案級（file-pair-level）致命錯誤 —— 不該被 article-level 的
    per-article rollback 吞掉。遇到這類錯誤整個檔案對視為失敗，
    不寫入 .bulk_load_done，下次重跑。

    例：embedding_offset 超出 .npy 範圍（少 chunk 卻無錯是資料完整性
    問題，必須當檔案級失敗，不可靜默跳過）。
    """


DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d",
]


def parse_date(date_str: str):
    """Parse date string to datetime, return None on failure."""
    if not date_str:
        return None
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


_ARTICLE_INSERT_SQL = """
    INSERT INTO articles (url, title, author, source, date_published, content, metadata)
    VALUES (%(url)s, %(title)s, %(author)s, %(source)s, %(date_published)s, %(content)s, %(metadata)s)
    ON CONFLICT (url) DO UPDATE SET
        title = EXCLUDED.title,
        author = EXCLUDED.author,
        source = EXCLUDED.source,
        date_published = EXCLUDED.date_published,
        content = EXCLUDED.content,
        metadata = EXCLUDED.metadata
    RETURNING url, id
"""

_CHUNK_INSERT_SQL = """
    INSERT INTO chunks (article_id, chunk_index, chunk_text, embedding, tsv)
    VALUES (%s, %s, %s, %s::vector, %s)
    ON CONFLICT (article_id, chunk_index) DO UPDATE SET
        chunk_text = EXCLUDED.chunk_text,
        embedding = EXCLUDED.embedding,
        tsv = EXCLUDED.tsv
"""


def _article_params(data: dict) -> dict:
    """從一行 jsonl dict 抽出 articles INSERT 的 named params。

    data["url"] 缺失會 KeyError —— 由呼叫端當 article-level 壞資料處理
    （errors+1、隔離），與批次原子性無關。
    """
    return {
        "url": data["url"],
        "title": data.get("title", ""),
        "author": data.get("author", ""),
        "source": data.get("source", ""),
        "date_published": parse_date(data.get("date_published", "")),
        "content": data.get("content", ""),
        "metadata": json.dumps(data.get("metadata", {}), ensure_ascii=False),
    }


def _dedup_batch(batch: list) -> list:
    """批次內 url 去重，保留最後一筆（後出現覆蓋先出現）。

    dict 保插入序、同 key 覆蓋 value → 恰好是「後出現覆蓋先出現」，符合
    ON CONFLICT DO UPDATE「最後寫入勝出」語義（較晚爬取通常較新）。

    兩條路徑共用（快路徑 _flush_batch 防 CardinalityViolation；降級路徑
    _flush_batch_per_article 統一 stats 計數——重複 url 恆算 1 篇，DB 就是 1 列）。
    抽 helper 避免兩處邏輯漂移。
    """
    deduped_map: dict = {}
    for url, ap, chunks in batch:
        deduped_map[url] = (url, ap, chunks)
    return list(deduped_map.values())


def _build_chunk_rows(url: str, chunks: list, article_id, embeddings) -> list:
    """組一篇文章的 chunk INSERT rows。

    offset 越界 raise BulkLoadError（檔案級致命）—— 不可靜默 continue
    （會少 chunk 卻無錯，資料完整性問題）。負 offset 尤其危險：numpy 會當
    「從尾端索引」→ 靜默拿到錯的 embedding（資料損壞而無錯），故也要擋。
    embeddings 保留原始存取語義（mmap 陣列，%.8f 格式化 + ::vector cast）。
    """
    rows = []
    for c in chunks:
        offset = c["embedding_offset"]
        if offset < 0 or offset >= len(embeddings):
            kind = "negative" if offset < 0 else "out of range"
            raise BulkLoadError(
                f"embedding_offset {offset} {kind} "
                f"(npy has {len(embeddings)} embeddings) "
                f"for url={url} chunk_index={c.get('chunk_index')}"
            )
        emb = embeddings[offset]
        emb_str = "[" + ",".join(f"{v:.8f}" for v in emb.tolist()) + "]"
        rows.append((
            article_id,
            c["chunk_index"],
            c["chunk_text"],
            emb_str,
            c["chunk_text"],  # tsv = chunk_text for pg_bigm
        ))
    return rows


def _insert_chunk_rows(conn, chunk_rows: list) -> None:
    """把已組好的 chunk rows 分片 executemany 插入（不 commit）。"""
    for i in range(0, len(chunk_rows), CHUNK_INSERT_BATCH):
        piece = chunk_rows[i:i + CHUNK_INSERT_BATCH]
        with conn.cursor() as cur:
            cur.executemany(_CHUNK_INSERT_SQL, piece)


def _flush_batch(conn, batch: list, embeddings, stats: dict) -> None:
    """把一批已解析好的文章（含 chunks）原子寫入並 commit 一次。

    batch 每筆是 (url, article_params, chunks_list)。

    正確性關鍵：
    - 批次原子性：整批 article upsert + orphan DELETE + chunk INSERT 在**同一
      transaction**，最後 commit 一次。這保住 orphan chunks 的 DELETE-then-INSERT
      原子替換語義（跨多篇仍成立），且把跨洲 commit RTT 從每篇一次攤到每批一次。
    - id 對應：用 `RETURNING url, id` 建 url→id map，**不依賴多列 INSERT 的
      RETURNING 順序**（Postgres 不保證多列 INSERT RETURNING 順序對應 VALUES）。
      url 是 UNIQUE NOT NULL，是可靠鍵。
    - 批次 vs 單篇隔離：整批任一步 DB 失敗 → rollback 後 raise，由呼叫端降級成
      逐篇重試（見 _flush_batch_per_article），好篇照樣 land、壞篇挑出計 error。
    - BulkLoadError（out-of-range offset）在組 chunk_rows 時就 raise → 穿透到
      load_file_pair 外，整檔失敗（不進 done）。
    - 批次內重複 url 去重（P1）：上游 crawler TSV 不保證單檔 url 唯一
      （cloud_embed.py 不去重），同一批若有兩行相同 url，多列
      `INSERT ... ON CONFLICT (url) DO UPDATE` 會 raise CardinalityViolation
      （"cannot affect row a second time"）→ 快路徑必炸強制降級、廢掉批次效能。
      故在組 params 前先去重，**保留最後一筆**（後出現的 article_params + chunks
      覆蓋先出現的，符合 ON CONFLICT DO UPDATE「最後寫入勝出」語義；較晚的通常
      是較新爬取）。去重抽成 _dedup_batch helper，快路徑（防 CardinalityViolation）
      與降級路徑（統一 stats 計數，AR R1 #1）**共用同一 helper**——重複 url 恆算
      1 篇，兩路徑計數一致，避免邏輯漂移。
    """
    if not batch:
        return

    # 批次內 url 去重（保留最後一筆，見 _dedup_batch）。防 CardinalityViolation +
    # stats 據 deduped 計數（重複 url 在 DB 就是 1 列 article，算 1 篇）。
    deduped = _dedup_batch(batch)

    # 1) 批次 upsert articles，一次 SQL 拿回全部 url→id
    placeholders = ",".join(
        ["(%(url_{0})s, %(title_{0})s, %(author_{0})s, %(source_{0})s, "
         "%(date_{0})s, %(content_{0})s, %(metadata_{0})s)".format(i)
         for i in range(len(deduped))]
    )
    params: dict = {}
    for i, (_url, ap, _chunks) in enumerate(deduped):
        params[f"url_{i}"] = ap["url"]
        params[f"title_{i}"] = ap["title"]
        params[f"author_{i}"] = ap["author"]
        params[f"source_{i}"] = ap["source"]
        params[f"date_{i}"] = ap["date_published"]
        params[f"content_{i}"] = ap["content"]
        params[f"metadata_{i}"] = ap["metadata"]

    multi_article_sql = (
        "INSERT INTO articles "
        "(url, title, author, source, date_published, content, metadata) "
        f"VALUES {placeholders} "
        "ON CONFLICT (url) DO UPDATE SET "
        "title = EXCLUDED.title, author = EXCLUDED.author, source = EXCLUDED.source, "
        "date_published = EXCLUDED.date_published, content = EXCLUDED.content, "
        "metadata = EXCLUDED.metadata "
        "RETURNING url, id"
    )
    rows = conn.execute(multi_article_sql, params).fetchall()
    url_to_id = {r["url"]: r["id"] for r in rows}

    # 2) 組全批 chunk_rows（BulkLoadError 在此可能 raise → 檔案級失敗）
    #
    # 記憶體 tradeoff（有意識取捨，AR R1 Codex+Gemini P2 標註）：all_chunk_rows 在
    # batch scope 累積整批（至多 COMMIT_BATCH_SIZE=300 篇）的所有 chunk rows，每個
    # emb_str 是 1024 維 %.8f 字串（~11KB/chunk）。峰值粗估：300 篇 × 平均 chunk 數
    # × 11KB ≈ 十幾 MB/批（**不累積跨批**，每批 flush 後釋放）。這抵銷了 embeddings
    # mmap 的省 RAM 設計，是相對「逐篇」的記憶體回歸。為何可接受：GPU embed VM RAM
    # 充足、峰值遠低於 OOM 門檻，換來的是把跨洲 commit RTT 從每篇一次攤到每批一次
    # （效能才是此改造的目標）。未來若要進一步省 RAM：把 chunk INSERT 也改成 batch
    # scope 內分批 flush（較大改動，須保住整批 orphan DELETE 的原子性），這輪不做。
    all_chunk_rows: list = []
    article_ids: list = []
    for url, _ap, chunks in deduped:
        article_id = url_to_id[url]
        article_ids.append(article_id)
        all_chunk_rows.extend(_build_chunk_rows(url, chunks, article_id, embeddings))

    # 3) orphan 防護：先刪整批這些文章的舊 chunks（原子替換），再 INSERT 新的。
    #    DELETE + INSERT 與上面的 article upsert 同一 transaction（本函式尾 commit
    #    一起提交）。首次載入 DELETE 空集合是 no-op。
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE article_id = ANY(%s)", (article_ids,))

    _insert_chunk_rows(conn, all_chunk_rows)

    # 4) 整批一次 commit
    conn.commit()

    # stats 累加**必須在 conn.commit() 之後**（P2 — errors gate 正確性的隱性依賴）。
    # 若挪到 commit 前：批次任一步 DB 失敗會 raise，此時累加已發生 → 呼叫端降級
    # 逐篇重試會「重新計數」同一批文章 → errors gate 依賴的 stats 重複計數而破
    # （errors 數與 articles/chunks 不再對應真實寫入）。commit 成功才代表這批真的
    # 落 DB，才可累加。用去重後的 deduped 計數：重複 url 算 1 篇（DB 就是 1 列）。
    stats["articles"] += len(deduped)
    stats["chunks"] += len(all_chunk_rows)


def _flush_batch_per_article(conn, batch: list, embeddings, stats: dict) -> None:
    """降級路徑：批次 commit 失敗時，逐篇重試該批。

    好篇照樣 land（各自 commit），壞篇 rollback + errors+1 隔離。這保住「一批有
    壞篇不能整批 rollback 丟好篇」的單篇隔離語義。BulkLoadError（out-of-range /
    負 offset）仍往外拋 —— 檔案級致命不可被逐篇 fallback 降級吞掉。

    入口先去重（AR R1 #1 根治 stats 雙計）：降級處理的是原始未去重 batch，若同批
    有重複 url，逐篇會對同一 url 各 commit + articles+1 兩次，但 DB 只有 1 列
    （ON CONFLICT last-writer-wins）→ stats["articles"] 高報。套與快路徑相同的
    _dedup_batch（保留最後一筆）統一計數：重複 url 恆算 1 篇。
    """
    for url, ap, chunks in _dedup_batch(batch):
        try:
            row = conn.execute(_ARTICLE_INSERT_SQL, ap).fetchone()
            if not row:
                stats["errors"] += 1
                conn.rollback()
                continue
            article_id = row["id"]

            chunk_rows = _build_chunk_rows(url, chunks, article_id, embeddings)

            # orphan 防護（單篇原子替換）
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunks WHERE article_id = %s", (article_id,))
            _insert_chunk_rows(conn, chunk_rows)

            conn.commit()
            stats["articles"] += 1
            stats["chunks"] += len(chunk_rows)
        except BulkLoadError:
            # 檔案級致命：rollback partial 寫入後往外拋，整檔失敗（不進 done）。
            conn.rollback()
            raise
        except Exception as e:
            logger.error(f"  Error processing {url}: {e}")
            conn.rollback()
            stats["errors"] += 1


def load_file_pair(jsonl_path: Path, npy_path: Path, conn) -> dict:
    """Load one .jsonl + .npy pair into PostgreSQL（批次寫入）。

    批次策略：累積至多 COMMIT_BATCH_SIZE 篇文章 → 一次批次 upsert articles +
    一次批次 orphan DELETE + 批次 chunk INSERT + 一次 commit。把跨洲 commit RTT
    從「每篇一次」攤到「每批一次」。批次 DB 失敗時降級逐篇重試（隔離壞篇）。
    """
    stats = {"articles": 0, "chunks": 0, "errors": 0}

    # Load embeddings (memory-mapped to avoid RAM spike on large files)
    embeddings = np.load(npy_path, mmap_mode='r')
    # 防呆：非 2-D array（例如 1-D）取 shape[1] 會 IndexError crash。
    # 先驗維度，給乾淨的檔案級錯誤訊息（不 silent，不裸 crash）。
    if embeddings.ndim != 2:
        raise ValueError(
            f"Expected 2-D embeddings array (N, 1024), got ndim={embeddings.ndim} "
            f"shape={embeddings.shape} in {npy_path.name}"
        )
    if embeddings.shape[1] != 1024:
        raise ValueError(f"Expected 1024-dim embeddings, got {embeddings.shape[1]}")
    logger.info(f"  Loaded {embeddings.shape[0]} embeddings from {npy_path.name}")

    def flush(batch: list) -> None:
        """提交一批：先試批次快路徑，DB 失敗則 rollback 後降級逐篇。

        BulkLoadError（檔案級致命）不在此攔 —— 讓它穿透到 load_file_pair 外。
        """
        if not batch:
            return
        try:
            _flush_batch(conn, batch, embeddings, stats)
        except BulkLoadError:
            # out-of-range offset 等檔案級致命：rollback 後往外拋，整檔失敗。
            conn.rollback()
            raise
        except Exception as e:
            # 批次 DB 失敗（例如某篇違反 constraint 毒化整批 transaction）→
            # rollback 整批後降級逐篇重試，好篇照樣 land、壞篇隔離計 error。
            logger.warning(f"  批次寫入失敗，降級逐篇重試該批（{len(batch)} 篇）：{e}")
            conn.rollback()
            _flush_batch_per_article(conn, batch, embeddings, stats)

    # Process articles（累積成批）
    batch: list = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # 壞行：解析階段就隔離，不進 batch（不會毒化批次 transaction）。
                stats["errors"] += 1
                continue

            try:
                ap = _article_params(data)  # data["url"] 缺失 → KeyError
            except (KeyError, TypeError) as e:
                # 缺 url / 結構壞：article-level 壞資料，解析階段隔離、errors+1。
                logger.error(f"  Error parsing article: {e}")
                stats["errors"] += 1
                continue

            chunks = data.get("chunks", [])
            # zero-chunk 防禦（AR R1 #3）：chunks **完全為空**當 article-level 壞資料
            # 隔離（errors+1、不進 batch）。若放行會 upsert article + DELETE 舊 chunks +
            # INSERT 零個 → 靜默內容遺失且 errors==0 寫 done。在 parse 層擋（跟「缺 url」
            # 同層），壞篇根本不進批次 → 不會走到 DELETE 既有 chunks 的路徑。
            #
            # zero-chunk vs 合法 shrink 的區別：只看**新資料 chunks 是否為空**，不看
            # DB 現存數量。len(chunks)==0 才擋；len(chunks)>=1 一律放行（含 shrink——
            # 舊 5→新 3 是合法更新，chunks 非空只是變少，由 orphan 防護的 DELETE-then-
            # INSERT 正確處理，不在此誤傷）。
            # 背景：cloud_embed.py chunk_article() 對通過 body 過濾（≥50 字 + 中文≥20%）
            # 的 article 保證 ≥1 chunk，實際不觸發；此為不賭上游的下游防禦缺口補強。
            if not chunks:
                logger.error(f"  Zero-chunk article（內容遺失風險，隔離）: {ap['url']}")
                stats["errors"] += 1
                continue

            batch.append((ap["url"], ap, chunks))

            if len(batch) >= COMMIT_BATCH_SIZE:
                flush(batch)
                batch = []

    # flush 尾批
    flush(batch)

    return stats


def main_load_dir(results_dir: str, dsn: str) -> dict:
    """掃描 results_dir 的 .jsonl+.npy 配對逐對載入 dsn 指向的 PG。

    從 main() 抽出的可測核心（main() 只負責解析 argv + 決定 dsn）。
    回傳 grand 統計 dict。

    .bulk_load_done gate：**只有 stats.errors == 0 才寫入 done**（原本 errors>0
    也寫，導致含壞資料的檔被永久跳過、漏資料）。檔案級失敗（load_file_pair
    raise，例如 out-of-range offset / 維度不符）同樣不寫 done，下次重跑。
    """
    results_dir = Path(results_dir)

    # Find all .jsonl files with matching .npy
    pairs = []
    for jsonl in sorted(results_dir.glob("*.jsonl")):
        npy = jsonl.with_suffix(".npy")
        if npy.exists():
            pairs.append((jsonl, npy))

    logger.info("=== Bulk Load ===")
    logger.info(f"Results dir: {results_dir}")
    logger.info(f"File pairs: {len(pairs)}")

    grand = {"articles": 0, "chunks": 0, "errors": 0}

    if not pairs:
        logger.error("No .jsonl + .npy pairs found")
        return grand

    # Track done files
    done_file = results_dir / ".bulk_load_done"
    done_set = set()
    if done_file.exists():
        with open(done_file, encoding="utf-8") as f:
            done_set = {line.strip() for line in f if line.strip()}

    remaining = [(j, n) for j, n in pairs if j.name not in done_set]
    logger.info(f"Done: {len(done_set)}, Remaining: {len(remaining)}")

    # tunnel 逾時防護（跨洲 SSH tunnel「半開」連線止血，實機驗證）——三道皆連線層，
    # 不動任何寫入邏輯。psycopg3 直接吃這些 libpq kwargs。
    # 1) connect_timeout：建連階段就卡死時的上限。
    # 2) TCP keepalive：治「TCP 斷了但狀態未偵測」的半開連線主力 —— idle 30s 後起探測，
    #    每 10s 一次、連 3 次無回應（~60s）判死，psycopg 拋錯而非無限等 DB 回應。
    # 3) statement_timeout：單條 SQL 上限。超時 → PG 拋 QueryCanceled，走現有 flush()
    #    降級路徑（該批 rollback → 逐篇重試 → 逐篇也超時各自 errors+1 → 該檔進 error
    #    不進 done，下次重跑）。
    #    **必須用連線參數 options 而非連上後 `SET statement_timeout`**：psycopg3 預設非
    #    autocommit，`SET`（非 SET LOCAL）會綁在隱式開啟的 transaction 上，任何
    #    conn.rollback()（降級路徑必經）會把 statement_timeout 一併回滾成 0（無限）→
    #    降級逐篇重試時防護失效、半開連線下該篇又無限等（真 PG 實測 errors 誤計 0）。
    #    options 是 libpq 連線層設定，不綁 transaction，rollback 不影響（真 PG 驗證）。
    with psycopg.connect(
        dsn,
        row_factory=dict_row,
        connect_timeout=15,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT}",
    ) as conn:
        logger.info("Connected to PostgreSQL")

        for i, (jsonl, npy) in enumerate(remaining):
            logger.info(f"[{len(done_set)+i+1}/{len(pairs)}] {jsonl.name}")
            t0 = time.time()

            try:
                stats = load_file_pair(jsonl, npy, conn)
                elapsed = time.time() - t0
                logger.info(f"  OK: {stats['articles']} articles, {stats['chunks']} chunks, "
                            f"{stats['errors']} errors ({elapsed:.0f}s)")

                for k in grand:
                    grand[k] += stats[k]

                # errors gate：僅在完全無錯時才記 done。errors>0 代表本檔有文章
                # 沒進 DB（漏資料），必須讓下次重跑，不可寫 done 永久跳過。
                if stats["errors"] == 0:
                    with open(done_file, "a", encoding="utf-8") as f:
                        f.write(jsonl.name + "\n")
                else:
                    logger.warning(
                        f"  SKIP done-mark: {jsonl.name} 有 {stats['errors']} 個 error，"
                        f"不寫入 .bulk_load_done（下次重跑）"
                    )

            except Exception as e:
                # 檔案級失敗（load_file_pair raise：BulkLoadError / 維度不符 / 檔損壞）
                # → 不寫 done，下次重跑。errors 計入 grand 讓最終統計反映失敗檔。
                logger.error(f"  FATAL: {e}")
                conn.rollback()
                grand["errors"] += 1
                continue

    logger.info("=== Complete ===")
    logger.info(f"Total: {grand['articles']} articles, {grand['chunks']} chunks, "
                f"{grand['errors']} errors")
    return grand


def main():
    parser = argparse.ArgumentParser(description="Bulk load cloud embeddings into PostgreSQL")
    parser.add_argument("results_dir", help="Directory with .jsonl + .npy files")
    parser.add_argument("--pg-dsn", default=None, help=f"PostgreSQL DSN (default: env POSTGRES_CONNECTION_STRING or {DEFAULT_DSN})")
    args = parser.parse_args()

    dsn = args.pg_dsn or os.environ.get("POSTGRES_CONNECTION_STRING", DEFAULT_DSN)
    main_load_dir(args.results_dir, dsn)


if __name__ == "__main__":
    main()
