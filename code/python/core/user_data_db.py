# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Database abstraction layer for user-uploaded files and private knowledge base.

Supports both SQLite (local development) and PostgreSQL (production).
Uses async connections for PostgreSQL to avoid blocking the event loop.
Reads USER_DATA_DATABASE_URL first, then falls back to POSTGRES_CONNECTION_STRING /
DATABASE_URL / ANALYTICS_DATABASE_URL (unified with auth_db.py).
"""

import os
import sqlite3
import asyncio
from typing import Any, List, Dict, Optional, Tuple
from pathlib import Path
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_data_db")

# Try to import PostgreSQL libraries (optional)
try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logger.warning("PostgreSQL libraries not available, falling back to SQLite")


def get_project_root_db_path() -> str:
    """
    Get absolute path to user data database from project root.

    Returns:
        Absolute path to data/user_data/user_data.db from project root
    """
    data_dir = os.environ.get('NLWEB_DATA_DIR')
    if data_dir:
        db_path = Path(data_dir) / "user_data" / "user_data.db"
        return str(db_path)
    current_file = Path(__file__).resolve()
    # user_data_db.py -> core/ -> python/ -> code/ -> NLWeb/
    project_root = current_file.parent.parent.parent.parent
    db_path = project_root / "data" / "user_data" / "user_data.db"
    return str(db_path)


class UserDataDB:
    """
    Database abstraction layer that supports both SQLite and PostgreSQL.

    PostgreSQL: uses psycopg async connection pool (non-blocking).
    SQLite: uses sync connections wrapped in asyncio.to_thread (dev only).

    Environment variable priority:
      USER_DATA_DATABASE_URL  (explicit override)
      → POSTGRES_CONNECTION_STRING  (unified main PG)
      → DATABASE_URL
      → ANALYTICS_DATABASE_URL
      → SQLite fallback
    """

    _instance = None

    @classmethod
    def get_instance(cls) -> 'UserDataDB':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, db_path: str = None):
        """
        Initialize database connection settings.

        Args:
            db_path: Path to SQLite database (used if no PG URL is found).
                     If None, uses absolute path from project root.
        """
        if db_path is None:
            db_path = get_project_root_db_path()

        # Priority: USER_DATA_DATABASE_URL → POSTGRES_CONNECTION_STRING →
        #           DATABASE_URL → ANALYTICS_DATABASE_URL → SQLite
        self.database_url = (
            os.environ.get('USER_DATA_DATABASE_URL')
            or os.environ.get('POSTGRES_CONNECTION_STRING')
            or os.environ.get('DATABASE_URL')
            or os.environ.get('ANALYTICS_DATABASE_URL')
        )
        self.db_path = Path(db_path)
        self.db_type = 'postgres' if self.database_url and POSTGRES_AVAILABLE else 'sqlite'
        self._initialized = False
        self._pool = None
        self._pool_lock = asyncio.Lock()

        logger.info(f"User data database type: {self.db_type}")

        if self.db_type == 'sqlite':
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Using SQLite database at: {self.db_path.absolute()}")
        else:
            masked = self.database_url.split('@')[1] if '@' in self.database_url else 'connected'
            logger.info(f"Using PostgreSQL user data database: {masked}")

    # ── Async query interface ─────────────────────────────────────

    async def fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict]:
        """Execute query and return one row as dict."""
        if self.db_type == 'postgres':
            return await self._pg_fetchone(query, params)
        else:
            return await asyncio.to_thread(self._sqlite_fetchone, query, params)

    async def fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        """Execute query and return all rows as list of dicts."""
        if self.db_type == 'postgres':
            return await self._pg_fetchall(query, params)
        else:
            return await asyncio.to_thread(self._sqlite_fetchall, query, params)

    async def execute(self, query: str, params: Optional[Tuple] = None):
        """Execute a query (INSERT/UPDATE/DELETE) and commit."""
        if self.db_type == 'postgres':
            await self._pg_execute(query, params)
        else:
            await asyncio.to_thread(self._sqlite_execute, query, params)

    # ── PostgreSQL connection pool ───────────────────────────────

    async def _get_pool(self) -> 'AsyncConnectionPool':
        """Get or create the async connection pool (lazy init, thread-safe)."""
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = AsyncConnectionPool(
                        conninfo=self.database_url,
                        min_size=1,
                        max_size=5,
                        open=False,
                    )
                    await self._pool.open()
                    logger.info("User data DB connection pool initialized")
        return self._pool

    async def close(self):
        """Close the connection pool. Call on shutdown."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("User data DB connection pool closed")

    # ── PostgreSQL async methods ──────────────────────────────────

    def _adapt_query_pg(self, query: str) -> str:
        """Convert ? placeholders to %s for psycopg."""
        return query.replace('?', '%s')

    async def _pg_fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict]:
        query = self._adapt_query_pg(query)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
                return dict(row) if row else None

    async def _pg_fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        query = self._adapt_query_pg(query)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def _pg_execute(self, query: str, params: Optional[Tuple] = None):
        query = self._adapt_query_pg(query)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
            await conn.commit()

    # ── SQLite sync methods (wrapped in to_thread) ────────────────

    def _sqlite_connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _sqlite_fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _sqlite_fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _sqlite_execute(self, query: str, params: Optional[Tuple] = None):
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            conn.commit()
        finally:
            conn.close()

    # ── Initialization ────────────────────────────────────────────

    async def initialize(self):
        """Initialize database tables (async). Called once at startup."""
        if self._initialized:
            return
        if self.db_type == 'sqlite':
            await asyncio.to_thread(self._init_database_sync)
        else:
            await self._init_database_async()
        self._initialized = True

    async def _init_database_async(self):
        """Create tables on PostgreSQL."""
        try:
            async with await psycopg.AsyncConnection.connect(
                self.database_url, autocommit=True, connect_timeout=5
            ) as conn:
                async with conn.cursor() as cur:
                    for table_name, create_sql in self._get_postgres_schema().items():
                        try:
                            await cur.execute(create_sql)
                            logger.debug(f"User data table ensured: {table_name}")
                        except Exception as e:
                            logger.error(f"Failed to create user data table {table_name}: {e}")
                            raise

                    for index_sql in self.get_index_sql():
                        try:
                            await cur.execute(index_sql)
                        except Exception as e:
                            logger.warning(f"Index creation skipped: {e}")

                    # Migration: add org_id column if missing
                    try:
                        await cur.execute(
                            "ALTER TABLE user_sources ADD COLUMN IF NOT EXISTS org_id VARCHAR(255)"
                        )
                        logger.info("Migration: ensured org_id column exists in user_sources (PostgreSQL)")
                    except Exception as e:
                        logger.warning(f"org_id migration skipped: {e}")

            logger.info("User data database initialized (PostgreSQL async)")
        except Exception as e:
            masked = self.database_url.split('@')[1] if '@' in self.database_url else self.database_url
            logger.error(f"無法連線到 PostgreSQL ({masked})。是不是忘記開 Docker Desktop？")
            logger.error(f"Failed to initialize user data database: {e}", exc_info=True)
            raise

    def _init_database_sync(self):
        """Create tables on SQLite."""
        try:
            conn = self._sqlite_connect()
            cursor = conn.cursor()

            for table_name, create_sql in self._get_sqlite_schema().items():
                try:
                    cursor.execute(create_sql)
                    logger.debug(f"User data table ensured: {table_name}")
                except Exception as e:
                    logger.error(f"Failed to create user data table {table_name}: {e}")
                    raise

            for index_sql in self.get_index_sql():
                try:
                    cursor.execute(index_sql)
                except Exception as e:
                    logger.warning(f"Index creation skipped: {e}")

            # Migration: add org_id column if missing
            cursor.execute("PRAGMA table_info(user_sources)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'org_id' not in columns:
                cursor.execute("ALTER TABLE user_sources ADD COLUMN org_id TEXT")
                logger.info("Migration: added org_id column to user_sources")

            conn.commit()
            conn.close()
            logger.info("User data database initialized (SQLite)")
        except Exception as e:
            logger.error(f"Failed to initialize user data database: {e}", exc_info=True)
            raise

    # ── Schema definitions ────────────────────────────────────────

    def _get_sqlite_schema(self) -> Dict[str, str]:
        """SQLite schema definitions."""
        return {
            'user_sources': """
                CREATE TABLE IF NOT EXISTS user_sources (
                    source_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT,
                    name TEXT NOT NULL,
                    file_type TEXT,
                    status TEXT NOT NULL,
                    size_bytes INTEGER,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """,
            'user_documents': """
                CREATE TABLE IF NOT EXISTS user_documents (
                    doc_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    checksum TEXT,
                    chunk_count INTEGER,
                    processed_at REAL,
                    FOREIGN KEY (source_id) REFERENCES user_sources(source_id) ON DELETE CASCADE
                )
            """
        }

    def _get_postgres_schema(self) -> Dict[str, str]:
        """PostgreSQL schema definitions."""
        return {
            'user_sources': """
                CREATE TABLE IF NOT EXISTS user_sources (
                    source_id VARCHAR(255) PRIMARY KEY,
                    user_id VARCHAR(255) NOT NULL,
                    org_id VARCHAR(255),
                    name VARCHAR(500) NOT NULL,
                    file_type VARCHAR(50),
                    status VARCHAR(50) NOT NULL,
                    size_bytes INTEGER,
                    error_message TEXT,
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
            """,
            'user_documents': """
                CREATE TABLE IF NOT EXISTS user_documents (
                    doc_id VARCHAR(255) PRIMARY KEY,
                    source_id VARCHAR(255) NOT NULL,
                    checksum VARCHAR(64),
                    chunk_count INTEGER,
                    processed_at DOUBLE PRECISION,
                    FOREIGN KEY (source_id) REFERENCES user_sources(source_id) ON DELETE CASCADE
                )
            """
        }

    def get_schema_sql(self) -> Dict[str, str]:
        """Get SQL statements for creating tables."""
        if self.db_type == 'postgres':
            return self._get_postgres_schema()
        else:
            return self._get_sqlite_schema()

    def get_index_sql(self) -> List[str]:
        """Get SQL statements for creating indexes."""
        return [
            "CREATE INDEX IF NOT EXISTS idx_user_sources_user_id ON user_sources(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_user_sources_status ON user_sources(status)",
            "CREATE INDEX IF NOT EXISTS idx_user_documents_source_id ON user_documents(source_id)"
        ]


# Global instance for reuse
_user_data_db_instance = None


def get_user_data_db(db_path: str = None) -> UserDataDB:
    """
    Get or create the global UserDataDB instance.

    NOTE: Call await db.initialize() before first use (done by get_user_data_manager_async).

    Args:
        db_path: Optional path to SQLite database

    Returns:
        UserDataDB instance
    """
    global _user_data_db_instance
    if _user_data_db_instance is None:
        _user_data_db_instance = UserDataDB(db_path)
    return _user_data_db_instance
