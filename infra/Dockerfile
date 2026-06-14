# PostgreSQL 17 + pgvector + pg_bigm
# Base: pgvector/pgvector already includes pgvector extension on top of official postgres
# We add: pg_bigm for Chinese/CJK full-text search via 2-gram indexing

FROM pgvector/pgvector:pg17

# Build pg_bigm from source
# v1.2-20240606 supports PostgreSQL 16+
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        postgresql-server-dev-17 \
        curl \
        ca-certificates \
    && cd /tmp \
    && curl -L -o pg_bigm.tar.gz https://github.com/pgbigm/pg_bigm/archive/refs/tags/v1.2-20240606.tar.gz \
    && tar xzf pg_bigm.tar.gz \
    && cd pg_bigm-1.2-20240606 \
    && make USE_PGXS=1 PG_CONFIG=/usr/bin/pg_config \
    && make USE_PGXS=1 PG_CONFIG=/usr/bin/pg_config install \
    && cd / \
    && rm -rf /tmp/pg_bigm* \
    && apt-get purge -y build-essential postgresql-server-dev-17 curl \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
