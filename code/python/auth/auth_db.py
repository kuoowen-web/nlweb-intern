"""
Database abstraction layer for authentication and session management.

Schema is managed by alembic (see `code/python/alembic/versions/`).
This module owns the connection pool + query interface ONLY. Do NOT add
DDL here — write a new alembic migration instead. The legacy schema-dict
methods (`_get_postgres_schema` / `_get_sqlite_schema` / `_get_index_sql`)
and the `_init_database_async` / `_init_database_sync` helpers are
preserved for Phase 2 regression test + legacy unit-test fixtures only;
they are NOT part of the production startup path (see `initialize()`).

Supports both SQLite (local development) and PostgreSQL (production).
Uses AsyncConnectionPool for PostgreSQL to avoid per-query connection overhead.
Reads POSTGRES_CONNECTION_STRING (preferred) / DATABASE_URL / ANALYTICS_DATABASE_URL.
"""

import os
import json
import sqlite3
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("auth_db")

# Try to import PostgreSQL libraries (optional)
try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    logger.warning("PostgreSQL libraries not available, auth falling back to SQLite")


def get_project_root_db_path() -> str:
    """Get absolute path to auth database from project root."""
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    db_path = project_root / "data" / "auth" / "auth.db"
    return str(db_path)


class AuthDB:
    """
    Database abstraction layer for auth + session tables.

    PostgreSQL: uses psycopg async connection pool.
    SQLite: uses sync connections wrapped in asyncio.to_thread (dev only).
    """

    _instance = None

    @classmethod
    def get_instance(cls) -> 'AuthDB':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = get_project_root_db_path()

        # Unified: prefer POSTGRES_CONNECTION_STRING, fall back to DATABASE_URL / ANALYTICS_DATABASE_URL (legacy)
        self.database_url = (os.environ.get('POSTGRES_CONNECTION_STRING')
                             or os.environ.get('DATABASE_URL')
                             or os.environ.get('ANALYTICS_DATABASE_URL'))
        self.db_path = Path(db_path)
        self.db_type = 'postgres' if self.database_url and POSTGRES_AVAILABLE else 'sqlite'
        self._initialized = False
        self._pool = None
        self._pool_lock = asyncio.Lock()

        logger.info(f"Auth database type: {self.db_type}")

        if self.db_type == 'sqlite':
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Using SQLite auth database at: {self.db_path.absolute()}")
        else:
            masked = self.database_url.split('@')[1] if '@' in self.database_url else 'connected'
            logger.info(f"Using PostgreSQL auth database: {masked}")

    async def initialize(self) -> None:
        """初始化 connection pool 並驗 alembic-managed schema 存在。

        Schema 由 alembic 管，本方法**不**主動建表 / 補欄位 / 建 index。
        只做兩件事：
          1. Sanity check：`alembic_version` 表存在嗎？不存在 → raise
             (含具體解法指令；絕不 silent fail)。
          2. PostgreSQL：warm-up connection pool。

        若 alembic 還沒跑過 → 直接 raise RuntimeError，error message
        引導使用者跑 `cd code/python && alembic upgrade head` 後重啟。
        違反「alembic 是唯一 schema source of truth」原則的任何 DDL
        都不可加在這裡 — 寫新的 alembic migration 取代。
        """
        if self._initialized:
            return
        if self.db_type == 'postgres':
            await self._verify_schema_async()
            await self._get_pool()  # connection pool warm-up
        else:
            self._verify_schema_sync()
        self._initialized = True

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

    async def execute_returning(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict]:
        """Execute INSERT/UPDATE with RETURNING clause (both SQLite and PostgreSQL support RETURNING)."""
        if self.db_type == 'postgres':
            return await self._pg_fetchone(query, params)
        else:
            return await asyncio.to_thread(self._sqlite_execute_returning, query, params)

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
                    logger.info("Auth DB connection pool initialized")
        return self._pool

    async def close(self):
        """Close the connection pool. Call on shutdown."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Auth DB connection pool closed")

    # ── PostgreSQL async methods ──────────────────────────────────

    def _adapt_query_pg(self, query: str) -> str:
        """Convert ? placeholders to %s for psycopg.

        NOTE: This naive replace will break if queries ever use PostgreSQL's
        JSONB ? operator. Current auth queries don't use JSONB, so this is safe.
        If JSONB is added later, switch to a proper placeholder parser.
        """
        return query.replace('?', '%s')

    @staticmethod
    def _serialize_row(row: Dict) -> Dict:
        """Ensure all values are JSON-serializable (PostgreSQL returns UUID objects)."""
        import uuid as _uuid
        return {k: str(v) if isinstance(v, _uuid.UUID) else v for k, v in row.items()}

    async def _pg_fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict]:
        query = self._adapt_query_pg(query)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
                return self._serialize_row(dict(row)) if row else None

    async def _pg_fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        query = self._adapt_query_pg(query)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
                return [self._serialize_row(dict(r)) for r in rows]

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

    def _sqlite_execute_returning(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict]:
        """Execute an INSERT/UPDATE ... RETURNING query, commit, and return the first result row."""
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or ())
            row = cursor.fetchone()
            conn.commit()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── Initialization ────────────────────────────────────────────

    # Sanity-check error message — 必須含具體解法指令（CEO Q4: raise，不 silent fail）。
    _SCHEMA_NOT_INITIALIZED_MSG = (
        "alembic_version 表不存在 — 表示 schema 還沒由 alembic 建立。\n"
        "請先執行：cd code/python && alembic upgrade head\n"
        "然後重新啟動 server。"
    )

    async def _verify_schema_async(self) -> None:
        """PostgreSQL：驗 alembic 已跑過（`alembic_version` 表存在）。

        失敗時 raise `RuntimeError`，message 含具體解法指令。
        絕不 silent fail（依「不可 silent fail」紀律）。
        """
        try:
            async with await psycopg.AsyncConnection.connect(
                self.database_url, autocommit=True, connect_timeout=5
            ) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'alembic_version'"
                    )
                    row = await cur.fetchone()
                    if row is None:
                        raise RuntimeError(self._SCHEMA_NOT_INITIALIZED_MSG)
            logger.info("Auth DB schema verified (PostgreSQL, alembic-managed)")
        except RuntimeError:
            # 已經是 sanity check raise，直接 propagate（不要被下面 except 蓋掉）
            raise
        except Exception as e:
            logger.error(
                f"無法連線到 PostgreSQL ({self.database_url.split('@')[1] if '@' in self.database_url else self.database_url})。"
                f"是不是忘記開 Docker Desktop？"
            )
            logger.error(f"Failed to verify auth database schema: {e}", exc_info=True)
            raise

    def _verify_schema_sync(self) -> None:
        """SQLite：驗 alembic 已跑過（`alembic_version` 表存在）。

        失敗時 raise `RuntimeError`，message 含具體解法指令。
        """
        try:
            conn = self._sqlite_connect()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(self._SCHEMA_NOT_INITIALIZED_MSG)
            finally:
                conn.close()
            logger.info("Auth DB schema verified (SQLite, alembic-managed)")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Failed to verify auth database schema: {e}", exc_info=True)
            raise

    # ── Legacy DDL methods (deprecated, retained for unit tests only) ──

    async def _init_database_async(self):
        """⚠ DEPRECATED — schema is managed by alembic, NOT by this method.

        保留此方法**僅**供下列場景使用：
          1. Phase 2 schema-equivalence regression test
             (`tests/test_alembic_schema_equivalence.py`) 跑 path B。
          2. 既有 legacy unit-test fixtures（部分 test 直接呼叫做快速
             schema setup，不走 alembic）— 這些 test 視為 legacy，
             未來會逐步遷移到 alembic-based fixture。

        Runtime / server startup 路徑（`initialize()`）**不再**呼叫本方法。
        DO NOT add new tables / columns to `_get_postgres_schema()` — 寫
        新的 alembic migration 取代。
        """
        try:
            async with await psycopg.AsyncConnection.connect(
                self.database_url, autocommit=True, connect_timeout=5
            ) as conn:
                async with conn.cursor() as cur:
                    for table_name, create_sql in self._get_postgres_schema().items():
                        try:
                            await cur.execute(create_sql)
                            logger.debug(f"Auth table ensured: {table_name}")
                        except Exception as e:
                            logger.error(f"Failed to create auth table {table_name}: {e}")
                            raise

                    for index_sql in self._get_index_sql():
                        try:
                            await cur.execute(index_sql)
                        except Exception as e:
                            logger.warning(f"Index creation skipped: {e}")

            logger.info("Auth database initialized (PostgreSQL async, LEGACY path)")
        except Exception as e:
            logger.error(
                f"無法連線到 PostgreSQL ({self.database_url.split('@')[1] if '@' in self.database_url else self.database_url})。"
                f"是不是忘記開 Docker Desktop？"
            )
            logger.error(f"Failed to initialize auth database: {e}", exc_info=True)
            raise

    def _init_database_sync(self):
        """⚠ DEPRECATED — schema is managed by alembic, NOT by this method.

        保留此方法**僅**供下列場景使用：
          1. Phase 2 schema-equivalence regression test
             (`tests/test_alembic_schema_equivalence.py`) 跑 path B。
          2. 既有 legacy unit-test fixtures
             (`tests/test_auth_service.py`, `tests/test_session_service.py`,
             `tests/test_help_routes.py`) 直接呼叫做 SQLite schema 快速
             setup — 這些 test 未來會遷移到 alembic-based fixture。

        Runtime / server startup 路徑（`initialize()`）**不再**呼叫本方法。
        DO NOT add new tables / columns to `_get_sqlite_schema()` — 寫
        新的 alembic migration 取代。
        """
        try:
            conn = self._sqlite_connect()
            cursor = conn.cursor()

            for table_name, create_sql in self._get_sqlite_schema().items():
                try:
                    cursor.execute(create_sql)
                    logger.debug(f"Auth table ensured: {table_name}")
                except Exception as e:
                    logger.error(f"Failed to create auth table {table_name}: {e}")
                    raise

            for index_sql in self._get_index_sql():
                try:
                    cursor.execute(index_sql)
                except Exception as e:
                    logger.warning(f"Index creation skipped: {e}")

            conn.commit()
            conn.close()
            logger.info("Auth database initialized (SQLite, LEGACY path)")
        except Exception as e:
            logger.error(f"Failed to initialize auth database: {e}", exc_info=True)
            raise

    # ── Legacy sync interface (for backward compat during transition) ─

    def connect(self):
        """Create sync connection. Use async methods instead when possible."""
        if self.db_type == 'postgres':
            return psycopg.connect(self.database_url, row_factory=dict_row)
        else:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            return conn

    def adapt_query(self, query: str) -> str:
        if self.db_type == 'postgres':
            return query.replace('?', '%s')
        return query

    # ── Schema definitions ────────────────────────────────────────

    def _get_sqlite_schema(self) -> Dict[str, str]:
        """⚠ DEPRECATED — schema source of truth is alembic, NOT this dict.

        保留此方法**僅**供 Phase 2 unit test 做 regression compare 使用
        (`tests/test_alembic_schema_equivalence.py` 跑 path B) 及既有
        legacy unit-test fixtures 用。

        執行時序：alembic upgrade head 跑完才有正確 schema；本 dict
        所描述的 schema **不會**在 server 啟動時被執行（`initialize()`
        已改為 sanity check only）。

        DO NOT add new tables / columns here — 寫新的 alembic migration
        取代。如本 dict 與 alembic head 不一致，以 alembic 為準。
        """
        return {
            'organizations': """
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    plan TEXT,
                    max_members INTEGER NOT NULL DEFAULT 5,
                    settings TEXT DEFAULT '{}',
                    storage_quota_gb INTEGER DEFAULT 5,
                    monthly_search_limit INTEGER DEFAULT 1000,
                    created_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
            """,
            'users': """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT,
                    name TEXT NOT NULL,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    email_verification_token TEXT,
                    email_verification_expires REAL,
                    password_reset_token TEXT,
                    password_reset_expires REAL,
                    last_login REAL,
                    created_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
            """,
            'org_memberships': """
                CREATE TABLE IF NOT EXISTS org_memberships (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    invited_by TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    accepted_at REAL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
                )
            """,
            'invitations': """
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    invited_by TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    expires_at REAL NOT NULL,
                    accepted_at REAL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
                    FOREIGN KEY (invited_by) REFERENCES users(id)
                )
            """,
            'refresh_tokens': """
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    revoked_at REAL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """,
            'login_attempts': """
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    ip_address TEXT,
                    success INTEGER NOT NULL DEFAULT 0,
                    attempted_at REAL NOT NULL
                )
            """,
            'search_sessions': """
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    title TEXT,
                    conversation_history TEXT DEFAULT '[]',
                    session_history TEXT DEFAULT '[]',
                    chat_history TEXT DEFAULT '[]',
                    accumulated_articles TEXT DEFAULT '[]',
                    pinned_messages TEXT DEFAULT '[]',
                    pinned_news_cards TEXT DEFAULT '[]',
                    research_report TEXT DEFAULT '{}',
                    user_feedback TEXT,
                    admin_note TEXT,
                    visibility TEXT DEFAULT 'private',
                    team_comments TEXT DEFAULT '[]',
                    is_archived INTEGER DEFAULT 0,
                    deleted_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (org_id) REFERENCES organizations(id)
                )
            """,
            'org_folders': """
                CREATE TABLE IF NOT EXISTS org_folders (
                    id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id)
                )
            """,
            'org_folder_sessions': """
                CREATE TABLE IF NOT EXISTS org_folder_sessions (
                    folder_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    added_at REAL NOT NULL,
                    PRIMARY KEY (folder_id, session_id),
                    FOREIGN KEY (folder_id) REFERENCES org_folders(id) ON DELETE CASCADE,
                    FOREIGN KEY (session_id) REFERENCES search_sessions(id) ON DELETE CASCADE
                )
            """,
            'session_shares': """
                CREATE TABLE IF NOT EXISTS session_shares (
                    session_id TEXT NOT NULL,
                    shared_with_user_id TEXT NOT NULL,
                    shared_at REAL NOT NULL,
                    PRIMARY KEY (session_id, shared_with_user_id),
                    FOREIGN KEY (session_id) REFERENCES search_sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY (shared_with_user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """,
            'user_preferences': """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    preference_value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id, org_id, preference_key),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (org_id) REFERENCES organizations(id)
                )
            """,
            'audit_logs': """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    org_id TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    details TEXT,
                    ip_address TEXT,
                    created_at REAL NOT NULL
                )
            """,
            'bootstrap_tokens': """
                CREATE TABLE IF NOT EXISTS bootstrap_tokens (
                    id TEXT PRIMARY KEY,
                    token TEXT UNIQUE NOT NULL,
                    org_name_hint TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL,
                    used_by_email TEXT
                )
            """,
            'feedbacks': """
                CREATE TABLE IF NOT EXISTS feedbacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    email TEXT,
                    category TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    screenshot_path TEXT,
                    session_id TEXT,
                    created_at REAL NOT NULL
                )
            """,
            'faqs': """
                CREATE TABLE IF NOT EXISTS faqs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_published INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """,
        }

    def _get_postgres_schema(self) -> Dict[str, str]:
        """⚠ DEPRECATED — schema source of truth is alembic, NOT this dict.

        保留此方法**僅**供 Phase 2 unit test 做 regression compare 使用
        (`tests/test_alembic_schema_equivalence.py` 跑 path B)。

        執行時序：alembic upgrade head 跑完才有正確 schema；本 dict
        所描述的 schema **不會**在 server 啟動時被執行（`initialize()`
        已改為 sanity check only）。

        DO NOT add new tables / columns here — 寫新的 alembic migration
        取代。如本 dict 與 alembic head 不一致，以 alembic 為準（這也
        是 Phase 2 unit test 要驗證的事情）。
        """
        return {
            'organizations': """
                CREATE TABLE IF NOT EXISTS organizations (
                    id UUID PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    slug VARCHAR(255) NOT NULL UNIQUE,
                    plan VARCHAR(50),
                    max_members INTEGER NOT NULL DEFAULT 5,
                    settings TEXT DEFAULT '{}',
                    storage_quota_gb INTEGER DEFAULT 5,
                    monthly_search_limit INTEGER DEFAULT 1000,
                    created_at DOUBLE PRECISION NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """,
            'users': """
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash VARCHAR(255),
                    name VARCHAR(255) NOT NULL,
                    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    email_verification_token VARCHAR(255),
                    email_verification_expires DOUBLE PRECISION,
                    password_reset_token VARCHAR(255),
                    password_reset_expires DOUBLE PRECISION,
                    last_login DOUBLE PRECISION,
                    created_at DOUBLE PRECISION NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """,
            'org_memberships': """
                CREATE TABLE IF NOT EXISTS org_memberships (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    role VARCHAR(50) NOT NULL DEFAULT 'member',
                    invited_by UUID,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    accepted_at DOUBLE PRECISION
                )
            """,
            'invitations': """
                CREATE TABLE IF NOT EXISTS invitations (
                    id UUID PRIMARY KEY,
                    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    email VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'member',
                    invited_by UUID NOT NULL REFERENCES users(id),
                    token VARCHAR(255) NOT NULL UNIQUE,
                    expires_at DOUBLE PRECISION NOT NULL,
                    accepted_at DOUBLE PRECISION,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """,
            'refresh_tokens': """
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash VARCHAR(255) NOT NULL UNIQUE,
                    expires_at DOUBLE PRECISION NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    revoked_at DOUBLE PRECISION
                )
            """,
            'login_attempts': """
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id UUID PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    ip_address INET,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    attempted_at DOUBLE PRECISION NOT NULL
                )
            """,
            'search_sessions': """
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    org_id UUID NOT NULL REFERENCES organizations(id),
                    title VARCHAR(500),
                    conversation_history JSONB DEFAULT '[]',
                    session_history JSONB DEFAULT '[]',
                    chat_history JSONB DEFAULT '[]',
                    accumulated_articles JSONB DEFAULT '[]',
                    pinned_messages JSONB DEFAULT '[]',
                    pinned_news_cards JSONB DEFAULT '[]',
                    research_report JSONB DEFAULT '{}',
                    user_feedback VARCHAR(20),
                    admin_note TEXT,
                    visibility VARCHAR(20) DEFAULT 'private',
                    team_comments JSONB DEFAULT '[]',
                    is_archived BOOLEAN DEFAULT FALSE,
                    deleted_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """,
            'org_folders': """
                CREATE TABLE IF NOT EXISTS org_folders (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    created_by UUID NOT NULL REFERENCES users(id),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """,
            'org_folder_sessions': """
                CREATE TABLE IF NOT EXISTS org_folder_sessions (
                    folder_id UUID NOT NULL REFERENCES org_folders(id) ON DELETE CASCADE,
                    session_id UUID NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    added_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (folder_id, session_id)
                )
            """,
            'session_shares': """
                CREATE TABLE IF NOT EXISTS session_shares (
                    session_id UUID NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    shared_with_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    shared_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (session_id, shared_with_user_id)
                )
            """,
            'user_preferences': """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    org_id UUID NOT NULL REFERENCES organizations(id),
                    preference_key VARCHAR(100) NOT NULL,
                    preference_value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, org_id, preference_key)
                )
            """,
            'audit_logs': """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID,
                    org_id UUID,
                    action VARCHAR(100) NOT NULL,
                    target_type VARCHAR(50),
                    target_id UUID,
                    details JSONB,
                    ip_address VARCHAR(64),
                    created_at DOUBLE PRECISION NOT NULL
                )
            """,
            'bootstrap_tokens': """
                CREATE TABLE IF NOT EXISTS bootstrap_tokens (
                    id TEXT PRIMARY KEY,
                    token TEXT UNIQUE NOT NULL,
                    org_name_hint TEXT DEFAULT '',
                    created_at DOUBLE PRECISION NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    used_at DOUBLE PRECISION,
                    used_by_email TEXT
                )
            """,
            'feedbacks': """
                CREATE TABLE IF NOT EXISTS feedbacks (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT,
                    email VARCHAR(255),
                    category VARCHAR(50) NOT NULL,
                    rating INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    screenshot_path TEXT,
                    session_id TEXT,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """,
            'faqs': """
                CREATE TABLE IF NOT EXISTS faqs (
                    id SERIAL PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    category VARCHAR(50) NOT NULL DEFAULT 'general',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_published BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
            """,
        }

    def _get_index_sql(self) -> List[str]:
        """⚠ DEPRECATED — index source of truth is alembic, NOT this list.

        保留此方法**僅**供 Phase 2 unit test 做 regression compare 使用
        (`tests/test_alembic_schema_equivalence.py` 跑 path B) 及既有
        legacy unit-test fixtures 用。

        執行時序：alembic upgrade head 跑完才有正確 index 集合；本 list
        所描述的 index **不會**在 server 啟動時被執行（`initialize()`
        已改為 sanity check only）。

        DO NOT add new indexes here — 寫新的 alembic migration 取代。
        注意：alembic head 比本 list 多兩個 partial index
        (`idx_sessions_visibility` / `idx_sessions_deleted`)，這是
        alembic 比 legacy initialize 更完整的部分。
        """
        return [
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
            "CREATE INDEX IF NOT EXISTS idx_users_verification_token ON users(email_verification_token)",
            "CREATE INDEX IF NOT EXISTS idx_users_reset_token ON users(password_reset_token)",
            "CREATE INDEX IF NOT EXISTS idx_org_memberships_user ON org_memberships(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_org_memberships_org ON org_memberships(org_id)",
            "CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token)",
            "CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email)",
            "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash)",
            "CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_login_attempts_email ON login_attempts(email)",
            "CREATE INDEX IF NOT EXISTS idx_login_attempts_time ON login_attempts(attempted_at)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_org ON search_sessions(user_id, org_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON search_sessions(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_org_folders ON org_folders(org_id)",
            "CREATE INDEX IF NOT EXISTS idx_prefs_user_org ON user_preferences(user_id, org_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_logs(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_org_id ON audit_logs(org_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)",
            "CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_bootstrap_tokens_token ON bootstrap_tokens(token)",
            "CREATE INDEX IF NOT EXISTS idx_feedbacks_created_at ON feedbacks(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_faqs_sort_order ON faqs(sort_order, id)",
        ]

