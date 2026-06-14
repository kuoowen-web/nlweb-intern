-- NLWeb Database Schema
-- PostgreSQL 17 + pgvector + pg_bigm
-- This script runs automatically on first container start.

-- =============================================================================
-- Extensions
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: vector similarity search
CREATE EXTENSION IF NOT EXISTS pg_bigm;     -- pg_bigm: 2-gram full-text search (CJK support)

-- =============================================================================
-- Tables
-- =============================================================================

CREATE TABLE articles (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    author          TEXT,
    source          TEXT NOT NULL,           -- e.g. 'chinatimes', 'ltn', 'udn'
    date_published  TIMESTAMPTZ,
    content         TEXT,
    metadata        JSONB DEFAULT '{}',      -- flexible extra fields
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE chunks (
    id              BIGSERIAL PRIMARY KEY,
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,        -- position within the article (0-based)
    chunk_text      TEXT NOT NULL,
    embedding       vector(1024),            -- matches our embedding model output dimension
    tsv             TEXT,                     -- stored text for pg_bigm LIKE search
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (article_id, chunk_index)
);

-- =============================================================================
-- Indexes
-- =============================================================================

-- Vector search: IVFFlat index on chunk embeddings
-- IVF clusters vectors into lists, only scans nearby clusters at query time
-- lists = sqrt(num_vectors) rule of thumb; 1000 covers up to ~1M vectors
-- Benchmark (118K chunks, 2026-03-05):
--   probes=20: R@10=97.0%, R@50=91.2%, avg 21ms
--   probes=50: R@10=98.7%, R@50=97.1%, avg 29ms  <-- recommended
--   probes=100: R@10=98.7%, R@50=98.2%, avg 31ms
-- Set at query time: SET ivfflat.probes = 50;
-- Note: Do NOT create HNSW index — same size as IVFFlat (~930MB for 118K chunks)
--       and HNSW rebuild requires large shared memory allocation.
CREATE INDEX idx_chunks_embedding_ivf
    ON chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 1000);

-- Full-text search: pg_bigm GIN index on chunk text
-- pg_bigm creates 2-gram tokens, works natively with CJK characters
CREATE INDEX idx_chunks_tsv_bigm
    ON chunks
    USING gin (tsv gin_bigm_ops);

-- Also index article title for title search
CREATE INDEX idx_articles_title_bigm
    ON articles
    USING gin (title gin_bigm_ops);

-- Structural filters: B-tree indexes for common WHERE clauses
CREATE INDEX idx_articles_source
    ON articles (source);

CREATE INDEX idx_articles_date_published
    ON articles (date_published DESC);

CREATE INDEX idx_articles_author
    ON articles (author)
    WHERE author IS NOT NULL;

-- Foreign key lookup
CREATE INDEX idx_chunks_article_id
    ON chunks (article_id);

-- =============================================================================
-- User Document Chunks (private knowledge base)
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    org_id TEXT,
    source_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_udc_user_id ON user_document_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_udc_source_id ON user_document_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_udc_user_org ON user_document_chunks(user_id, org_id);

-- =============================================================================
-- Sample Hybrid Search Query (for reference)
-- =============================================================================

/*
-- Hybrid search: combine vector similarity + text match + structural filters
-- This is a template showing the approach; actual weights will be tuned.

WITH vector_results AS (
    -- Vector similarity search (semantic meaning)
    SELECT
        c.id AS chunk_id,
        c.article_id,
        c.chunk_text,
        1 - (c.embedding <=> $1::vector) AS vector_score  -- cosine similarity
    FROM chunks c
    JOIN articles a ON a.id = c.article_id
    WHERE a.source = $2                                     -- structural filter
      AND a.date_published >= $3                            -- date filter
    ORDER BY c.embedding <=> $1::vector                     -- ORDER BY distance
    LIMIT 100
),
text_results AS (
    -- Full-text search using pg_bigm (2-gram matching)
    SELECT
        c.id AS chunk_id,
        c.article_id,
        c.chunk_text,
        bigm_similarity(c.tsv, $4) AS text_score           -- bigram similarity
    FROM chunks c
    JOIN articles a ON a.id = c.article_id
    WHERE c.tsv LIKE '%' || likequery($4) || '%'            -- pg_bigm LIKE search
      AND a.source = $2
      AND a.date_published >= $3
    ORDER BY text_score DESC
    LIMIT 100
)
-- Combine results with weighted scoring
SELECT
    COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
    COALESCE(v.article_id, t.article_id) AS article_id,
    COALESCE(v.chunk_text, t.chunk_text) AS chunk_text,
    COALESCE(v.vector_score, 0) AS vector_score,
    COALESCE(t.text_score, 0) AS text_score,
    -- Weighted combination (weights to be tuned)
    0.7 * COALESCE(v.vector_score, 0) + 0.3 * COALESCE(t.text_score, 0) AS combined_score
FROM vector_results v
FULL OUTER JOIN text_results t ON v.chunk_id = t.chunk_id
ORDER BY combined_score DESC
LIMIT 20;

-- Parameters:
--   $1 = query embedding vector (1024 dimensions)
--   $2 = source filter (e.g., 'chinatimes')
--   $3 = date filter (e.g., '2026-01-01')
--   $4 = query text in Chinese (e.g., '台積電營收')
*/
