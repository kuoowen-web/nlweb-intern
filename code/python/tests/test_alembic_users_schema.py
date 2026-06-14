"""
TDD test for Fix 1: Alembic baseline vs auth_db.initialize() schema drift.

Tests run against SQLite to avoid requiring PG credentials.

Strategy:
  - Create a fresh SQLite DB using auth_db._init_database_sync() (mirrors production path)
  - Then run the new migration's upgrade() directly (simulating a new-env migration)
  - Verify the two drifts are fixed:
    1. email_verification_expires column exists and is nullable
    2. password_hash is nullable

We do NOT run the full alembic chain because b5e9d3f71a42/d4a7e1b83c59
contain PG-only DDL (BIGSERIAL, TIMESTAMPTZ, JSONB).
"""
import os
import sqlite3
import importlib
import pytest

# Ensure no PG env vars (use SQLite)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)


def _get_users_columns(db_path: str) -> dict:
    """Return column info for users table: {col_name: {notnull, type}}."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("PRAGMA table_info(users)")
        rows = cursor.fetchall()
        # columns: (cid, name, type, notnull, dflt_value, pk)
        return {row[1]: {'type': row[2], 'notnull': row[3]} for row in rows}
    finally:
        conn.close()


@pytest.fixture
def baseline_db_only(tmp_path):
    """
    Create a DB using the baseline migration only (tables that auth_db auto-creates).
    This simulates a new environment where baseline ran but new migrations have not.
    """
    db_path = str(tmp_path / 'baseline_only.db')

    # Create the users table as baseline does (old schema, without email_verification_expires,
    # and with password_hash NOT NULL)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0,
                email_verification_token TEXT,
                password_reset_token TEXT,
                password_reset_expires REAL,
                last_login REAL,
                created_at REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
    finally:
        conn.close()

    return db_path


@pytest.fixture
def auth_db_sqlite(tmp_path):
    """
    Create a SQLite DB using auth_db._init_database_sync (the auto-create path).
    This represents what VPS already has.
    """
    from auth.auth_db import AuthDB

    db_path = str(tmp_path / 'auth_init.db')
    AuthDB._instance = None
    db = AuthDB(db_path=db_path)
    AuthDB._instance = db
    db._init_database_sync()
    db._initialized = True

    yield db_path

    AuthDB._instance = None


class TestBaselineSchemaDrift:
    """Verify the drift exists before migration (proves tests are meaningful)."""

    def test_baseline_missing_email_verification_expires(self, baseline_db_only):
        """Baseline schema does NOT have email_verification_expires — confirms the drift."""
        columns = _get_users_columns(baseline_db_only)
        assert 'email_verification_expires' not in columns, (
            "This test verifies the drift exists in baseline. "
            "If it passes, the fix is not applied yet."
        )

    def test_baseline_password_hash_not_null(self, baseline_db_only):
        """Baseline has password_hash NOT NULL — confirms the drift."""
        columns = _get_users_columns(baseline_db_only)
        assert columns['password_hash']['notnull'] == 1, (
            "Baseline password_hash should be NOT NULL (the drift we're fixing)."
        )


class TestAuthDbInitSchema:
    """Verify auth_db.initialize() creates the correct schema (target schema)."""

    def test_auth_db_has_email_verification_expires(self, auth_db_sqlite):
        """auth_db._init_database_sync creates email_verification_expires."""
        columns = _get_users_columns(auth_db_sqlite)
        assert 'email_verification_expires' in columns, (
            "auth_db._init_database_sync must create email_verification_expires column."
        )

    def test_auth_db_password_hash_nullable(self, auth_db_sqlite):
        """auth_db._init_database_sync creates password_hash as nullable."""
        columns = _get_users_columns(auth_db_sqlite)
        assert columns['password_hash']['notnull'] == 0, (
            "auth_db creates password_hash nullable (for admin-created users without password)."
        )


class TestMigrationFixesDrift:
    """Core TDD tests: after running the new migration, drift is fixed."""

    def _apply_new_migration(self, db_path: str) -> None:
        """Apply the align_users_schema_with_initialize migration upgrade() directly."""
        from sqlalchemy import create_engine
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations

        # Import the migration module dynamically by file path
        import importlib.util
        from pathlib import Path
        migration_dir = Path(__file__).resolve().parent.parent / 'alembic' / 'versions'
        # Find the migration file matching our name
        migration_files = list(migration_dir.glob('*align_users_schema_with_initialize*.py'))
        assert migration_files, (
            "align_users_schema_with_initialize migration file not found in alembic/versions/. "
            "Fix 1 migration must be created first."
        )
        spec = importlib.util.spec_from_file_location('align_migration', migration_files[0])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        engine = create_engine(f'sqlite:///{db_path}')
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            # Temporarily set op context for the migration's op.get_bind() calls
            with Operations.context(ctx):
                mod.upgrade()
            conn.commit()

    def test_email_verification_expires_added(self, baseline_db_only):
        """After migration, email_verification_expires column must exist."""
        self._apply_new_migration(baseline_db_only)
        columns = _get_users_columns(baseline_db_only)
        assert 'email_verification_expires' in columns, (
            "Migration must add email_verification_expires column."
        )

    def test_email_verification_expires_is_nullable(self, baseline_db_only):
        """After migration, email_verification_expires must be nullable."""
        self._apply_new_migration(baseline_db_only)
        columns = _get_users_columns(baseline_db_only)
        col = columns.get('email_verification_expires')
        assert col is not None
        assert col['notnull'] == 0, "email_verification_expires must be nullable"

    def test_password_hash_becomes_nullable(self, baseline_db_only):
        """After migration, password_hash must be nullable."""
        self._apply_new_migration(baseline_db_only)
        columns = _get_users_columns(baseline_db_only)
        col = columns['password_hash']
        assert col['notnull'] == 0, (
            "After migration, password_hash must be nullable "
            "(admin-created users have no password)."
        )

    def test_can_insert_user_with_null_password_hash_after_migration(self, baseline_db_only):
        """After migration, can insert user with NULL password_hash."""
        import uuid
        import time
        self._apply_new_migration(baseline_db_only)
        conn = sqlite3.connect(baseline_db_only)
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, name, email_verified, created_at) "
                "VALUES (?, ?, NULL, ?, 0, ?)",
                (str(uuid.uuid4()), 'admin_created@example.com', 'Admin User', time.time())
            )
            conn.commit()
            row = conn.execute(
                "SELECT password_hash FROM users WHERE email = 'admin_created@example.com'"
            ).fetchone()
            assert row is not None
            assert row[0] is None, "password_hash should be NULL for admin-created user"
        finally:
            conn.close()

    def test_migration_idempotent_on_auth_db_schema(self, auth_db_sqlite):
        """
        Running migration on a DB that already has auth_db.initialize() schema
        (i.e., VPS) must not fail — it must be idempotent (no-op).
        """
        # Should not raise
        self._apply_new_migration(auth_db_sqlite)
        # Schema still correct after no-op
        columns = _get_users_columns(auth_db_sqlite)
        assert 'email_verification_expires' in columns
        assert columns['password_hash']['notnull'] == 0
