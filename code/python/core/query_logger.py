# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Query Logging System for Machine Learning Training Data Collection

This module provides comprehensive logging of search queries, retrieval results,
ranking scores, and user interactions for training XGBoost ranking models.

Key Features:
- Async logging (non-blocking)
- Multi-database support (SQLite for local, PostgreSQL for production)
- Privacy-conscious design
- Captures all data needed for feature engineering

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import asyncio
import json
import queue
import time
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pathlib import Path
import threading
from queue import Queue
from misc.logger.logging_config_helper import get_configured_logger
from core.analytics_db import AnalyticsDB
from core.schema_definitions import (
    get_sqlite_schema, get_postgres_schema, get_index_sql,
    ALLOWED_TABLES, ALLOWED_COLUMNS,
)

logger = get_configured_logger("query_logger")


def get_project_root_db_path() -> str:
    """
    Get absolute path to analytics database from project root.

    This ensures consistent database location regardless of working directory.
    Working directory varies by startup method:
    - startup_aiohttp.sh: /c/Users/User/NLWeb/code/python
    - Direct python run: varies

    Returns:
        Absolute path to data/analytics/query_logs.db from project root
    """
    # Get path to this file (core/query_logger.py)
    current_file = Path(__file__).resolve()
    # Navigate up to project root: query_logger.py -> core/ -> python/ -> code/ -> NLWeb/
    project_root = current_file.parent.parent.parent.parent
    # Build absolute path to database
    db_path = project_root / "data" / "analytics" / "query_logs.db"
    return str(db_path)


