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
                    if offset >= len(embeddings):
                        continue
                    emb = embeddings[offset]
                    emb_str = "[" + ",".join(f"{v:.8f}" for v in emb.tolist()) + "]"
                    chunk_rows.append((
                        article_id,
                        c["chunk_index"],
                        c["chunk_text"],
                        emb_str,
                        c["chunk_text"],  # tsv = chunk_text for pg_bigm
                    ))

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

            except Exception as e:
                logger.error(f"  Error processing {url}: {e}")
                conn.rollback()
                stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Bulk load cloud embeddings into PostgreSQL")
    parser.add_argument("results_dir", help="Directory with .jsonl + .npy files")
    parser.add_argument("--pg-dsn", default=None, help=f"PostgreSQL DSN (default: env POSTGRES_CONNECTION_STRING or {DEFAULT_DSN})")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    dsn = args.pg_dsn or os.environ.get("POSTGRES_CONNECTION_STRING", DEFAULT_DSN)

    # Find all .jsonl files with matching .npy
    pairs = []
    for jsonl in sorted(results_dir.glob("*.jsonl")):
        npy = jsonl.with_suffix(".npy")
        if npy.exists():
            pairs.append((jsonl, npy))

    logger.info("=== Bulk Load ===")
    logger.info(f"Results dir: {results_dir}")
    logger.info(f"File pairs: {len(pairs)}")

    if not pairs:
        logger.error("No .jsonl + .npy pairs found")
        return

    # Track done files
    done_file = results_dir / ".bulk_load_done"
    done_set = set()
    if done_file.exists():
        with open(done_file, encoding="utf-8") as f:
            done_set = {line.strip() for line in f if line.strip()}

    remaining = [(j, n) for j, n in pairs if j.name not in done_set]
    logger.info(f"Done: {len(done_set)}, Remaining: {len(remaining)}")

    grand = {"articles": 0, "chunks": 0, "errors": 0}

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

                with open(done_file, "a", encoding="utf-8") as f:
                    f.write(jsonl.name + "\n")

            except Exception as e:
                logger.error(f"  FATAL: {e}")
                conn.rollback()
                continue

    logger.info("=== Complete ===")
    logger.info(f"Total: {grand['articles']} articles, {grand['chunks']} chunks, "
                f"{grand['errors']} errors")


if __name__ == "__main__":
    main()
