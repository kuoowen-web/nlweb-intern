# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

import json
import os
import asyncio
import sentry_sdk
import time
from datetime import datetime, timezone
from typing import List, Dict, Union, Optional, Any

from urllib.parse import urlparse, parse_qs

# PostgreSQL client library (psycopg3)
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import pgvector.psycopg

from core.config import CONFIG
from core.retriever import RetrievalClientBase
from core.embedding import get_embedding, batch_get_embeddings
from misc.logger.logging_config_helper  import get_configured_logger
from misc.logger.logger import LogLevel

# Analytics logging
from core.query_logger import get_query_logger

logger = get_configured_logger("postgres_client")

class PgVectorClient(RetrievalClientBase):

    def __init__(self, endpoint_name: Optional[str] = None):
        self.endpoint_name = endpoint_name or CONFIG.write_endpoint
        self._conn_lock = asyncio.Lock()
        self._pool = None
        self._pool_init_lock = asyncio.Lock()
        
        logger.info(f"Initializing PgVectorClient for endpoint: {self.endpoint_name}")
        
        # Get endpoint configuration
        self.endpoint_config = self._get_endpoint_config()
        self.api_endpoint = self.endpoint_config.api_endpoint
        self.api_key = self.endpoint_config.api_key
        self.database_path = self.endpoint_config.database_path
        self.default_collection_name = self.endpoint_config.index_name or "nlweb_collection"

        self.pg_raw_config = self._get_config_from_postgres_connection_string(self.api_endpoint)
        
        self.host = self.pg_raw_config.get("host")
        self.port = self.pg_raw_config.get("port", 5432)  # Default PostgreSQL port
        self.dbname = self.pg_raw_config.get("database") 
        self.username = self.pg_raw_config.get("username") 
        self.password = self.api_key or self.pg_raw_config.get("password")
        self.table_name = self.default_collection_name or "documents"

        # Validate critical configuration
        if not self.host:
            error_msg = f"Missing 'host' in PostgreSQL configuration for endpoint '{self.endpoint_name}'"
            logger.error(error_msg)
            logger.error(f"Available configuration keys: {list(self.pg_raw_config.keys())}")
            raise ValueError(error_msg)
        if not self.dbname:
            error_msg = f"Missing 'database_name' in PostgreSQL configuration for endpoint '{self.endpoint_name}'"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info(f"Using PostgreSQL database: {self.dbname} on {self.host}:{self.port}")
        logger.info(f"Table name: {self.default_collection_name}")
    
    def _get_config_from_postgres_connection_string(self, connection_string: str) -> Dict[str, Any]:
        parsed_url = urlparse(connection_string)

        host = parsed_url.hostname
        port = parsed_url.port
        database = parsed_url.path[1:] if parsed_url.path else None

        username = parsed_url.username
        password = parsed_url.password

        if not username:
            query_params = parse_qs(parsed_url.query)
            username = query_params.get('user', [None])[0]
            password = password or query_params.get('password', [None])[0]

        return {
            'host': host,
            'port': port,
            'database': database,
            'username': username,
            'password': password
        }

    def _get_endpoint_config(self):
        """
        Get the PostgreSQL endpoint configuration from CONFIG
        
        Returns:
            Tuple of (RetrievalProviderConfig)
        """
        # Get the endpoint configuration object
        endpoint_config = CONFIG.retrieval_endpoints.get(self.endpoint_name)
        
        if not endpoint_config:
            error_msg = f"No configuration found for endpoint {self.endpoint_name}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Verify this is a PostgreSQL endpoint
        if endpoint_config.db_type != "postgres":
            error_msg = f"Endpoint {self.endpoint_name} is not a PostgreSQL endpoint (type: {endpoint_config.db_type})"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Get the raw configuration dictionary from the YAML file
        config_dir = os.path.dirname(os.path.abspath(os.path.join(os.path.dirname(__file__), "../config")))
        config_path = os.path.join(config_dir, "config_retrieval.yaml")

        return endpoint_config
    
    async def _get_connection_pool(self):
        """
        Get or create the connection pool for PostgreSQL.
        Connection pooling is used for better performance and resource management.
        
        Returns:
            A PostgreSQL connection pool
        """
        if self._pool is None:
            async with self._pool_init_lock:
                if self._pool is None:
                    logger.info("Initializing PostgreSQL connection pool")
                    
                    try:
                        # Make sure we have all required connection parameters
                        if not self.host:
                            raise ValueError("Missing host in PostgreSQL configuration")
                        if not self.dbname:
                            raise ValueError("Missing database_name in PostgreSQL configuration")
                        if not self.username:
                            raise ValueError("Missing username or username_env in PostgreSQL configuration")
                        if not self.password:
                            raise ValueError("Missing password or password_env in PostgreSQL configuration")
                            
                        # Log connection attempt (without sensitive information)
                        logger.info(f"Connecting to PostgreSQL at {self.host}:{self.port}/{self.dbname} with user {self.username}")
                        
                        # Set up async connection pool with reasonable defaults
                        # #5 (deploy-env-hardening): app-only statement / idle 超時。
                        # libpq `options` 走連線啟動參數,只套用「此 pool 開出的連線」
                        # = app runtime search 查詢。**不影響 alembic / DDL / 維護**——
                        # alembic/env.py 走獨立 SQLAlchemy 連線,conninfo 不帶 options。
                        # 🔴 options 值含空白 → libpq 要求**單引號包覆**,否則
                        #    `invalid connection option "statement_timeout"` 開 pool crash
                        #    (親驗:psycopg.conninfo.conninfo_to_dict 無引號→raise)。
                        # statement_timeout=30s: 界定失控 query([HYPOTHESIS] 正常查詢
                        #    不撞,冷查詢可能 10-20s → prod 監 pg_stat_statements max_exec_time)。
                        # idle_in_transaction_session_timeout=60s: async cancel 留下的
                        #    aborted-transaction 連線不會永久占住 pool。
                        conninfo = (
                            f"host={self.host} port={self.port} dbname={self.dbname} "
                            f"user={self.username} password={self.password} "
                            f"connect_timeout=5 "
                            f"options='-c statement_timeout=30s -c idle_in_transaction_session_timeout=60s'"
                        )
                        self._pool = AsyncConnectionPool(
                            conninfo=conninfo,
                            min_size=1,
                            max_size=10,
                            open=False # Don't open immediately, we will do it explicitly later
                        )
                        # Explicitly open the pool as recommended in newer psycopg versions
                        await self._pool.open()
                        logger.info("PostgreSQL connection pool initialized")
                        
                        # Verify pgvector extension is installed
                        async with self._pool.connection() as conn:
                            # Register vector type
                            await pgvector.psycopg.register_vector_async(conn)
                            
                            async with conn.cursor() as cur:
                                await cur.execute("SELECT * FROM pg_extension WHERE extname = 'vector'")
                                row = await cur.fetchone()
                                if not row:
                                    logger.warning("pgvector extension not found in the database")
                    
                    except Exception as e:
                        logger.error(
                            f"無法連線到 PostgreSQL ({self.host}:{self.port})。"
                            f"是不是忘記開 Docker Desktop？"
                        )
                        logger.exception(f"Error creating PostgreSQL connection pool: {e}")
                        raise
        
        return self._pool

    async def close(self):
        """Close the connection pool when done"""
        if self._pool:
            await self._pool.close()
    
    async def _execute_with_retry(self, query_func, max_retries=3, initial_backoff=0.1):
        """
        Execute a database query with retry logic for transient failures.
        
        Args:
            query_func: Function that performs the database query
            max_retries: Maximum number of retry attempts
            initial_backoff: Initial backoff time in seconds (doubles with each retry)
            
        Returns:
            Query result
        """
        retry_count = 0
        backoff_time = initial_backoff
        
        while True:
            try:
                # With psycopg3, we can use async directly
                async with (await self._get_connection_pool()).connection() as conn:
                    # Register vector type
                    await pgvector.psycopg.register_vector_async(conn)
                    return await query_func(conn)
            
            except (psycopg.OperationalError, psycopg.InternalError) as e:
                # Handle transient errors like connection issues
                retry_count += 1
                
                if retry_count > max_retries:
                    logger.error(f"Maximum retries exceeded: {e}")
                    raise
                
                logger.warning(f"Database error (attempt {retry_count}/{max_retries}): {e}")
                logger.warning(f"Retrying in {backoff_time:.2f} seconds...")
                
                await asyncio.sleep(backoff_time)
                backoff_time *= 2  # Exponential backoff
            
            except Exception as e:
                # Non-transient errors are raised immediately
                logger.exception(f"Database error: {e}")
                raise
    
    async def delete_documents_by_site(self, site: str, **kwargs) -> int:
        logger.info(f"Deleting articles for source: {site}")

        async def _delete(conn):
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM articles WHERE source = %s", (site,))
                count = cur.rowcount
                await conn.commit()
                return count

        try:
            count = await self._execute_with_retry(_delete)
            logger.info(f"Deleted {count} articles for source: {site}")
            return count
        except Exception as e:
            logger.exception(f"Error deleting articles for source {site}: {e}")
            raise
    
    async def upload_documents(self, documents: List[Dict[str, Any]], **kwargs) -> int:
        logger.info(f"Uploading {len(documents)} documents")

        if not documents:
            logger.warning("Empty documents list provided")
            return 0

        batch_size = kwargs.get("batch_size", 100)
        inserted_count = 0

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]

            async def _upload_batch(conn, _batch=batch):
                async with conn.cursor() as cur:
                    count = 0
                    for doc in _batch:
                        try:
                            required = ["url", "name", "site", "embedding"]
                            if not all(k in doc for k in required):
                                missing = [k for k in required if k not in doc]
                                logger.warning(f"Skipping document with missing fields: {missing}")
                                continue

                            embedding = doc["embedding"]
                            if not isinstance(embedding, list) or len(embedding) == 0:
                                logger.warning(f"Skipping document with invalid embedding")
                                continue

                            # Extract article-level fields
                            url = doc["url"]
                            title = doc["name"]
                            source = doc["site"]
                            schema_data = doc.get("schema_json")
                            if isinstance(schema_data, str):
                                try:
                                    schema_data = json.loads(schema_data)
                                except (json.JSONDecodeError, TypeError):
                                    schema_data = {}
                            if not isinstance(schema_data, dict):
                                schema_data = {}

                            author = schema_data.get("author", {}).get("name") if isinstance(schema_data.get("author"), dict) else schema_data.get("author")
                            date_published = schema_data.get("datePublished")
                            content = schema_data.get("articleBody", "")
                            chunk_text = content or title
                            tsv = chunk_text
                            chunk_index = doc.get("chunk_index", 0)

                            # Build metadata from schema minus fields we extract
                            metadata = {k: v for k, v in schema_data.items()
                                        if k not in ("headline", "url", "datePublished", "author", "articleBody", "source", "@type")}

                            # Upsert article
                            await cur.execute("""
                                INSERT INTO articles (url, title, author, source, date_published, content, metadata)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (url) DO UPDATE SET
                                    title = EXCLUDED.title,
                                    author = EXCLUDED.author,
                                    source = EXCLUDED.source,
                                    date_published = EXCLUDED.date_published,
                                    content = EXCLUDED.content,
                                    metadata = EXCLUDED.metadata
                                RETURNING id
                            """, (url, title, author, source, date_published, content,
                                  json.dumps(metadata, ensure_ascii=False)))

                            row = await cur.fetchone()
                            article_id = row[0]

                            # Upsert chunk
                            await cur.execute("""
                                INSERT INTO chunks (article_id, chunk_index, chunk_text, embedding, tsv)
                                VALUES (%s, %s, %s, %s::vector, %s)
                                ON CONFLICT (article_id, chunk_index) DO UPDATE SET
                                    chunk_text = EXCLUDED.chunk_text,
                                    embedding = EXCLUDED.embedding,
                                    tsv = EXCLUDED.tsv
                            """, (article_id, chunk_index, chunk_text, embedding, tsv))

                            count += 1

                        except Exception as e:
                            logger.warning(f"Error processing document: {e}, keys: {list(doc.keys())}")
                            continue

                    await conn.commit()
                    return count

            try:
                batch_count = await self._execute_with_retry(_upload_batch)
                inserted_count += batch_count
                logger.info(f"Batch {i//batch_size + 1} inserted: {batch_count} documents")
            except Exception as e:
                logger.exception(f"Error uploading batch {i//batch_size + 1}: {e}")
                raise

        logger.info(f"Successfully uploaded {inserted_count} documents")
        return inserted_count
    
    def _build_filters(self, sites, query_params=None, kwargs_filters=None):
        """Build SQL WHERE clauses and parameters from filter inputs.

        Args:
            sites: List of site/source names to filter by.
            query_params: Legacy dict with keys like date_from, date_to, author.
            kwargs_filters: Generic filter list passed via kwargs['filters'], e.g.
                [{"field": "datePublished", "operator": "gte", "value": "2026-01-01"}]
                Supported fields: datePublished
                Supported operators: gte (>=), lte (<=)
                Unknown fields or operators are silently ignored to prevent SQL injection.

        Returns:
            Tuple of (clauses: List[str], params: List[Any])
        """
        clauses = []
        params = []

        if sites:
            placeholders = ", ".join(["%s"] * len(sites))
            clauses.append(f"a.source IN ({placeholders})")
            params.extend(sites)

        if query_params:
            if query_params.get("author"):
                clauses.append("a.author ILIKE %s")
                params.append(f"%{query_params['author']}%")
            if query_params.get("date_from"):
                clauses.append("a.date_published >= %s")
                params.append(query_params["date_from"])
            if query_params.get("date_to"):
                clauses.append("a.date_published <= %s")
                params.append(query_params["date_to"])

        # Handle generic kwargs filters (from baseHandler search_filters)
        _FIELD_MAP = {
            "datePublished": "a.date_published",
        }
        _OP_MAP = {
            "gte": ">=",
            "lte": "<=",
        }
        if kwargs_filters:
            for f in kwargs_filters:
                field = f.get("field")
                operator = f.get("operator")
                value = f.get("value")
                if field not in _FIELD_MAP or operator not in _OP_MAP:
                    logger.debug(f"Ignoring unknown filter field/operator: {field}/{operator}")
                    continue
                sql_col = _FIELD_MAP[field]
                sql_op = _OP_MAP[operator]
                clauses.append(f"{sql_col} {sql_op} %s")
                params.append(value)

        return clauses, params

    def _build_schema_json(self, row):
        chunk = row.get("chunk_text", "") or ""
        schema = {
            "@type": "NewsArticle",
            "headline": row["title"],
            "url": row["url"],
            "datePublished": row["date_published"].isoformat() if row.get("date_published") else None,
            "articleBody": row.get("chunk_text", ""),
            "source": row["source"],
            # 新增：卡片摘要顯示（前端 schema.description fallback 鏈期待此 key）
            "description": chunk,
            # 新增：text fragment verbatim quote（前端 buildCitationHref 的 src.quote）
            "matched_text": chunk.strip(),
        }
        if row.get("author"):
            schema["author"] = {"@type": "Person", "name": row["author"]}

        metadata = row.get("metadata")
        if metadata and isinstance(metadata, dict):
            for k, v in metadata.items():
                if k not in schema:
                    schema[k] = v

        return json.dumps(schema, ensure_ascii=False)

    async def search(self, query: str, site: Union[str, List[str]],
                    num_results: int = 50, query_params: Optional[Dict[str, Any]] = None, **kwargs) -> List[List[str]]:
        start_time = time.time()
        logger.info(f"Searching for '{query[:50]}...' in site: {site}, num_results: {num_results}")

        include_vectors = kwargs.get('include_vectors', False)

        # Accept a precomputed embedding to avoid redundant API calls when batching
        precomputed_embedding = kwargs.get('precomputed_embedding', None)
        try:
            if precomputed_embedding is not None:
                logger.debug("Using precomputed embedding, skipping embedding API call")
                query_embedding = [float(v) for v in precomputed_embedding]
            else:
                query_embedding = await get_embedding(query, query_params=query_params)
                # Ensure all values are float — psycopg rejects mixed int/float lists
                query_embedding = [float(v) for v in query_embedding]
        except Exception as e:
            logger.exception(f"Error generating embedding for query: {e}")
            raise

        sites = []
        if isinstance(site, list):
            sites = site
        elif isinstance(site, str) and site != "all":
            sites = [site]

        kwargs_filters = kwargs.get('filters', [])
        filter_clauses, filter_params = self._build_filters(sites, query_params, kwargs_filters=kwargs_filters)

        # Read cosine similarity threshold from config (default 0.40)
        vector_similarity_min = float(
            CONFIG.retrieval_threshold.get('vector_similarity_min', 0.40)
        )

        async def _search_docs(conn, _clauses=filter_clauses, _params=filter_params):
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SET hnsw.ef_search = 100")

                # --- Vector search ---
                embedding_col = ", c.embedding" if include_vectors else ""
                # Combine filter clauses with cosine threshold filter
                threshold_clause = "1 - (c.embedding <=> %s::vector) >= %s"
                if _clauses:
                    where_sql = "WHERE " + " AND ".join(_clauses) + f" AND {threshold_clause}"
                else:
                    where_sql = f"WHERE {threshold_clause}"
                vector_sql = f"""
                    SELECT c.id AS chunk_id, c.article_id, c.chunk_text,
                           a.url, a.title, a.author, a.source, a.date_published, a.metadata,
                           1 - (c.embedding <=> %s::vector) AS vector_score{embedding_col}
                    FROM chunks c
                    JOIN articles a ON a.id = c.article_id
                    {where_sql}
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s
                """
                # Params: score SELECT embedding, filter params, threshold embedding, threshold value,
                #         ORDER BY embedding, LIMIT
                vector_params = [query_embedding] + _params + [query_embedding, vector_similarity_min, query_embedding, num_results]
                await cur.execute(vector_sql, vector_params)
                vector_rows = await cur.fetchall()

                # --- Text search (pg_bigm) ---
                text_where_parts = ["c.tsv LIKE '%%' || likequery(%s) || '%%'"] + _clauses
                text_where_sql = "WHERE " + " AND ".join(text_where_parts)
                # Text search does not select embedding to avoid double retrieval;
                # vectors from vector_rows are merged in below.
                text_sql = f"""
                    SELECT c.id AS chunk_id, c.article_id, c.chunk_text,
                           a.url, a.title, a.author, a.source, a.date_published, a.metadata,
                           bigm_similarity(c.tsv, %s) AS text_score
                    FROM chunks c
                    JOIN articles a ON a.id = c.article_id
                    {text_where_sql}
                    ORDER BY text_score DESC
                    LIMIT %s
                """
                text_params = [query, query] + _params + [num_results]

                try:
                    await cur.execute(text_sql, text_params)
                    text_rows = await cur.fetchall()
                except Exception as e:
                    logger.error(f"Text search failed, using vector-only: {e}")
                    sentry_sdk.capture_exception(e)
                    text_rows = []

                # --- Union merge (deduplicate by chunk_id) ---
                # Build lookup: chunk_id → embedding for vector-searched rows
                vector_row_embeddings: dict = {}
                if include_vectors:
                    for row in vector_rows:
                        cid = row["chunk_id"]
                        emb = row.get("embedding")
                        if emb is not None:
                            # pgvector returns numpy arrays or lists; normalise to list[float]
                            if hasattr(emb, 'tolist'):
                                emb = emb.tolist()
                            else:
                                emb = [float(v) for v in emb]
                            vector_row_embeddings[cid] = emb

                seen_chunk_ids = set()
                merged = []
                for row in vector_rows:
                    if row["chunk_id"] not in seen_chunk_ids:
                        seen_chunk_ids.add(row["chunk_id"])
                        merged.append(row)
                for row in text_rows:
                    if row["chunk_id"] not in seen_chunk_ids:
                        seen_chunk_ids.add(row["chunk_id"])
                        merged.append(row)

                # --- URL dedup: keep only the highest-score chunk per article URL ---
                url_best: dict = {}
                for row in merged:
                    url = row["url"]
                    score = float(row.get("vector_score") or row.get("text_score") or 0.0)
                    if url not in url_best or score > url_best[url]["_best_score"]:
                        url_best[url] = {"row": row, "_best_score": score}
                merged = [entry["row"] for entry in url_best.values()]

                # --- Quality gate: filter out results below minimum thresholds ---
                # Prevents gibberish queries from returning irrelevant results via text search path
                TEXT_SCORE_MIN = 0.05  # minimum pg_bigm similarity for text-only results
                merged = [
                    row for row in merged
                    if float(row.get("vector_score") or 0.0) >= vector_similarity_min
                    or float(row.get("text_score") or 0.0) >= TEXT_SCORE_MIN
                ]

                results = []
                for row in merged:
                    schema_str = self._build_schema_json(row)
                    item = {
                        'url': row["url"],
                        'schema_str': schema_str,
                        'title': row["title"],
                        'source': row["source"],
                        'author': row.get("author") or "",
                        'date_published': row["date_published"].isoformat() if row.get("date_published") else "",
                        'vector_score': float(row.get("vector_score") or 0.0),
                        'text_score': float(row.get("text_score") or 0.0),
                    }
                    if include_vectors:
                        cid = row["chunk_id"]
                        item['vector'] = vector_row_embeddings.get(cid)
                    results.append(item)

                return results

        try:
            raw_results = await self._execute_with_retry(_search_docs)
            duration = time.time() - start_time
            logger.info(f"Search completed in {duration:.2f}s, found {len(raw_results)} results")

            # --- Date filter fallback (mirrors Qdrant provider behaviour) ---
            # If the query had datePublished filters and returned no results,
            # retry without date filters and notify the frontend via the handler flag.
            handler = kwargs.get('handler')
            has_date_filter = kwargs_filters and any(
                f.get('field') == 'datePublished' for f in kwargs_filters
            )
            if not raw_results and has_date_filter:
                logger.warning(
                    "[FILTER] PG date filter returned 0 results — retrying without date filter"
                )
                try:
                    relaxed_filters = [
                        f for f in kwargs_filters
                        if f.get('field') != 'datePublished'
                    ]
                    relaxed_clauses, relaxed_params = self._build_filters(
                        sites, query_params, kwargs_filters=relaxed_filters
                    )

                    raw_results = await self._execute_with_retry(
                        lambda conn: _search_docs(conn, relaxed_clauses, relaxed_params)
                    )
                    logger.warning(
                        f"[FILTER] Relaxed search returned {len(raw_results)} results"
                    )
                    if handler is not None:
                        handler.time_filter_relaxed = True
                        logger.info("[FILTER] handler.time_filter_relaxed set to True")
                    else:
                        logger.warning("[FILTER] No handler available — time_filter_relaxed flag not set")
                except Exception as fallback_err:
                    logger.exception(
                        f"[FILTER] Date-filter fallback search failed: {fallback_err}"
                    )
                    # Graceful degradation: caller gets the original empty list
                    # Error is logged above via logger.exception
            # --- End date filter fallback ---

            # Analytics: Log retrieved documents with scores
            if handler and hasattr(handler, 'query_id'):
                query_logger = get_query_logger()
                try:
                    for position, item in enumerate(raw_results):
                        url = item['url']
                        author = item.get('author', '')
                        date_published = item.get('date_published', '')
                        vector_score = item.get('vector_score', 0.0)
                        text_score = item.get('text_score', 0.0)
                        # Combined hybrid score (vector + text)
                        final_score = vector_score if vector_score > 0 else text_score

                        # Compute derived fields
                        schema_str = item.get('schema_str', '')
                        doc_length = len(schema_str)
                        has_author = 1 if author else 0

                        # Compute recency_days from date_published
                        recency_days = None
                        if date_published:
                            try:
                                date_str = date_published.split('T')[0] if 'T' in date_published else date_published
                                pub_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                                recency_days = (datetime.now(timezone.utc) - pub_date).days
                            except Exception as e:
                                logger.debug(f"Failed to parse date for analytics: {e}")

                        query_logger.log_retrieved_document(
                            query_id=handler.query_id,
                            doc_url=url,
                            doc_title=item.get('title', ''),
                            doc_description='',
                            retrieval_position=position,
                            vector_similarity_score=vector_score,
                            bm25_score=text_score,
                            keyword_boost_score=0.0,
                            final_retrieval_score=final_score,
                            doc_published_date=date_published,
                            doc_author=author,
                            doc_source=item.get('source', ''),
                            retrieval_algorithm='postgres_hybrid',
                            doc_length=doc_length,
                            has_author=has_author,
                            recency_days=recency_days,
                        )
                    logger.info(f"Analytics: Logged {len(raw_results)} retrieved documents for query {handler.query_id}")
                except Exception as log_err:
                    logger.warning(f"Failed to log retrieved documents: {log_err}")

            # Convert back to list-of-lists format expected by downstream code.
            # When include_vectors=True, emit 5-tuples so ranking.py can extract
            # vectors for MMR: [url, schema_str, title, source, vector]
            #
            # AGGREGATOR_KEEP_SCORES flag (default '0' = off): when on, emit a
            # fixed 6-tuple [url, schema_str, title, source, vector_or_None,
            # retrieval_scores] so the XGBoost shadow ranker's retrieval features
            # (index 14-18) are no longer all-zero. Flag off = 4/5-tuple exactly
            # as before (no behavioural change to prod).
            keep_scores = os.environ.get('AGGREGATOR_KEEP_SCORES', '0') == '1'
            results = []
            for item in raw_results:
                row = [item['url'], item['schema_str'], item['title'], item['source']]
                if keep_scores:
                    # Fixed 6-tuple: vector at index 4 (None placeholder when
                    # absent — NOT omitted), retrieval_scores at index 5.
                    if include_vectors and item.get('vector') is not None:
                        row.append(item['vector'])
                    else:
                        row.append(None)
                    vector_score = item.get('vector_score', 0.0)
                    text_score = item.get('text_score', 0.0)
                    row.append({
                        'vector_score':          vector_score,
                        'bm25_score':            text_score,   # text_score is the bm25 component
                        'keyword_boost':         0.0,          # postgres has no such component (yet)
                        'temporal_boost':        0.0,          # postgres has no such component (yet)
                        'final_retrieval_score': max(vector_score, text_score),
                    })
                else:
                    # Legacy behaviour (flag off): 4- or 5-tuple, unchanged.
                    if include_vectors and 'vector' in item and item['vector'] is not None:
                        row.append(item['vector'])
                results.append(row)
            return results
        except Exception as e:
            logger.exception(f"Error in search: {e}")
            raise
    
    async def search_by_url(self, url: str, **kwargs) -> Optional[List[str]]:
        logger.info(f"Retrieving article with URL: {url}")

        async def _search_by_url(conn):
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT a.url, a.title, a.source, a.author, a.date_published, a.content, a.metadata
                    FROM articles a WHERE a.url ILIKE %s
                """, (f"%{url}%",))
                row = await cur.fetchone()

                if row:
                    schema = {
                        "@type": "NewsArticle",
                        "headline": row["title"],
                        "url": row["url"],
                        "datePublished": row["date_published"].isoformat() if row.get("date_published") else None,
                        "articleBody": row.get("content", ""),
                        "source": row["source"],
                    }
                    if row.get("author"):
                        schema["author"] = {"@type": "Person", "name": row["author"]}
                    metadata = row.get("metadata")
                    if metadata and isinstance(metadata, dict):
                        for k, v in metadata.items():
                            if k not in schema:
                                schema[k] = v
                    schema_str = json.dumps(schema, ensure_ascii=False)
                    return [row["url"], schema_str, row["title"], row["source"]]
                return None

        try:
            result = await self._execute_with_retry(_search_by_url)
            if result:
                logger.debug(f"Found article for URL: {url}")
            else:
                logger.warning(f"No article found for URL: {url}")
            return result
        except Exception as e:
            logger.exception(f"Error retrieving article with URL: {url}")
            raise
    
    async def search_all_sites(self, query: str, num_results: int = 50, **kwargs) -> List[List[str]]:
        return await self.search(query, site=[], num_results=num_results, **kwargs)
        
    async def test_connection(self) -> Dict[str, Any]:
        logger.info("Testing PostgreSQL connection")

        config_info = {
            "host": self.host, "port": self.port,
            "database": self.dbname, "username": self.username
        }

        async def _test(conn):
            result = {
                "success": False, "database_version": None,
                "pgvector_installed": False, "pg_bigm_installed": False,
                "articles_table_exists": False, "chunks_table_exists": False,
                "article_count": 0, "chunk_count": 0,
                "configuration": config_info
            }
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT version()")
                    result["database_version"] = (await cur.fetchone())[0]
                    result["success"] = True

                    await cur.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_bigm')")
                    exts = {row[0] for row in await cur.fetchall()}
                    result["pgvector_installed"] = "vector" in exts
                    result["pg_bigm_installed"] = "pg_bigm" in exts

                    for table in ("articles", "chunks"):
                        await cur.execute("""
                            SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)
                        """, (table,))
                        exists = (await cur.fetchone())[0]
                        result[f"{table}_table_exists"] = exists
                        if exists:
                            await cur.execute(f"SELECT COUNT(*) FROM {table}")
                            result[f"{table.rstrip('s')}_count" if table == "articles" else "chunk_count"] = (await cur.fetchone())[0]

            except Exception as e:
                logger.exception("Error testing PostgreSQL connection")
                result["error"] = str(e)
            return result

        try:
            return await self._execute_with_retry(_test)
        except Exception as e:
            logger.exception("Failed to test PostgreSQL connection")
            return {"success": False, "error": str(e), "configuration": config_info}

    async def check_table_schema(self) -> Dict[str, Any]:
        logger.info("Checking table schema for articles + chunks")

        async def _check(conn):
            info = {
                "tables": {},
                "needs_corrections": []
            }
            try:
                async with conn.cursor(row_factory=dict_row) as cur:
                    for table_name, required_cols in [
                        ("articles", ["id", "url", "title", "source", "author", "date_published", "content", "metadata"]),
                        ("chunks", ["id", "article_id", "chunk_index", "chunk_text", "embedding", "tsv"]),
                    ]:
                        await cur.execute("""
                            SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)
                        """, (table_name,))
                        exists = (await cur.fetchone())["exists"]
                        info["tables"][table_name] = {"exists": exists, "columns": {}}

                        if not exists:
                            info["needs_corrections"].append(f"Table '{table_name}' does not exist")
                            continue

                        await cur.execute("""
                            SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s
                        """, (table_name,))
                        cols = await cur.fetchall()
                        for col in cols:
                            info["tables"][table_name]["columns"][col["column_name"]] = col["data_type"]

                        for rc in required_cols:
                            if rc not in info["tables"][table_name]["columns"]:
                                info["needs_corrections"].append(f"Missing column '{rc}' in table '{table_name}'")

            except Exception as e:
                logger.exception(f"Error checking table schema: {e}")
                info["error"] = str(e)
            return info

        try:
            return await self._execute_with_retry(_check)
        except Exception as e:
            logger.exception(f"Failed to check table schema: {e}")
            return {"error": str(e), "needs_corrections": [str(e)]}

    async def get_sites(self, **kwargs) -> Optional[List[str]]:
        async def _get(conn):
            async with conn.cursor() as cur:
                await cur.execute("SELECT DISTINCT source FROM articles ORDER BY source")
                return [row[0] for row in await cur.fetchall()]

        try:
            return await self._execute_with_retry(_get)
        except Exception as e:
            logger.exception(f"Error getting sites: {e}")
            return None