class QueryLogger:
    """
    Async query logger for collecting ML training data.

    Logs:
    1. Query metadata (text, timestamp, user_id, site, mode)
    2. Retrieval results (documents, scores, positions)
    3. Ranking scores (vector, keyword/BM25, LLM, XGBoost)
    4. User interactions (clicks, dwell time, scroll depth)
    """

    def __init__(self, db_path: str = None):
        """
        Initialize the query logger.

        Args:
            db_path: Path to SQLite database file (used if ANALYTICS_DATABASE_URL not set).
                     If None, uses absolute path from project root.
                     Note: db_path is ignored; always uses the shared singleton AnalyticsDB instance.
        """
        # Always use the shared singleton instance to avoid multiple connection pools
        self.db = AnalyticsDB.get_instance()

        # Async queue for non-blocking logging
        self.log_queue = Queue()
        self.is_running = False
        self.worker_thread = None

        # Lazy init flag: _init_database() is deferred to first _write_to_db()
        # to avoid blocking the event loop with a sync connect() at startup.
        self._db_initialized = False
        self._db_init_lock = threading.Lock()

        # Start async worker thread
        self._start_worker()

        logger.info(f"QueryLogger initialized with {self.db.db_type} database")

    def _ensure_initialized(self):
        """
        Ensure database schema is initialized before first write.

        Uses a threading.Lock so that if multiple worker threads call this
        simultaneously, only one executes _init_database(). Subsequent calls
        are no-ops after _db_initialized is set.
        """
        if self._db_initialized:
            return
        with self._db_init_lock:
            if not self._db_initialized:
                self._init_database()
                self._db_initialized = True

    def _init_database(self):
        """Initialize database schema with all necessary tables."""
        conn = self.db.connect()
        try:
            cursor = conn.cursor()

            # Check if tables exist and need migration
            needs_migration = self._check_schema_migration_needed(cursor)

            if needs_migration:
                logger.info("Detected Schema v1 - migrating to Schema v2...")
                self._migrate_schema_v2(cursor)

            # Get schema SQL based on database type (SQLite or PostgreSQL)
            schema_dict = self._get_database_schema()

            # Create tables (IF NOT EXISTS - safe for both new and existing DBs)
            for table_name, create_sql in schema_dict.items():
                cursor.execute(create_sql)

            # Ensure org_id column exists (idempotent, for existing v2 DBs)
            self._ensure_org_id_column(cursor)

            # Ensure Phase 3 B2B columns exist (idempotent, for existing DBs)
            self._ensure_phase3_columns(cursor)

            # Create indexes
            index_sqls = get_index_sql(self.db.db_type)
            for index_sql in index_sqls:
                cursor.execute(index_sql)

            conn.commit()
            logger.info(f"Database schema initialized successfully ({self.db.db_type})")
        finally:
            conn.close()

    def _check_schema_migration_needed(self, cursor) -> bool:
        """Check if database needs migration from v1 to v2."""
        try:
            # Check if queries table exists
            if self.db.db_type == 'postgres':
                cursor.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name='queries'
                """)
                columns = [row['column_name'] if isinstance(row, dict) else row[0] for row in cursor.fetchall()]
            else:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='queries'")
                if not cursor.fetchone():
                    # No queries table = fresh database, no migration needed
                    return False
                cursor.execute("PRAGMA table_info(queries)")
                columns = [row[1] for row in cursor.fetchall()]

            # If schema_version OR parent_query_id column doesn't exist, we need migration
            needs_migration = 'schema_version' not in columns or 'parent_query_id' not in columns

            if needs_migration and 'parent_query_id' not in columns:
                logger.info("Detected missing parent_query_id column - will add during migration")

            return needs_migration

        except Exception as e:
            logger.warning(f"Error checking schema version: {e}")
            return False

    def _migrate_schema_v2(self, cursor):
        """Migrate existing database from v1 to v2 by adding new columns."""
        logger.info("Starting Schema v1 → v2 migration...")

        try:
            if self.db.db_type == 'postgres':
                # PostgreSQL: Use ADD COLUMN IF NOT EXISTS
                logger.info("Adding v2 columns to queries table...")
                cursor.execute("""
                    ALTER TABLE queries
                    ADD COLUMN IF NOT EXISTS query_length_words INTEGER,
                    ADD COLUMN IF NOT EXISTS query_length_chars INTEGER,
                    ADD COLUMN IF NOT EXISTS has_temporal_indicator INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100),
                    ADD COLUMN IF NOT EXISTS parent_query_id VARCHAR(255),
                    ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
                """)

                logger.info("Adding v2 columns to retrieved_documents table...")
                cursor.execute("""
                    ALTER TABLE retrieved_documents
                    ADD COLUMN IF NOT EXISTS query_term_count INTEGER,
                    ADD COLUMN IF NOT EXISTS doc_length INTEGER,
                    ADD COLUMN IF NOT EXISTS title_exact_match INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS desc_exact_match INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS keyword_overlap_ratio DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS recency_days INTEGER,
                    ADD COLUMN IF NOT EXISTS has_author INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS retrieval_algorithm VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
                """)

                logger.info("Adding v2 columns to ranking_scores table...")
                cursor.execute("""
                    ALTER TABLE ranking_scores
                    ADD COLUMN IF NOT EXISTS relative_score DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS score_percentile DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS xgboost_confidence DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
                """)

                logger.info("Adding v2 column to user_interactions table...")
                cursor.execute("""
                    ALTER TABLE user_interactions
                    ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
                """)

            else:
                # SQLite: Must execute one ALTER TABLE per column
                alter_statements = [
                    # queries table
                    ("queries", "query_length_words", "INTEGER"),
                    ("queries", "query_length_chars", "INTEGER"),
                    ("queries", "has_temporal_indicator", "INTEGER DEFAULT 0"),
                    ("queries", "embedding_model", "TEXT"),
                    ("queries", "parent_query_id", "TEXT"),
                    ("queries", "schema_version", "INTEGER DEFAULT 2"),
                    # retrieved_documents table
                    ("retrieved_documents", "query_term_count", "INTEGER"),
                    ("retrieved_documents", "doc_length", "INTEGER"),
                    ("retrieved_documents", "title_exact_match", "INTEGER DEFAULT 0"),
                    ("retrieved_documents", "desc_exact_match", "INTEGER DEFAULT 0"),
                    ("retrieved_documents", "keyword_overlap_ratio", "REAL"),
                    ("retrieved_documents", "recency_days", "INTEGER"),
                    ("retrieved_documents", "has_author", "INTEGER DEFAULT 0"),
                    ("retrieved_documents", "retrieval_algorithm", "TEXT"),
                    ("retrieved_documents", "schema_version", "INTEGER DEFAULT 2"),
                    # ranking_scores table
                    ("ranking_scores", "relative_score", "REAL"),
                    ("ranking_scores", "score_percentile", "REAL"),
                    ("ranking_scores", "xgboost_confidence", "REAL"),
                    ("ranking_scores", "schema_version", "INTEGER DEFAULT 2"),
                    # user_interactions table
                    ("user_interactions", "schema_version", "INTEGER DEFAULT 2"),
                ]

                for table, column, column_type in alter_statements:
                    try:
                        sql = f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
                        cursor.execute(sql)
                        logger.debug(f"Added column {table}.{column}")
                    except Exception as e:
                        # Ignore "duplicate column" errors
                        if "duplicate column name" not in str(e).lower() and "already exists" not in str(e).lower():
                            raise

            logger.info("[OK] Schema v2 migration completed successfully.")

        except Exception as e:
            logger.error(f"[FAILED] Schema migration failed: {e}")
            raise

    def _ensure_org_id_column(self, cursor):
        """Add org_id column to queries table if missing (idempotent)."""
        try:
            if self.db.db_type == 'postgres':
                cursor.execute("ALTER TABLE queries ADD COLUMN IF NOT EXISTS org_id VARCHAR(255)")
            else:
                cursor.execute("ALTER TABLE queries ADD COLUMN org_id TEXT")
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                logger.warning(f"Failed to add org_id column: {e}")

    def _ensure_phase3_columns(self, cursor):
        """
        Add Phase 3 B2B columns to user_interactions and user_feedback tables
        if missing (idempotent — safe to run on both new and existing DBs).
        """
        # Columns to add: (table, column, sqlite_type, pg_type)
        new_columns = [
            ("user_interactions", "user_id", "TEXT", "VARCHAR(255)"),
            ("user_interactions", "org_id", "TEXT", "VARCHAR(255)"),
            ("user_feedback", "query_id", "TEXT", "VARCHAR(255)"),
            ("user_feedback", "user_id", "TEXT", "VARCHAR(255)"),
            ("user_feedback", "org_id", "TEXT", "VARCHAR(255)"),
        ]
        for table, column, sqlite_type, pg_type in new_columns:
            try:
                if self.db.db_type == 'postgres':
                    cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {pg_type}"
                    )
                else:
                    cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_type}"
                    )
            except Exception as e:
                if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                    logger.warning(f"Failed to add {table}.{column}: {e}")

    def _get_database_schema(self) -> Dict[str, str]:
        """Get database schema SQL for current database type."""
        if self.db.db_type == 'postgres':
            return get_postgres_schema()
        else:
            return get_sqlite_schema()

    def _start_worker(self):
        """Start background worker thread for async logging."""
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Logging worker thread started")

    def _worker_loop(self):
        """Background worker that processes log queue."""
        while self.is_running:
            try:
                log_entry = self.log_queue.get(timeout=1.0)
                table_name = log_entry.get("table")
                data = log_entry.get("data")
                if table_name and data:
                    self._write_to_db(table_name, data)
                self.log_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in logging worker: {e}")

    def _write_to_db(self, table_name: str, data: Dict[str, Any]):
        """Write data to database (synchronous, called by worker thread)."""
        # Lazy init: ensure schema exists before first write (avoids blocking event loop at startup)
        self._ensure_initialized()

        # Validate table name against whitelist
        if table_name not in ALLOWED_TABLES:
            logger.error(f"Rejected write to invalid table name: {table_name}")
            return

        # Validate column names against whitelist
        invalid_cols = set(data.keys()) - ALLOWED_COLUMNS
        if invalid_cols:
            logger.error(f"Rejected write with invalid column names: {invalid_cols}")
            return

        max_retries = 5  # Increased from 3
        # Exponential backoff: 0.5s, 1s, 2s, 4s, 8s
        retry_delays = [0.5, 1.0, 2.0, 4.0, 8.0]

        for attempt in range(max_retries):
            conn = None
            try:
                conn = self.db.connect()
                cursor = conn.cursor()

                # Build INSERT statement dynamically
                columns = ", ".join(data.keys())
                # Use appropriate placeholder for database type
                placeholder = "%s" if self.db.db_type == 'postgres' else "?"
                placeholders = ", ".join([placeholder for _ in data])
                query = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"

                cursor.execute(query, list(data.values()))
                conn.commit()
                return  # Success, exit retry loop

            except Exception as e:
                error_msg = str(e)
                # Check if it's a foreign key error
                if "foreign key constraint" in error_msg.lower() and attempt < max_retries - 1:
                    # Wait and retry with exponential backoff
                    delay = retry_delays[attempt]
                    time.sleep(delay)
                    logger.warning(
                        f"Foreign key constraint error on {table_name}, "
                        f"retrying in {delay}s (attempt {attempt + 2}/{max_retries})"
                    )
                else:
                    # Log error but don't crash
                    logger.error(
                        f"Failed to write to {table_name} after {attempt + 1} attempts: {e}"
                    )
                    return
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    # Regex pattern for detecting temporal indicators in Chinese queries
    # Compiled once at class level for efficiency
    import re as _re
    _TEMPORAL_PATTERN = _re.compile(
        r'最新|近日|去年|今年|上週|本月|昨天|前天|上個月|近期|最近|'
        r'今天|本週|本年|上半年|下半年|第[一二三四]季|'
        r'2020|2021|2022|2023|2024|2025|2026|2027'
    )

    def log_query_start(
        self,
        query_id: str,
        user_id: str,
        query_text: str,
        site: str,
        mode: str,
        decontextualized_query: str = "",
        session_id: str = "",
        conversation_id: str = "",
        model: str = "",
        parent_query_id: str = None,
        org_id: str = None,
        embedding_model: str = "",
    ) -> None:
        """
        Log the start of a query (SYNCHRONOUS - ensures queries table is written first).

        Args:
            query_id: Unique identifier for this query
            user_id: User identifier (anonymized if needed)
            query_text: Original query text
            site: Site being queried
            mode: Query mode (list, generate, summarize)
            decontextualized_query: Decontextualized version
            session_id: Session identifier
            conversation_id: Conversation identifier
            model: LLM model being used
            parent_query_id: Parent query ID (for generate requests that follow summarize)
            org_id: Organization identifier (for B2B analytics)
            embedding_model: Embedding model used for vector search
        """
        # Compute query length metrics
        query_length_chars = len(query_text) if query_text else 0
        query_length_words = len(query_text.split()) if query_text else 0

        # Detect temporal indicators (simple regex, no LLM call)
        has_temporal_indicator = 1 if (query_text and self._TEMPORAL_PATTERN.search(query_text)) else 0

        data = {
            "query_id": query_id,
            "timestamp": time.time(),
            "user_id": user_id,
            "org_id": org_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "query_text": query_text,
            "decontextualized_query": decontextualized_query,
            "site": site,
            "mode": mode,
            "model": model,
            "parent_query_id": parent_query_id,
            "query_length_chars": query_length_chars,
            "query_length_words": query_length_words,
            "has_temporal_indicator": has_temporal_indicator,
            "embedding_model": embedding_model or "",
            "schema_version": 2,
        }

        # Write synchronously to ensure queries table has the record BEFORE
        # any child tables (retrieved_documents, ranking_scores, etc.) are written
        self._write_to_db("queries", data)

    def log_query_complete(
        self,
        query_id: str,
        latency_total_ms: float,
        latency_retrieval_ms: float = 0,
        latency_ranking_ms: float = 0,
        latency_generation_ms: float = 0,
        num_results_retrieved: int = 0,
        num_results_ranked: int = 0,
        num_results_returned: int = 0,
        cost_usd: float = 0,
        error_occurred: bool = False,
        error_message: str = ""
    ) -> None:
        """
        Update query with completion metrics.

        Args:
            query_id: Query identifier
            latency_total_ms: Total query latency
            latency_retrieval_ms: Retrieval phase latency
            latency_ranking_ms: Ranking phase latency
            latency_generation_ms: Generation phase latency
            num_results_retrieved: Number of documents retrieved
            num_results_ranked: Number of documents ranked
            num_results_returned: Number of results returned to user
            cost_usd: Estimated cost in USD
            error_occurred: Whether an error occurred
            error_message: Error message if any
        """
        conn = None
        try:
            conn = self.db.connect()
            cursor = conn.cursor()

            # Use appropriate placeholder for database type
            placeholder = "%s" if self.db.db_type == 'postgres' else "?"

            query_sql = f"""
                UPDATE queries SET
                    latency_total_ms = {placeholder},
                    latency_retrieval_ms = {placeholder},
                    latency_ranking_ms = {placeholder},
                    latency_generation_ms = {placeholder},
                    num_results_retrieved = {placeholder},
                    num_results_ranked = {placeholder},
                    num_results_returned = {placeholder},
                    cost_usd = {placeholder},
                    error_occurred = {placeholder},
                    error_message = {placeholder}
                WHERE query_id = {placeholder}
            """

            cursor.execute(query_sql, (
                latency_total_ms,
                latency_retrieval_ms,
                latency_ranking_ms,
                latency_generation_ms,
                num_results_retrieved,
                num_results_ranked,
                num_results_returned,
                cost_usd,
                1 if error_occurred else 0,
                error_message,
                query_id
            ))

            conn.commit()
        except Exception as e:
            logger.error(f"Error updating query completion: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def log_retrieved_document(
        self,
        query_id: str,
        doc_url: str,
        doc_title: str,
        doc_description: str,
        retrieval_position: int,
        vector_similarity_score: float = 0,
        keyword_boost_score: float = 0,
        bm25_score: float = 0,
        temporal_boost: float = 0,
        domain_match: bool = False,
        final_retrieval_score: float = 0,
        doc_published_date: str = "",
        doc_author: str = "",
        doc_source: str = "",
        retrieval_algorithm: str = "",
        doc_length: int = None,
        has_author: int = None,
        recency_days: int = None,
    ) -> None:
        """
        Log a retrieved document (before ranking).

        Args:
            query_id: Query identifier
            doc_url: Document URL
            doc_title: Document title
            doc_description: Document description
            retrieval_position: Position in retrieval results
            vector_similarity_score: Embedding similarity score
            keyword_boost_score: Keyword boosting score
            bm25_score: BM25 score
            temporal_boost: Temporal boosting score
            domain_match: Whether domain matched
            final_retrieval_score: Combined retrieval score
            doc_published_date: Publication date
            doc_author: Author name
            doc_source: Source/publisher
            retrieval_algorithm: Algorithm used for retrieval (e.g. 'qdrant_hybrid_search', 'postgres_hybrid')
            doc_length: Length of the document text in characters
            has_author: 1 if author is present, 0 otherwise
            recency_days: Days since publication (None if unknown)
        """
        data = {
            "query_id": query_id,
            "doc_url": doc_url,
            "doc_title": doc_title,
            "doc_description": doc_description[:500] if doc_description else "",  # Truncate
            "doc_published_date": doc_published_date,
            "doc_author": doc_author,
            "doc_source": doc_source,
            "retrieval_position": retrieval_position,
            "vector_similarity_score": vector_similarity_score,
            "keyword_boost_score": keyword_boost_score,
            "bm25_score": bm25_score,
            "temporal_boost": temporal_boost,
            "domain_match": 1 if domain_match else 0,
            "final_retrieval_score": final_retrieval_score,
        }

        # Optional instant-fillable ML fields
        if retrieval_algorithm:
            data["retrieval_algorithm"] = retrieval_algorithm
        if doc_length is not None:
            data["doc_length"] = doc_length
        if has_author is not None:
            data["has_author"] = has_author
        if recency_days is not None:
            data["recency_days"] = recency_days

        self.log_queue.put({"table": "retrieved_documents", "data": data})

    def log_ranking_score(
        self,
        query_id: str,
        doc_url: str,
        ranking_position: int,
        llm_final_score: float = 0,
        llm_snippet: str = "",
        xgboost_score: float = 0,
        xgboost_confidence: float = 0,
        mmr_diversity_score: float = 0,
        final_ranking_score: float = 0,
        ranking_method: str = "llm",
        # Deprecated sub-score params kept for backwards compatibility (ignored)
        llm_relevance_score: float = 0,
        llm_keyword_score: float = 0,
        llm_semantic_score: float = 0,
        llm_freshness_score: float = 0,
        llm_authority_score: float = 0,
    ) -> None:
        """
        Log ranking scores for a document.

        Args:
            query_id: Query identifier
            doc_url: Document URL
            ranking_position: Position after ranking (0-based)
            llm_final_score: LLM combined score
            llm_snippet: LLM-generated snippet
            xgboost_score: XGBoost predicted score
            xgboost_confidence: XGBoost confidence
            mmr_diversity_score: MMR diversity score
            final_ranking_score: Final combined score
            ranking_method: Method used (llm, xgboost, hybrid, mmr)
        """
        data = {
            "query_id": query_id,
            "doc_url": doc_url,
            "ranking_position": ranking_position,
            "llm_final_score": llm_final_score,
            "llm_snippet": llm_snippet[:200] if llm_snippet else "",  # Truncate
            "xgboost_score": xgboost_score,
            "xgboost_confidence": xgboost_confidence,
            "mmr_diversity_score": mmr_diversity_score,
            "final_ranking_score": final_ranking_score,
            "ranking_method": ranking_method,
        }

        self.log_queue.put({"table": "ranking_scores", "data": data})

    def update_ranking_positions(
        self,
        query_id: str,
        positions: list,
    ) -> None:
        """
        Batch-update ranking_position for all documents of a query after final sort.

        Args:
            query_id: Query identifier
            positions: List of (doc_url, position) tuples in final sorted order
        """
        conn = None
        try:
            conn = self.db.connect()
            cursor = conn.cursor()
            placeholder = "%s" if self.db.db_type == 'postgres' else "?"

            for doc_url, position in positions:
                cursor.execute(
                    f"UPDATE ranking_scores SET ranking_position = {placeholder} "
                    f"WHERE query_id = {placeholder} AND doc_url = {placeholder} AND ranking_method = 'llm'",
                    (position, query_id, doc_url)
                )

            conn.commit()
            logger.debug(f"Updated ranking_position for {len(positions)} documents in query {query_id}")
        except Exception as e:
            logger.error(f"Failed to update ranking positions for query {query_id}: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def log_mmr_score(
        self,
        query_id: str,
        doc_url: str,
        mmr_score: float,
        ranking_position: int
    ) -> None:
        """
        Update ranking_scores table with MMR diversity score.
        This is called after MMR re-ranking to add diversity scores.

        Args:
            query_id: Query identifier
            doc_url: Document URL
            mmr_score: MMR diversity score
            ranking_position: Final position after MMR
        """
        # Log as a ranking score update with just MMR info
        data = {
            "query_id": query_id,
            "doc_url": doc_url,
            "ranking_position": ranking_position,
            "mmr_diversity_score": mmr_score,
            "ranking_method": "mmr",
            # Other scores will be 0/empty for this partial update
            "llm_final_score": 0,
            "final_ranking_score": mmr_score,
        }

        self.log_queue.put({"table": "ranking_scores", "data": data})

    def log_xgboost_scores(
        self,
        query_id: str,
        doc_url: str,
        xgboost_score: float,
        xgboost_confidence: float,
        ranking_position: int
    ) -> None:
        """
        Log XGBoost predictions in shadow mode (Phase A).

        Creates a NEW row in ranking_scores table with ranking_method='xgboost_shadow'.
        This follows the Multiple INSERTs pattern - same (query_id, doc_url) will have:
        - Row 1: ranking_method='llm' (LLM scores)
        - Row 2: ranking_method='xgboost_shadow' (XGBoost predictions)
        - Row 3: ranking_method='mmr' (MMR scores)

        Args:
            query_id: Query identifier
            doc_url: Document URL
            xgboost_score: XGBoost predicted relevance score (0-1)
            xgboost_confidence: XGBoost prediction confidence (0-1)
            ranking_position: Position in ranked results
        """
        data = {
            "query_id": query_id,
            "doc_url": doc_url,
            "ranking_position": ranking_position,
            "xgboost_score": xgboost_score,
            "xgboost_confidence": xgboost_confidence,
            "ranking_method": "xgboost_shadow",
            # Placeholder values for other scores (not used in Phase A)
            "llm_final_score": 0,
            "mmr_diversity_score": 0,
            "final_ranking_score": 0,
        }

        self.log_queue.put({"table": "ranking_scores", "data": data})

    def log_user_interaction(
        self,
        query_id: str,
        doc_url: str,
        interaction_type: str,
        result_position: int = 0,
        dwell_time_ms: float = 0,
        scroll_depth_percent: float = 0,
        clicked: bool = False,
        client_user_agent: str = "",
        client_ip_hash: str = "",
        user_id: str = None,
        org_id: str = None,
    ) -> None:
        """
        Log user interaction with a result.

        Args:
            query_id: Query identifier
            doc_url: Document URL
            interaction_type: Type (click, view, scroll, etc.)
            result_position: Position in results
            dwell_time_ms: Time spent on result
            scroll_depth_percent: How far user scrolled
            clicked: Whether result was clicked
            client_user_agent: User agent string
            client_ip_hash: Hashed IP address
            user_id: User identifier (for B2B analytics)
            org_id: Organization identifier (for B2B analytics)
        """
        data = {
            "query_id": query_id,
            "doc_url": doc_url,
            "interaction_type": interaction_type,
            "interaction_timestamp": time.time(),
            "result_position": result_position,
            "dwell_time_ms": dwell_time_ms,
            "scroll_depth_percent": scroll_depth_percent,
            "clicked": 1 if clicked else 0,
            "client_user_agent": client_user_agent,
            "client_ip_hash": client_ip_hash,
        }

        if user_id is not None:
            data["user_id"] = user_id
        if org_id is not None:
            data["org_id"] = org_id

        self.log_queue.put({"table": "user_interactions", "data": data})

    def log_tier_6_enrichment(
        self,
        query_id: str,
        source_type: str,
        cache_hit: bool = False,
        latency_ms: int = 0,
        timeout_occurred: bool = False,
        result_count: int = 0,
        metadata: Dict[str, Any] = None
    ) -> None:
        """
        Log Tier 6 knowledge enrichment activity.

        Args:
            query_id: Query identifier
            source_type: Type of enrichment source ('google_search', 'wikipedia', 'llm_knowledge')
            cache_hit: Whether result came from cache
            latency_ms: Latency in milliseconds
            timeout_occurred: Whether timeout occurred
            result_count: Number of results returned
            metadata: Additional metadata (stored as JSON)
        """
        data = {
            "query_id": query_id,
            "source_type": source_type,
            "cache_hit": 1 if cache_hit else 0,
            "latency_ms": latency_ms,
            "timeout_occurred": 1 if timeout_occurred else 0,
            "result_count": result_count,
            "timestamp": time.time(),
            "metadata": json.dumps(metadata) if metadata else None,
            "schema_version": 2
        }

        self.log_queue.put({"table": "tier_6_enrichment", "data": data})

    async def get_query_stats(self, days: int = 7) -> Dict[str, Any]:
        """
        Get query statistics for the past N days.

        Args:
            days: Number of days to look back

        Returns:
            Dictionary with statistics
        """
        try:
            cutoff_timestamp = time.time() - (days * 24 * 60 * 60)

            def get_val(row, key):
                """Extract scalar value from fetchone() dict result."""
                if row is None:
                    return 0
                # Explicit alias key first, then fallback to first value
                if key in row:
                    return row[key] or 0
                return list(row.values())[0] or 0

            # Total queries
            row = await self.db.fetchone(
                "SELECT COUNT(*) AS total_count FROM queries WHERE timestamp > ?",
                (cutoff_timestamp,)
            )
            total_queries = get_val(row, 'total_count')

            # Average latency
            row = await self.db.fetchone(
                "SELECT AVG(latency_total_ms) AS avg_latency FROM queries WHERE timestamp > ? AND latency_total_ms IS NOT NULL",
                (cutoff_timestamp,)
            )
            avg_latency = get_val(row, 'avg_latency')

            # Total cost
            row = await self.db.fetchone(
                "SELECT SUM(cost_usd) AS total_cost FROM queries WHERE timestamp > ? AND cost_usd IS NOT NULL",
                (cutoff_timestamp,)
            )
            total_cost = get_val(row, 'total_cost')

            # Error rate
            row = await self.db.fetchone(
                "SELECT COUNT(*) AS error_count FROM queries WHERE timestamp > ? AND error_occurred = 1",
                (cutoff_timestamp,)
            )
            error_count = get_val(row, 'error_count')
            error_rate = error_count / total_queries if total_queries > 0 else 0

            # Click-through rate
            row = await self.db.fetchone(
                "SELECT COUNT(DISTINCT query_id) AS click_count FROM user_interactions WHERE interaction_timestamp > ? AND clicked = 1",
                (cutoff_timestamp,)
            )
            queries_with_clicks = get_val(row, 'click_count')
            ctr = queries_with_clicks / total_queries if total_queries > 0 else 0

            return {
                "total_queries": total_queries,
                "avg_latency_ms": avg_latency,
                "total_cost_usd": total_cost,
                "error_rate": error_rate,
                "click_through_rate": ctr,
                "days": days,
            }
        except Exception as e:
            logger.error(f"Error getting query stats: {e}")
            return {}

    def log_feedback(
        self,
        query: str,
        answer_snippet: str,
        rating: str,
        comment: str = "",
        session_id: str = "",
        query_id: str = None,
        user_id: str = None,
        org_id: str = None,
    ):
        """Log user feedback (thumbs up/down + optional comment).

        Args:
            query: The search query associated with the feedback
            answer_snippet: First ~200 chars of the answer being rated
            rating: 'positive' or 'negative'
            comment: Optional user comment
            session_id: Optional session identifier
            query_id: Optional query identifier (FK to queries table)
            user_id: Optional user identifier (for B2B analytics)
            org_id: Optional organization identifier (for B2B analytics)
        """
        data = {
            "query": query or "",
            "answer_snippet": (answer_snippet or "")[:500],
            "rating": rating,
            "comment": comment or "",
            "session_id": session_id or "",
            "created_at": time.time()
        }
        if query_id is not None:
            data["query_id"] = query_id
        if user_id is not None:
            data["user_id"] = user_id
        if org_id is not None:
            data["org_id"] = org_id
        self.log_queue.put({"table": "user_feedback", "data": data})
        logger.info(f"Queued feedback: rating={rating}, query='{(query or '')[:50]}'")

    def shutdown(self):
        """Gracefully shutdown the logger."""
        logger.info("Shutting down QueryLogger...")
        self.is_running = False

        # Wait for queue to empty
        self.log_queue.join()

        if self.worker_thread:
            self.worker_thread.join(timeout=5)

        logger.info("QueryLogger shutdown complete")


# Global singleton instance
_global_logger = None


def get_query_logger(db_path: str = None) -> QueryLogger:
    """
    Get the global QueryLogger instance (singleton pattern).

    Args:
        db_path: Path to database file. If None, uses absolute path from project root.

    Returns:
        QueryLogger instance
    """
    global _global_logger
    if _global_logger is None:
        _global_logger = QueryLogger(db_path=db_path)
    return _global_logger
