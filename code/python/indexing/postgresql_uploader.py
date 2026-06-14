"""
postgresql_uploader.py - PostgreSQL storage module for indexing pipeline.

Replaces QdrantUploader + VaultStorage (SQLite). Writes articles and chunks
(with embeddings) directly to PostgreSQL with pgvector.

Schema:
    articles (id, url, title, author, source, date_published, content, metadata)
    chunks   (id, article_id, chunk_index, chunk_text, embedding, tsv)

Embedding model: Qwen3-Embedding-4B (INT8), 1024 dimensions.
NOTE: First model load takes ~35 seconds.
"""

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from .chunking_engine import Chunk
from .ingestion_engine import CanonicalDataModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Connection string must come from env var or constructor argument — no hardcoded default
_NO_CONN_MSG = "POSTGRES_CONNECTION_STRING env var is required (or pass connection_string to constructor)"

# Batching
EMBED_BATCH_SIZE = 8       # texts sent to model per encode() call
EMBED_BLOCK_SIZE = 50      # texts per thermal-check block (was 100, halved for better temp control)
DB_INSERT_BATCH_SIZE = 500  # chunks inserted per DB transaction

# GPU thermal protection
GPU_TEMP_LIMIT = 78        # pause embedding above this (was 83, too close to throttle point)
GPU_TEMP_RESUME = 70       # resume below this (was 75)


# ---------------------------------------------------------------------------
# GPU thermal helpers
# ---------------------------------------------------------------------------

def _get_gpu_temp() -> Optional[int]:
    """Get GPU temperature in degrees C. Returns None if unavailable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def _wait_for_gpu_cooldown() -> None:
    """Block until GPU temperature drops below GPU_TEMP_RESUME."""
    temp = _get_gpu_temp()
    if temp is None or temp <= GPU_TEMP_LIMIT:
        return

    logger.warning(f"GPU temp {temp} C exceeds {GPU_TEMP_LIMIT} C -- pausing embedding")
    while True:
        time.sleep(15)
        temp = _get_gpu_temp()
        if temp is None:
            logger.warning("Cannot read GPU temp, resuming anyway")
            return
        logger.info(f"  GPU cooling: {temp} C (resume at <={GPU_TEMP_RESUME} C)")
        if temp <= GPU_TEMP_RESUME:
            logger.info(f"GPU cooled to {temp} C -- resuming")
            return


# ---------------------------------------------------------------------------
# Embedding model (lazy-loaded singleton)
# ---------------------------------------------------------------------------

_embedding_model = None


def _get_embedding_model():
    """
    Load Qwen3-Embedding-4B with INT8 quantization (lazy, singleton).

    NOTE: First load takes ~35 seconds due to model download / quantization.
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    logger.info("Loading Qwen3-Embedding-4B (INT8) -- this takes ~35 seconds on first call...")
    t0 = time.time()

    from sentence_transformers import SentenceTransformer
    from transformers import BitsAndBytesConfig

    quantization_config = BitsAndBytesConfig(load_in_8bit=True)
    _embedding_model = SentenceTransformer(
        "Qwen/Qwen3-Embedding-4B",
        model_kwargs={"quantization_config": quantization_config},
        truncate_dim=1024,
    )

    elapsed = time.time() - t0
    logger.info(f"Embedding model loaded in {elapsed:.1f}s")
    return _embedding_model


def _embed_texts(texts: list[str]) -> np.ndarray:
    """
    Embed texts using Qwen3-Embedding-4B.

    Returns (N, 1024) float32 array. Includes GPU thermal protection
    between blocks of EMBED_BLOCK_SIZE texts.
    """
    if not texts:
        return np.empty((0, 1024), dtype=np.float32)

    model = _get_embedding_model()

    all_embeddings = []
    for i in range(0, len(texts), EMBED_BLOCK_SIZE):
        _wait_for_gpu_cooldown()

        block = texts[i:i + EMBED_BLOCK_SIZE]
        embs = model.encode(block, batch_size=EMBED_BATCH_SIZE, show_progress_bar=False)
        all_embeddings.append(embs)

    return np.vstack(all_embeddings).astype(np.float32)


# ---------------------------------------------------------------------------
# PostgreSQLUploader
# ---------------------------------------------------------------------------

