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
CHUNK_INSERT_BATCH = 500  # chunks per transaction


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


def load_file_pair(jsonl_path: Path, npy_path: Path, conn) -> dict:
    """Load one .jsonl + .npy pair into PostgreSQL."""
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

    # Process articles
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                stats["errors"] += 1
                continue

            url = data["url"]
            title = data.get("title", "")
            author = data.get("author", "")
            source = data.get("source", "")
            date_published = parse_date(data.get("date_published", ""))
            content = data.get("content", "")
            metadata = json.dumps(data.get("metadata", {}), ensure_ascii=False)
            chunks = data.get("chunks", [])

            try:
                # Insert article
                result = conn.execute(
                    """
                    INSERT INTO articles (url, title, author, source, date_published, content, metadata)
                    VALUES (%(url)s, %(title)s, %(author)s, %(source)s, %(date_published)s, %(content)s, %(metadata)s)
                    ON CONFLICT (url) DO UPDATE SET
                        title = EXCLUDED.title,
                        author = EXCLUDED.author,
                        source = EXCLUDED.source,
                        date_published = EXCLUDED.date_published,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata
                    RETURNING id
                    """,
                    {
                        "url": url,
                        "title": title,
                        "author": author,
                        "source": source,
                        "date_published": date_published,
                        "content": content,
                        "metadata": metadata,
                    },
                )
                row = result.fetchone()
                if not row:
                    stats["errors"] += 1
                    conn.rollback()
                    continue
                article_id = row["id"]

                # Insert chunks with embeddings
                chunk_rows = []
                for c in chunks:
                    offset = c["embedding_offset"]
                    # out-of-range offset：不可靜默 continue（會少 chunk 卻無錯，
                    # 資料完整性問題）。當檔案級失敗，整檔不進 done、下次重跑。
                    if offset >= len(embeddings):
                        raise BulkLoadError(
                            f"embedding_offset {offset} out of range "
                            f"(npy has {len(embeddings)} embeddings) "
                            f"for url={url} chunk_index={c.get('chunk_index')}"
                        )
                    emb = embeddings[offset]
                    emb_str = "[" + ",".join(f"{v:.8f}" for v in emb.tolist()) + "]"
                    chunk_rows.append((
                        article_id,
                        c["chunk_index"],
                        c["chunk_text"],
                        emb_str,
                        c["chunk_text"],  # tsv = chunk_text for pg_bigm
                    ))

                # orphan chunks 防護（原子替換）：先刪這篇文章的所有舊 chunks 再
                # insert 新的。若新 chunk 數 < 舊 chunk 數，純 ON CONFLICT UPDATE
                # 只更新不刪 → 舊的高 index chunk 殘留 → 搜尋返回過期內容。
                # DELETE + INSERT 同一 transaction（下方 conn.commit() 一起提交），
                # 首次載入 DELETE 空集合是 no-op（正常）。
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM chunks WHERE article_id = %s", (article_id,)
                    )

                # Batch insert chunks
                chunk_sql = """
                    INSERT INTO chunks (article_id, chunk_index, chunk_text, embedding, tsv)
                    VALUES (%s, %s, %s, %s::vector, %s)
                    ON CONFLICT (article_id, chunk_index) DO UPDATE SET
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        tsv = EXCLUDED.tsv
                """
                for i in range(0, len(chunk_rows), CHUNK_INSERT_BATCH):
                    batch = chunk_rows[i:i + CHUNK_INSERT_BATCH]
                    with conn.cursor() as cur:
                        cur.executemany(chunk_sql, batch)

                # Commit article + all its chunks together
                conn.commit()

                stats["articles"] += 1
                stats["chunks"] += len(chunk_rows)

            except BulkLoadError:
                # 檔案級致命錯誤：rollback 當前文章的 partial 寫入後往外拋，
                # 讓整個檔案對視為失敗（不進 done）。不可降級成 article-level
                # error 吞掉（否則會少 chunk 卻靜默成功）。
                conn.rollback()
                raise
            except Exception as e:
                logger.error(f"  Error processing {url}: {e}")
                conn.rollback()
                stats["errors"] += 1

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

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
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
