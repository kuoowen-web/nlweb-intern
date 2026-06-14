"""
Dual-Tier Storage for M0 Indexing Module.

- The Map (Qdrant): Stores chunk summaries + embeddings for search
- The Vault (SQLite): Stores compressed full text for retrieval
"""

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False

from .chunking_engine import Chunk


@dataclass
class VaultConfig:
    """Vault storage configuration."""
    db_path: Path
    compression_level: int = 3
    short_threshold: int = 1000
    long_threshold: int = 5000
    short_compression: int = 1
    long_compression: int = 5


class VaultStorage:
    """
    SQLite-based storage for compressed full text.

    Uses Zstd compression with adaptive compression levels.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS article_chunks (
        chunk_id TEXT PRIMARY KEY,
        article_url TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        full_text_compressed BLOB NOT NULL,
        original_length INTEGER,
        compressed_length INTEGER,
        version INTEGER DEFAULT 2,
        is_deleted INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        deleted_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_article_url ON article_chunks(article_url);
    CREATE INDEX IF NOT EXISTS idx_version ON article_chunks(version);
    CREATE INDEX IF NOT EXISTS idx_is_deleted ON article_chunks(is_deleted);
    """

    def __init__(self, config: Optional[VaultConfig] = None):
        """
        Initialize VaultStorage.

        Args:
            config: VaultConfig or None for defaults
        """
        if config is None:
            # Default: data/vault/full_texts.db
            db_path = Path(__file__).parents[3] / "data" / "vault" / "full_texts.db"
            config = VaultConfig(db_path=db_path)

        self.config = config
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_lock = threading.Lock()
        self._decompressor = zstd.ZstdDecompressor() if ZSTD_AVAILABLE else None

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection (thread-safe)."""
        with self._conn_lock:
            if self._conn is None:
                # Ensure directory exists
                self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
                # check_same_thread=False for async compatibility
                self._conn = sqlite3.connect(str(self.config.db_path), check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(self.SCHEMA)
            return self._conn

    def _get_compression_level(self, text_length: int) -> int:
        """Get adaptive compression level based on text length."""
        if text_length < self.config.short_threshold:
            return self.config.short_compression
        elif text_length > self.config.long_threshold:
            return self.config.long_compression
        return self.config.compression_level

    def _compress(self, text: str) -> bytes:
        """Compress text using Zstd or fallback to raw bytes."""
        text_bytes = text.encode('utf-8')
        if not ZSTD_AVAILABLE:
            return text_bytes

        level = self._get_compression_level(len(text))
        compressor = zstd.ZstdCompressor(level=level)
        return compressor.compress(text_bytes)

    def _decompress(self, data: bytes) -> str:
        """Decompress data using Zstd or treat as raw bytes."""
        if not ZSTD_AVAILABLE:
            return data.decode('utf-8')

        try:
            return self._decompressor.decompress(data).decode('utf-8')
        except zstd.ZstdError:
            # Fallback: maybe it's not compressed
            return data.decode('utf-8')

    def store_chunk(self, chunk: Chunk) -> None:
        """
        Store a chunk in the vault.

        Args:
            chunk: Chunk to store
        """
        conn = self._get_connection()
        compressed = self._compress(chunk.full_text)

        conn.execute("""
            INSERT OR REPLACE INTO article_chunks
            (chunk_id, article_url, chunk_index, full_text_compressed,
             original_length, compressed_length, version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 2, ?)
        """, (
            chunk.chunk_id,
            chunk.article_url,
            chunk.chunk_index,
            compressed,
            len(chunk.full_text),
            len(compressed),
            datetime.utcnow().isoformat()
        ))
        conn.commit()

    def store_chunks(self, chunks: list[Chunk]) -> None:
        """Store multiple chunks in a single transaction."""
        conn = self._get_connection()
        now = datetime.utcnow().isoformat()

        data = []
        for chunk in chunks:
            compressed = self._compress(chunk.full_text)
            data.append((
                chunk.chunk_id,
                chunk.article_url,
                chunk.chunk_index,
                compressed,
                len(chunk.full_text),
                len(compressed),
                2,
                now
            ))

        conn.executemany("""
            INSERT OR REPLACE INTO article_chunks
            (chunk_id, article_url, chunk_index, full_text_compressed,
             original_length, compressed_length, version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, data)
        conn.commit()

    def get_chunk(self, chunk_id: str) -> Optional[str]:
        """
        Retrieve full text for a chunk.

        Args:
            chunk_id: Chunk ID

        Returns:
            Full text or None if not found
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT full_text_compressed FROM article_chunks
            WHERE chunk_id = ? AND is_deleted = 0
        """, (chunk_id,))

        row = cursor.fetchone()
        if row is None:
            return None

        return self._decompress(row[0])

    def get_article_chunks(self, article_url: str) -> list[str]:
        """
        Retrieve all chunks for an article.

        Args:
            article_url: Article URL

        Returns:
            List of full texts, ordered by chunk_index
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT full_text_compressed FROM article_chunks
            WHERE article_url = ? AND is_deleted = 0
            ORDER BY chunk_index
        """, (article_url,))

        return [self._decompress(row[0]) for row in cursor.fetchall()]

    def iter_chunk_ids(self, site: Optional[str] = None, batch_size: int = 10000):
        """
        Generator that yields batches of chunk_ids from the vault.

        Args:
            site: Optional site filter (filters by article_url domain pattern)
            batch_size: Number of chunk_ids per batch

        Yields:
            set[str] of chunk_ids per batch
        """
        conn = self._get_connection()
        query = "SELECT chunk_id FROM article_chunks WHERE is_deleted = 0"
        params = []

        if site:
            # Filter by site pattern in article_url (e.g. 'ltn' matches ltn.com.tw)
            query += " AND article_url LIKE ?"
            params.append(f"%{site}%")

        query += " ORDER BY chunk_id"

        cursor = conn.execute(query, params)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield {row[0] for row in rows}

    def get_chunks_by_ids(self, chunk_ids: set) -> list:
        """
        Retrieve full Chunk-like data for a set of chunk_ids.

        Returns list of dicts with chunk_id, article_url, chunk_index, full_text.
        """
        conn = self._get_connection()
        results = []
        chunk_ids_list = list(chunk_ids)

        # Query in batches of 500 to avoid SQLite variable limit
        for i in range(0, len(chunk_ids_list), 500):
            batch = chunk_ids_list[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            cursor = conn.execute(f"""
                SELECT chunk_id, article_url, chunk_index, full_text_compressed
                FROM article_chunks
                WHERE chunk_id IN ({placeholders}) AND is_deleted = 0
            """, batch)
            for row in cursor:
                results.append({
                    'chunk_id': row[0],
                    'article_url': row[1],
                    'chunk_index': row[2],
                    'full_text': self._decompress(row[3]),
                })
        return results

    def soft_delete_chunks(self, chunk_ids: list[str]) -> None:
        """Soft delete chunks by setting is_deleted flag."""
        conn = self._get_connection()
        now = datetime.utcnow().isoformat()

        conn.executemany("""
            UPDATE article_chunks
            SET is_deleted = 1, deleted_at = ?
            WHERE chunk_id = ?
        """, [(now, cid) for cid in chunk_ids])
        conn.commit()

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


@dataclass
class MapPayload:
    """Payload structure for Qdrant (Version 2).

    Retriever/Ranking/Reasoning 相容欄位:
        url: article URL (citation link, dedup key)
        name: chunk summary (BM25 + display)
        site: source identifier
        schema_json: article-level metadata (NOT chunk metadata)

    Chunk-specific fields at top level for Qdrant native filtering.
    """
    url: str           # article URL (NOT chunk_id)
    name: str          # chunk summary
    site: str
    schema_json: str   # article-level metadata JSON
    chunk_id: str
    article_url: str
    chunk_index: int
    char_start: int
    char_end: int
    keywords: list
    indexed_at: str
    task_id: str
    version: int = 2

    @classmethod
    def from_chunk(
        cls,
        chunk: Chunk,
        site: str,
        headline: str = "",
        date_published: str = "",
        author: str = "",
        publisher: str = "",
        keywords: list = None,
        description: str = "",
        task_id: str = "",
    ) -> 'MapPayload':
        """Create payload from a Chunk with article-level metadata.

        Args:
            chunk: Chunk object
            site: source identifier (e.g. 'ltn', 'udn')
            headline: article headline
            date_published: ISO 8601 date string
            author: article author
            publisher: publisher name
            keywords: keyword list
            description: first ~200 chars of article body
            task_id: originating task ID
        """
        if keywords is None:
            keywords = []

        schema = {
            '@type': 'NewsArticle',
            'headline': headline,
            'datePublished': date_published,
            'author': author,
            'publisher': publisher,
            'keywords': keywords,
            'description': description,
        }

        return cls(
            url=chunk.article_url,
            name=chunk.summary,
            site=site,
            schema_json=json.dumps(schema, ensure_ascii=False),
            chunk_id=chunk.chunk_id,
            article_url=chunk.article_url,
            chunk_index=chunk.chunk_index,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            keywords=keywords,
            indexed_at=datetime.utcnow().isoformat(),
            task_id=task_id,
            version=2,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for Qdrant payload."""
        return asdict(self)