class PostgreSQLUploader:
    """
    Upload articles and chunks to PostgreSQL with pgvector embeddings.

    Replaces QdrantUploader (vector search) + VaultStorage (full text in SQLite).
    PostgreSQL now stores both the article content and chunk embeddings.
    """

    def __init__(self, connection_string: str = None):
        """
        Initialize with PostgreSQL connection string.

        Args:
            connection_string: PostgreSQL DSN. Defaults to env var
                POSTGRES_CONNECTION_STRING. Raises RuntimeError if neither is set.
        """
        self.connection_string = (
            connection_string
            or os.environ.get("POSTGRES_CONNECTION_STRING")
        )
        if not self.connection_string:
            raise RuntimeError(_NO_CONN_MSG)
        self._conn = None
        self._connected = False

    def _get_connection(self):
        """Get or create database connection. Raises on failure."""
        if self._conn is not None and self._connected:
            return self._conn

        import psycopg
        from psycopg.rows import dict_row

        try:
            self._conn = psycopg.connect(self.connection_string, row_factory=dict_row, connect_timeout=5)
            self._connected = True
            logger.info(f"PostgreSQL connected: {self._mask_dsn(self.connection_string)}")
            return self._conn
        except Exception as e:
            self._connected = False
            logger.error(
                f"無法連線到 PostgreSQL。"
                f"是不是忘記開 Docker Desktop？"
            )
            logger.error(f"PostgreSQL connection failed: {e}")
            raise

    @staticmethod
    def _mask_dsn(dsn: str) -> str:
        """Mask password in DSN for safe logging."""
        import re
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", dsn)

    # ----- Article insertion -----

    def _insert_article(self, article: CanonicalDataModel, site: str) -> Optional[int]:
        """
        Insert or update an article in PostgreSQL.

        Uses ON CONFLICT (url) DO UPDATE for idempotency -- if the article
        already exists, we update its content and metadata.

        Returns:
            article_id (int) on success, None on failure.
        """
        conn = self._get_connection()

        date_published = None
        if article.date_published:
            dt = article.date_published
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_published = dt

        metadata = {
            "keywords": article.keywords or [],
            "publisher": article.publisher or "",
            "raw_schema_json": article.raw_schema_json[:500] if article.raw_schema_json else "",
        }

        try:
            # Title+source dedup: skip if same article already exists under a different URL
            title = article.headline or ""
            if title:
                existing = conn.execute(
                    "SELECT id FROM articles WHERE title = %(title)s AND source = %(source)s LIMIT 1",
                    {"title": title, "source": site},
                ).fetchone()
                if existing:
                    logger.info(f"Title dedup: '{title}' already exists (id={existing['id']}), skipping URL {article.url}")
                    return existing["id"]

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
                    "url": article.url,
                    "title": article.headline or "",
                    "author": article.author,
                    "source": site,
                    "date_published": date_published,
                    "content": article.article_body or "",
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                },
            )
            row = result.fetchone()
            conn.commit()
            return row["id"] if row else None
        except Exception as e:
            logger.error(f"Failed to insert article {article.url}: {e}")
            conn.rollback()
            return None

    # ----- Chunk insertion -----

    def _insert_chunks_batch(
        self,
        chunk_rows: list[tuple],
    ) -> int:
        """
        Batch insert chunk rows into PostgreSQL.

        Each tuple: (article_id, chunk_index, chunk_text, embedding_str, tsv_text)

        Uses ON CONFLICT (article_id, chunk_index) DO UPDATE for idempotency.

        Returns:
            Number of rows inserted/updated.
        """
        if not chunk_rows:
            return 0

        conn = self._get_connection()

        try:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO chunks (article_id, chunk_index, chunk_text, embedding, tsv)
                    VALUES (%s, %s, %s, %s::vector, %s)
                    ON CONFLICT (article_id, chunk_index) DO UPDATE SET
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        tsv = EXCLUDED.tsv
                    """,
                    chunk_rows,
                )
            conn.commit()
            return len(chunk_rows)
        except Exception as e:
            logger.error(f"Failed to insert chunk batch ({len(chunk_rows)} rows): {e}")
            conn.rollback()
            return 0

    # ----- Public API -----

    def upload_article(
        self,
        article: CanonicalDataModel,
        chunks: list[Chunk],
        site: str = "",
    ) -> bool:
        """
        Upload a single article and its chunks to PostgreSQL.

        Args:
            article: The article's canonical data model.
            chunks: Pre-chunked Chunk objects from ChunkingEngine.
            site: Source identifier (e.g. 'ltn', 'cna'). Falls back to article.source_id.

        Returns:
            True if successful, False otherwise.
        """
        site = site or article.source_id

        # 1. Insert article
        article_id = self._insert_article(article, site)
        if article_id is None:
            return False

        if not chunks:
            return True

        # 2. Embed chunk texts
        embed_texts_list = [
            c.embedding_text if c.embedding_text else c.full_text
            for c in chunks
        ]

        try:
            embeddings = _embed_texts(embed_texts_list)
        except Exception as e:
            logger.error(f"Embedding failed for article {article.url}: {e}")
            return False

        if len(embeddings) != len(chunks):
            logger.error(
                f"Embedding count mismatch: got {len(embeddings)} for {len(chunks)} chunks"
            )
            return False

        # 3. Build chunk rows and insert
        chunk_rows = []
        for i, chunk in enumerate(chunks):
            emb_vector = embeddings[i].tolist()
            emb_str = "[" + ",".join(f"{v:.8f}" for v in emb_vector) + "]"
            chunk_rows.append((
                article_id,
                chunk.chunk_index,
                chunk.full_text,
                emb_str,
                chunk.full_text,  # tsv = chunk_text for pg_bigm search
            ))

        inserted = self._insert_chunks_batch(chunk_rows)
        if inserted == 0 and chunk_rows:
            logger.error(f"Zero chunks inserted for article {article.url}")
            return False

        return True

    def upload_batch(
        self,
        articles_with_chunks: list[tuple[CanonicalDataModel, list[Chunk]]],
        site_override: str = "",
    ) -> int:
        """
        Upload a batch of articles with their chunks.

        Collects all embed texts first, embeds in one pass, then inserts
        into the database. This is more efficient than per-article embedding.

        Args:
            articles_with_chunks: List of (CanonicalDataModel, list[Chunk]) tuples.
            site_override: Optional site override for all articles.

        Returns:
            Number of successfully uploaded articles.
        """
        if not articles_with_chunks:
            return 0

        # Phase A: Insert articles and collect embed texts
        # Each entry: (article_id, site, chunks, embed_start_idx)
        article_info: list[tuple[int, str, list[Chunk], int]] = []
        all_embed_texts: list[str] = []
        skipped = 0

        for article, chunks in articles_with_chunks:
            site = site_override or article.source_id
            article_id = self._insert_article(article, site)
            if article_id is None:
                skipped += 1
                continue

            if not chunks:
                article_info.append((article_id, site, [], len(all_embed_texts)))
                continue

            start_idx = len(all_embed_texts)
            for chunk in chunks:
                text = chunk.embedding_text if chunk.embedding_text else chunk.full_text
                all_embed_texts.append(text)
            article_info.append((article_id, site, chunks, start_idx))

        if skipped > 0:
            logger.warning(f"upload_batch: {skipped} articles failed insertion")

        # Phase B: Embed all texts in one pass
        if all_embed_texts:
            try:
                embeddings = _embed_texts(all_embed_texts)
            except Exception as e:
                logger.error(f"Batch embedding failed: {e}")
                return 0
        else:
            embeddings = np.empty((0, 1024), dtype=np.float32)

        # Phase C: Insert chunks into database
        chunk_insert_buffer: list[tuple] = []
        success_count = 0

        for article_id, site, chunks, start_idx in article_info:
            if not chunks:
                success_count += 1
                continue

            for j, chunk in enumerate(chunks):
                emb_idx = start_idx + j
                emb_vector = embeddings[emb_idx].tolist()
                emb_str = "[" + ",".join(f"{v:.8f}" for v in emb_vector) + "]"
                chunk_insert_buffer.append((
                    article_id,
                    chunk.chunk_index,
                    chunk.full_text,
                    emb_str,
                    chunk.full_text,  # tsv = chunk_text for pg_bigm search
                ))

            success_count += 1

            # Flush buffer when full
            if len(chunk_insert_buffer) >= DB_INSERT_BATCH_SIZE:
                inserted = self._insert_chunks_batch(chunk_insert_buffer)
                if inserted == 0 and chunk_insert_buffer:
                    logger.warning(
                        f"Chunk batch insert returned 0 for {len(chunk_insert_buffer)} rows"
                    )
                chunk_insert_buffer = []

        # Flush remaining
        if chunk_insert_buffer:
            inserted = self._insert_chunks_batch(chunk_insert_buffer)
            if inserted == 0 and chunk_insert_buffer:
                logger.warning(
                    f"Final chunk batch insert returned 0 for {len(chunk_insert_buffer)} rows"
                )

        logger.info(
            f"upload_batch complete: {success_count}/{len(articles_with_chunks)} articles, "
            f"{len(all_embed_texts)} chunks embedded"
        )
        return success_count

    def get_stats(self) -> dict:
        """Get article and chunk counts from PostgreSQL."""
        try:
            conn = self._get_connection()
            art_count = conn.execute("SELECT COUNT(*) AS cnt FROM articles").fetchone()["cnt"]
            chunk_count = conn.execute("SELECT COUNT(*) AS cnt FROM chunks").fetchone()["cnt"]
            return {
                "articles_count": art_count,
                "chunks_count": chunk_count,
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"articles_count": -1, "chunks_count": -1, "error": str(e)}

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as e:
                logger.warning(f"Error closing PostgreSQL connection: {e}")
            finally:
                self._conn = None
                self._connected = False
            logger.info("PostgreSQL connection closed")
