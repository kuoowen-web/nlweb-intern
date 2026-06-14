"""
Alembic environment configuration.

Supports dual-DB: PostgreSQL or SQLite (local dev).

Env var priority chain (matches CLAUDE.md「Analytics 資料庫」spec and
the convention used by `auth_db.py` / `analytics_db.py` / `health.py` /
`user_postgres_provider.py`):

    1. POSTGRES_CONNECTION_STRING  (preferred, used by VPS)
    2. DATABASE_URL                (legacy)
    3. ANALYTICS_DATABASE_URL      (legacy)
    4. SQLite fallback             (local dev, no PG configured)
"""
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool, create_engine
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _get_database_url() -> str:
    """Resolve database URL from environment, matching auth_db.py priority.

    Priority chain (per CLAUDE.md「Analytics 資料庫」, matches the
    convention used by auth_db.py / analytics_db.py / health.py /
    user_postgres_provider.py):
      1. POSTGRES_CONNECTION_STRING  (preferred, used by VPS)
      2. DATABASE_URL                (legacy)
      3. ANALYTICS_DATABASE_URL      (legacy)
      4. SQLite fallback             (local dev, no PG configured)

    Note: SQLAlchemy defaults to psycopg2 driver for `postgresql://` URLs.
    Project uses psycopg3 only. Rewrite scheme to `postgresql+psycopg://`
    so SQLAlchemy uses the psycopg3 dialect.

    The rewrite happens to a local variable only — the original env var
    is not mutated, so server modules that directly read the env var and
    call `psycopg.connect()` are unaffected (verified Phase 2 implement).
    """
    pg_url = (
        os.environ.get('POSTGRES_CONNECTION_STRING')
        or os.environ.get('DATABASE_URL')
        or os.environ.get('ANALYTICS_DATABASE_URL')
    )
    if pg_url:
        if pg_url.startswith('postgresql://'):
            pg_url = 'postgresql+psycopg://' + pg_url[len('postgresql://'):]
        elif pg_url.startswith('postgres://'):
            pg_url = 'postgresql+psycopg://' + pg_url[len('postgres://'):]
        return pg_url
    # SQLite fallback — same path as auth_db.py
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    db_path = project_root / "data" / "auth" / "auth.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without DB connection)."""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with DB connection)."""
    url = _get_database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
