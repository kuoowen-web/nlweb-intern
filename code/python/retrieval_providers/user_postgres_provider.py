# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
PostgreSQL retrieval provider for user-uploaded private files.

Replaces the Qdrant-based user_qdrant_provider.py.
Queries the 'user_document_chunks' table with user_id filtering
to retrieve chunks from a user's private knowledge base.

Connection pattern mirrors postgres_client.py: reads
POSTGRES_CONNECTION_STRING from environment variables.
"""

import os
import time
import asyncio
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, parse_qs

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
import pgvector.psycopg

from core.embedding import get_embedding
from misc.logger.logging_config_helper import get_configured_logger
from misc.logger.logger import LogLevel

logger = get_configured_logger("user_postgres_provider")


def _parse_connection_string(conn_str: str) -> Dict[str, Any]:
    """Parse a PostgreSQL connection URI into individual components."""
    parsed = urlparse(conn_str)
    host = parsed.hostname
    port = parsed.port or 5432
    database = parsed.path[1:] if parsed.path else None
    username = parsed.username
    password = parsed.password

    if not username:
        query_params = parse_qs(parsed.query)
        username = query_params.get('user', [None])[0]
        password = password or query_params.get('password', [None])[0]

    return {
        'host': host,
        'port': port,
        'database': database,
        'username': username,
        'password': password,
    }


def _build_user_docs_where(user_id, org_id, source_ids):
    """Build the WHERE clause + params for user_document_chunks search.

    Enforced org isolation (拍板 2):
      - org_id truthy  → "org_id = %s"（隔離到該 org）
      - org_id falsy   → "org_id IS NULL"（隔離到無 org 文件，不跳過過濾）

    Returns (where_sql, params). Note: "org_id IS NULL" is literal SQL and is
    NOT added to params (SQL 中 org_id = NULL 永遠為 false，必須用 IS NULL).
    """
    clauses = ["user_id = %s"]
    params = [user_id]

    if org_id:
        clauses.append("org_id = %s")
        params.append(org_id)
    else:
        clauses.append("org_id IS NULL")

    if source_ids:
        placeholders = ", ".join(["%s"] * len(source_ids))
        clauses.append(f"source_id IN ({placeholders})")
        params.extend(source_ids)

    return " AND ".join(clauses), params


class UserPostgresProvider:
    """Provider for querying user's private documents stored in PostgreSQL."""

    _pool: Optional[AsyncConnectionPool] = None
    _pool_init_lock: asyncio.Lock = None  # set per-instance in __init__

    def __init__(self):
        self._pool = None
        self._pool_init_lock = asyncio.Lock()

        conn_str = (
            os.environ.get('POSTGRES_CONNECTION_STRING')
            or os.environ.get('DATABASE_URL')
            or os.environ.get('ANALYTICS_DATABASE_URL')
        )
        if not conn_str:
            raise ValueError(
                "No PostgreSQL connection string found. "
                "Set POSTGRES_CONNECTION_STRING (or DATABASE_URL / ANALYTICS_DATABASE_URL)."
            )

        cfg = _parse_connection_string(conn_str)
        self._host = cfg['host']
        self._port = cfg['port']
        self._dbname = cfg['database']
        self._username = cfg['username']
        self._password = cfg['password']

        if not self._host:
            raise ValueError("Missing 'host' in POSTGRES_CONNECTION_STRING")
        if not self._dbname:
            raise ValueError("Missing 'database' in POSTGRES_CONNECTION_STRING")

        logger.info(
            f"UserPostgresProvider initialised: {self._host}:{self._port}/{self._dbname}"
        )

    # ------------------------------------------------------------------
    # Connection pool
    # ------------------------------------------------------------------

    async def _get_pool(self) -> AsyncConnectionPool:
        """Return (and lazily initialise) the async connection pool."""
        if self._pool is None:
            async with self._pool_init_lock:
                if self._pool is None:
                    logger.info("Initialising UserPostgresProvider connection pool")
                    conninfo = (
                        f"host={self._host} port={self._port} "
                        f"dbname={self._dbname} user={self._username} "
                        f"password={self._password} connect_timeout=5"
                    )
                    self._pool = AsyncConnectionPool(
                        conninfo=conninfo,
                        min_size=1,
                        max_size=5,
                        open=False,
                    )
                    await self._pool.open()
                    logger.info("UserPostgresProvider connection pool ready")
        return self._pool

    async def _get_conn(self):
        """Context manager: yield a registered psycopg connection from the pool."""
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await pgvector.psycopg.register_vector_async(conn)
            yield conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_user_documents(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        source_ids: Optional[List[str]] = None,
        query_params: Optional[Dict] = None,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search user's private documents using pgvector cosine similarity.

        Args:
            query: Search query text
            user_id: User identifier (mandatory filter)
            top_k: Number of results to return
            source_ids: Optional list of source_ids to restrict search to
            query_params: Optional parameters forwarded to the embedding function
            org_id: Optional organisation identifier (additional filter)

        Returns:
            List of result dicts with content and metadata; score is cosine
            similarity in [0, 1] (higher = more similar).
        """
        logger.info(
            f"Searching user documents: user_id={user_id}, org_id={org_id}, top_k={top_k}"
        )

        start_time = time.time()

        # 1. Generate query embedding
        embedding_start = time.time()
        embedding = await get_embedding(query, query_params=query_params)
        # psycopg requires a uniform float list
        embedding = [float(v) for v in embedding]
        embedding_time = time.time() - embedding_start

        # 2. Build WHERE clauses (enforced org isolation — 拍板 2)
        where_sql, params = _build_user_docs_where(user_id, org_id, source_ids)

        # 3. Query: order by cosine distance (ascending = most similar first)
        sql = f"""
            SELECT
                id, user_id, org_id, source_id, doc_id,
                chunk_index, total_chunks, content, metadata,
                1 - (embedding <=> %s::vector) AS score
            FROM user_document_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        # embedding appears twice: once in SELECT, once in ORDER BY
        all_params = [embedding] + params + [embedding, top_k]

        retrieval_start = time.time()
        try:
            pool = await self._get_pool()
            async with pool.connection() as conn:
                await pgvector.psycopg.register_vector_async(conn)
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(sql, all_params)
                    rows = await cur.fetchall()
        except Exception as e:
            logger.exception(f"Error querying user_document_chunks: {e}")
            return []

        retrieval_time = time.time() - retrieval_start
        total_time = time.time() - start_time

        results = self._format_results(rows)

        logger.log_with_context(
            LogLevel.INFO,
            "User documents search completed",
            {
                "user_id": user_id,
                "org_id": org_id,
                "embedding_time": f"{embedding_time:.2f}s",
                "retrieval_time": f"{retrieval_time:.2f}s",
                "total_time": f"{total_time:.2f}s",
                "results_count": len(results),
                "embedding_dim": len(embedding),
            },
        )

        return results

    async def user_has_any_chunk(
        self,
        user_id: str,
        org_id: Optional[str] = None,
    ) -> bool:
        """這個 user（在該 org 範圍內）有沒有任何 chunk（票 2026-08-11-a A14 成本短路）。

        **與 search_user_documents 的關鍵差別：不呼叫 get_embedding。**
        後者在查 DB 之前先 `await get_embedding(query)`（本檔 :189），零文件使用者
        的每個 internal seed 都白付一次 embedding；本函式只走 btree
        idx_udc_user_id（migration d4a7e1b83c59 :49-52）。

        **為什麼查 user_document_chunks 而不是 user_sources**（AR R2 BL-1）：
          1. orphan chunk 是正常可達路徑 —— core/user_data_manager.py:372-375 先
             DELETE FROM user_sources，chunk 清理在 webserver/routes/user_data.py:388-394
             且**明文 best-effort**（except 只 warning）；chunk 表無 FOREIGN KEY
             ⇒ DB 層零 cascade。此時 user_sources=0 但 chunk 還在、還撈得到。
          2. 跨 store —— UserDataDB 優先讀 USER_DATA_DATABASE_URL 且可 fallback SQLite
             （core/user_data_db.py:89-96），本 provider 只吃 PG 且從不讀那個變數
             ⇒ 兩者可能是不同的資料庫，兩個方向的誤判都可能。
        ⇒ 判準必須與**實際被檢索的那張表**同源，否則短路會靜默漏撈私文
          （正是本 plan 批評 B4 時定義的失效形狀）。

        **WHERE 共用 _build_user_docs_where**（本檔 :57）：org_id truthy → org_id = %s，
        falsy → org_id IS NULL。自己拼 WHERE 會讓短路判準與檢索的 org 隔離語義漂移。
        """
        where_sql, params = _build_user_docs_where(user_id, org_id, None)
        sql = f"SELECT 1 FROM user_document_chunks WHERE {where_sql} LIMIT 1"
        pool = await self._get_pool()
        async with pool.connection() as cur_conn:
            async with cur_conn.cursor() as cur:
                await cur.execute(sql, params)
                return (await cur.fetchone()) is not None

    def _format_results(self, rows: List[Dict]) -> List[Dict[str, Any]]:
        """
        Format DB rows into the same dict structure previously returned by
        UserQdrantProvider._format_results().

        Fields: content, source_id, doc_id, user_id, chunk_index,
                total_chunks, metadata, score, url, source_type.
        """
        results = []
        for row in rows:
            result = {
                # Content
                'content': row.get('content', ''),

                # IDs
                'source_id': row.get('source_id', ''),
                'doc_id': row.get('doc_id', ''),
                'user_id': row.get('user_id', ''),

                # Chunk info
                'chunk_index': row.get('chunk_index', 0),
                'total_chunks': row.get('total_chunks', 1),

                # Metadata
                'metadata': row.get('metadata') or {},

                # Score: pgvector <=> returns distance; we convert to similarity
                'score': float(row.get('score', 0.0)),

                # URL：**chunk 級**識別字（票 2026-08-14-k，P0）。
                # ⚠ 最後一段 chunk_index 是承重的，不是裝飾：doc_id 是每份文件
                #   一顆 uuid4（core/user_data_manager.py:264-288 對整份文件呼叫
                #   一次 create_document_record）⇒ 少了 chunk_index，一份文件的
                #   N 個 chunk 產出逐字相同的 URL，而 LR 入池點
                #   （reasoning/live_research/loop_engine.py:816-818）以 URL 為
                #   去重鍵、命中即沿用既有 eid 且**不更新 snippet** ⇒ writer 永遠
                #   只看得到第一次命中的那一個片段（prod 實測單一 doc_id 最多
                #   53 個 chunk 全部同 URL）。
                # ⚠ 用 row 的 chunk_index 而非 enumerate 的位置：後者是「本次
                #   檢索結果內的序號」，跨檢索不穩定，會讓同一個 chunk 在不同輪
                #   拿到不同 URL ⇒ 把「併太多」換成「灌太多」。
                # ⚠ scheme 前綴 private:// 不可改 —— 下游所有 startswith 特判
                #   （reasoning/orchestrator.py:3036、static/js/features/
                #   deep-research.js:189、sanitize_private_url）都依賴它。
                'url': (
                    f"private://{row.get('user_id')}/"
                    f"{row.get('source_id')}/{row.get('doc_id')}/"
                    f"{row.get('chunk_index', 0)}"
                ),

                # Source type for analytics
                'source_type': 'private',
            }
            results.append(result)
        return results

    async def delete_source_vectors(self, source_id: str) -> int:
        """
        Delete all chunks associated with a source_id.

        Returns:
            Number of rows deleted.
        """
        sql = "DELETE FROM user_document_chunks WHERE source_id = %s"
        try:
            pool = await self._get_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, (source_id,))
                    count = cur.rowcount
                    await conn.commit()

            if count == 0:
                logger.info(
                    f"No chunks found for source_id={source_id}, nothing to delete"
                )
            else:
                logger.info(f"Deleted {count} chunks for source_id={source_id}")
            return count

        except Exception as e:
            logger.exception(
                f"Error deleting chunks for source_id={source_id}: {e}"
            )
            raise

    async def get_document_chunks(
        self,
        user_id: str,
        source_id: str,
        doc_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all chunks for a specific document, ordered by chunk_index.

        Args:
            user_id: User identifier
            source_id: Source identifier
            doc_id: Optional document identifier (None = all docs in source)

        Returns:
            List of chunk dicts ordered by chunk_index.
        """
        clauses = ["user_id = %s", "source_id = %s"]
        params: List[Any] = [user_id, source_id]

        if doc_id:
            clauses.append("doc_id = %s")
            params.append(doc_id)

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT chunk_index, content, metadata
            FROM user_document_chunks
            WHERE {where_sql}
            ORDER BY chunk_index
            LIMIT 1000
        """

        try:
            pool = await self._get_pool()
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(sql, params)
                    rows = await cur.fetchall()

            chunks = [
                {
                    'chunk_index': row['chunk_index'],
                    'content': row['content'],
                    'metadata': row.get('metadata') or {},
                }
                for row in rows
            ]

            logger.info(
                f"Retrieved {len(chunks)} chunks for source_id={source_id}"
            )
            return chunks

        except Exception as e:
            logger.exception(f"Error retrieving document chunks: {e}")
            raise

    async def insert_chunks(
        self,
        rows: List[Dict[str, Any]],
    ) -> int:
        """
        Batch-insert chunk rows into user_document_chunks.

        Each dict must contain: user_id, org_id, source_id, doc_id,
        chunk_index, total_chunks, content, metadata, embedding.

        Returns:
            Number of rows inserted.
        """
        if not rows:
            raise ValueError("insert_chunks called with empty rows list")

        sql = """
            INSERT INTO user_document_chunks
                (user_id, org_id, source_id, doc_id, chunk_index,
                 total_chunks, content, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
        """

        try:
            pool = await self._get_pool()
            async with pool.connection() as conn:
                await pgvector.psycopg.register_vector_async(conn)
                async with conn.cursor() as cur:
                    for row in rows:
                        embedding = [float(v) for v in row['embedding']]
                        await cur.execute(sql, (
                            row['user_id'],
                            row.get('org_id'),
                            row['source_id'],
                            row['doc_id'],
                            row['chunk_index'],
                            row['total_chunks'],
                            row['content'],
                            Jsonb(row.get('metadata', {})),
                            embedding,
                        ))
                    await conn.commit()

            logger.info(f"Inserted {len(rows)} chunks into user_document_chunks")
            return len(rows)

        except Exception as e:
            logger.exception(f"Failed to insert chunks: {e}")
            raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_user_postgres_provider_instance: Optional[UserPostgresProvider] = None


def get_user_postgres_provider() -> UserPostgresProvider:
    """Return the global UserPostgresProvider singleton."""
    global _user_postgres_provider_instance
    if _user_postgres_provider_instance is None:
        _user_postgres_provider_instance = UserPostgresProvider()
    return _user_postgres_provider_instance
