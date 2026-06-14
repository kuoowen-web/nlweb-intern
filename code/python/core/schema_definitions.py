# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unified Analytics Schema Definitions — Schema v2 (ML-ready)

Single source of truth for all analytics table schemas.
Both analytics_db.py and query_logger.py import from here.

Tables (8):
  queries, retrieved_documents, ranking_scores, user_interactions,
  feature_vectors, user_feedback, tier_6_enrichment, guardrail_events

ALLOWED_COLUMNS: 105 unique entries (security whitelist for dynamic queries)
Indexes: 22 (get_index_sql returns 22 CREATE INDEX statements)

Schema version: 2
"""

from typing import Dict, List

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Security whitelists
# ---------------------------------------------------------------------------

ALLOWED_TABLES = {
    'queries',
    'retrieved_documents',
    'ranking_scores',
    'user_interactions',
    'feature_vectors',
    'user_feedback',
    'tier_6_enrichment',
    'guardrail_events',
}

ALLOWED_COLUMNS = {
    # queries
    'query_id', 'timestamp', 'user_id', 'org_id', 'session_id', 'conversation_id',
    'query_text', 'decontextualized_query', 'site', 'mode', 'model',
    'parent_query_id', 'latency_total_ms', 'latency_retrieval_ms',
    'latency_ranking_ms', 'latency_generation_ms', 'num_results_retrieved',
    'num_results_ranked', 'num_results_returned', 'cost_usd',
    'error_occurred', 'error_message', 'query_length_words',
    'query_length_chars', 'has_temporal_indicator', 'embedding_model',
    'schema_version',
    # retrieved_documents
    'doc_url', 'doc_title', 'doc_description', 'doc_published_date',
    'doc_author', 'doc_source', 'retrieval_position',
    'vector_similarity_score', 'keyword_boost_score', 'bm25_score',
    'temporal_boost', 'domain_match', 'final_retrieval_score',
    'query_term_count', 'doc_length', 'title_exact_match',
    'desc_exact_match', 'keyword_overlap_ratio', 'recency_days',
    'has_author', 'retrieval_algorithm',
    # ranking_scores
    'ranking_position', 'llm_final_score', 'llm_snippet', 'xgboost_score',
    'xgboost_confidence', 'mmr_diversity_score', 'final_ranking_score',
    'ranking_method', 'relative_score', 'score_percentile',
    # user_interactions
    'interaction_type', 'interaction_timestamp', 'result_position',
    'dwell_time_ms', 'scroll_depth_percent', 'clicked',
    'client_user_agent', 'client_ip_hash',
    # feature_vectors
    'query_type', 'has_brand_mention', 'doc_length_words',
    'doc_length_chars', 'title_length', 'has_publication_date',
    'schema_completeness', 'domain_authority', 'vector_similarity',
    'vector_similarity_score', 'query_term_coverage', 'temporal_relevance',
    'temporal_boost', 'entity_match_count', 'partial_match_count',
    'relative_score_to_top', 'relevance_grade', 'created_at',
    'description_length', 'url_length', 'position_change',
    'mmr_diversity_score', 'detected_intent', 'keyword_boost',
    'final_retrieval_score', 'llm_final_score',
    'has_quotes', 'has_numbers', 'has_question_words', 'keyword_count',
    # user_feedback
    'query', 'answer_snippet', 'rating', 'comment',
    # tier_6_enrichment
    'source_type', 'cache_hit', 'latency_ms', 'timeout_occurred',
    'result_count', 'metadata',
    # guardrail_events
    'event_type', 'severity', 'client_ip', 'details',
}

# ---------------------------------------------------------------------------
# SQLite schema — Schema v2
# ---------------------------------------------------------------------------

def get_sqlite_schema() -> Dict[str, str]:
    """Return SQLite CREATE TABLE statements for all 7 analytics tables."""
    return {
        'queries': """
            CREATE TABLE IF NOT EXISTS queries (
                query_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                user_id TEXT NOT NULL,
                org_id TEXT,
                session_id TEXT,
                conversation_id TEXT,
                query_text TEXT NOT NULL,
                decontextualized_query TEXT,
                site TEXT NOT NULL,
                mode TEXT NOT NULL,
                model TEXT,
                parent_query_id TEXT,
                latency_total_ms REAL,
                latency_retrieval_ms REAL,
                latency_ranking_ms REAL,
                latency_generation_ms REAL,
                num_results_retrieved INTEGER,
                num_results_ranked INTEGER,
                num_results_returned INTEGER,
                cost_usd REAL,
                error_occurred INTEGER DEFAULT 0,
                error_message TEXT,
                query_length_words INTEGER,
                query_length_chars INTEGER,
                has_temporal_indicator INTEGER DEFAULT 0,
                embedding_model TEXT,
                schema_version INTEGER DEFAULT 2
            )
        """,
        'retrieved_documents': """
            CREATE TABLE IF NOT EXISTS retrieved_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                doc_url TEXT NOT NULL,
                doc_title TEXT,
                doc_description TEXT,
                doc_published_date TEXT,
                doc_author TEXT,
                doc_source TEXT,
                retrieval_position INTEGER NOT NULL,
                vector_similarity_score REAL,
                keyword_boost_score REAL,
                bm25_score REAL,
                temporal_boost REAL,
                domain_match INTEGER,
                final_retrieval_score REAL,
                query_term_count INTEGER,
                doc_length INTEGER,
                title_exact_match INTEGER DEFAULT 0,
                desc_exact_match INTEGER DEFAULT 0,
                keyword_overlap_ratio REAL,
                recency_days INTEGER,
                has_author INTEGER DEFAULT 0,
                retrieval_algorithm TEXT,
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'ranking_scores': """
            CREATE TABLE IF NOT EXISTS ranking_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                doc_url TEXT NOT NULL,
                ranking_position INTEGER NOT NULL,
                llm_final_score REAL,
                llm_snippet TEXT,
                xgboost_score REAL,
                xgboost_confidence REAL,
                mmr_diversity_score REAL,
                final_ranking_score REAL,
                ranking_method TEXT,
                relative_score REAL,
                score_percentile REAL,
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'user_interactions': """
            CREATE TABLE IF NOT EXISTS user_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                doc_url TEXT NOT NULL,
                interaction_type TEXT NOT NULL,
                interaction_timestamp REAL NOT NULL,
                result_position INTEGER,
                dwell_time_ms REAL,
                scroll_depth_percent REAL,
                clicked INTEGER DEFAULT 0,
                user_id TEXT,
                org_id TEXT,
                client_user_agent TEXT,
                client_ip_hash TEXT,
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'feature_vectors': """
            CREATE TABLE IF NOT EXISTS feature_vectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                doc_url TEXT NOT NULL,
                query_length_chars INTEGER,
                query_length_words INTEGER,
                has_quotes INTEGER,
                has_numbers INTEGER,
                has_question_words INTEGER,
                keyword_count INTEGER,
                doc_length_words INTEGER,
                recency_days INTEGER,
                has_author INTEGER,
                has_publication_date INTEGER,
                schema_completeness REAL,
                title_length INTEGER,
                description_length INTEGER,
                url_length INTEGER,
                vector_similarity_score REAL,
                bm25_score REAL,
                keyword_boost REAL,
                temporal_boost REAL,
                final_retrieval_score REAL,
                keyword_overlap_ratio REAL,
                title_exact_match INTEGER,
                retrieval_position INTEGER,
                ranking_position INTEGER,
                llm_final_score REAL,
                relative_score_to_top REAL,
                score_percentile REAL,
                position_change INTEGER,
                mmr_diversity_score REAL,
                detected_intent INTEGER,
                query_type TEXT,
                has_temporal_indicator INTEGER,
                has_brand_mention INTEGER,
                doc_length_chars INTEGER,
                domain_authority REAL,
                query_term_coverage REAL,
                domain_match INTEGER,
                entity_match_count INTEGER,
                partial_match_count INTEGER,
                clicked INTEGER DEFAULT 0,
                dwell_time_ms REAL,
                relevance_grade INTEGER,
                schema_version INTEGER DEFAULT 2,
                created_at REAL NOT NULL,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'user_feedback': """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                answer_snippet TEXT,
                rating TEXT NOT NULL,
                comment TEXT,
                session_id TEXT,
                query_id TEXT,
                user_id TEXT,
                org_id TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE SET NULL
            )
        """,
        'tier_6_enrichment': """
            CREATE TABLE IF NOT EXISTS tier_6_enrichment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                cache_hit INTEGER DEFAULT 0,
                latency_ms INTEGER,
                timeout_occurred INTEGER DEFAULT 0,
                result_count INTEGER,
                timestamp REAL NOT NULL,
                metadata TEXT,
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'guardrail_events': """
            CREATE TABLE IF NOT EXISTS guardrail_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                user_id TEXT,
                client_ip TEXT,
                details TEXT,
                schema_version INTEGER DEFAULT 2
            )
        """,
    }


# ---------------------------------------------------------------------------
# PostgreSQL schema — Schema v2
# ---------------------------------------------------------------------------

def get_postgres_schema() -> Dict[str, str]:
    """Return PostgreSQL CREATE TABLE statements for all 7 analytics tables."""
    return {
        'queries': """
            CREATE TABLE IF NOT EXISTS queries (
                query_id VARCHAR(255) PRIMARY KEY,
                timestamp DOUBLE PRECISION NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                org_id VARCHAR(255),
                session_id VARCHAR(255),
                conversation_id VARCHAR(255),
                query_text TEXT NOT NULL,
                decontextualized_query TEXT,
                site VARCHAR(100) NOT NULL,
                mode VARCHAR(50) NOT NULL,
                model VARCHAR(100),
                parent_query_id VARCHAR(255),
                latency_total_ms DOUBLE PRECISION,
                latency_retrieval_ms DOUBLE PRECISION,
                latency_ranking_ms DOUBLE PRECISION,
                latency_generation_ms DOUBLE PRECISION,
                num_results_retrieved INTEGER,
                num_results_ranked INTEGER,
                num_results_returned INTEGER,
                cost_usd DOUBLE PRECISION,
                error_occurred INTEGER DEFAULT 0,
                error_message TEXT,
                query_length_words INTEGER,
                query_length_chars INTEGER,
                has_temporal_indicator INTEGER DEFAULT 0,
                embedding_model VARCHAR(100),
                schema_version INTEGER DEFAULT 2
            )
        """,
        'retrieved_documents': """
            CREATE TABLE IF NOT EXISTS retrieved_documents (
                id SERIAL PRIMARY KEY,
                query_id VARCHAR(255) NOT NULL,
                doc_url TEXT NOT NULL,
                doc_title TEXT,
                doc_description TEXT,
                doc_published_date VARCHAR(50),
                doc_author VARCHAR(255),
                doc_source VARCHAR(255),
                retrieval_position INTEGER NOT NULL,
                vector_similarity_score DOUBLE PRECISION,
                keyword_boost_score DOUBLE PRECISION,
                bm25_score DOUBLE PRECISION,
                temporal_boost DOUBLE PRECISION,
                domain_match INTEGER,
                final_retrieval_score DOUBLE PRECISION,
                query_term_count INTEGER,
                doc_length INTEGER,
                title_exact_match INTEGER DEFAULT 0,
                desc_exact_match INTEGER DEFAULT 0,
                keyword_overlap_ratio DOUBLE PRECISION,
                recency_days INTEGER,
                has_author INTEGER DEFAULT 0,
                retrieval_algorithm VARCHAR(50),
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'ranking_scores': """
            CREATE TABLE IF NOT EXISTS ranking_scores (
                id SERIAL PRIMARY KEY,
                query_id VARCHAR(255) NOT NULL,
                doc_url TEXT NOT NULL,
                ranking_position INTEGER NOT NULL,
                llm_final_score DOUBLE PRECISION,
                llm_snippet TEXT,
                xgboost_score DOUBLE PRECISION,
                xgboost_confidence DOUBLE PRECISION,
                mmr_diversity_score DOUBLE PRECISION,
                final_ranking_score DOUBLE PRECISION,
                ranking_method VARCHAR(50),
                relative_score DOUBLE PRECISION,
                score_percentile DOUBLE PRECISION,
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'user_interactions': """
            CREATE TABLE IF NOT EXISTS user_interactions (
                id SERIAL PRIMARY KEY,
                query_id VARCHAR(255) NOT NULL,
                doc_url TEXT NOT NULL,
                interaction_type VARCHAR(50) NOT NULL,
                interaction_timestamp DOUBLE PRECISION NOT NULL,
                result_position INTEGER,
                dwell_time_ms DOUBLE PRECISION,
                scroll_depth_percent DOUBLE PRECISION,
                clicked INTEGER DEFAULT 0,
                user_id VARCHAR(255),
                org_id VARCHAR(255),
                client_user_agent TEXT,
                client_ip_hash VARCHAR(255),
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'feature_vectors': """
            CREATE TABLE IF NOT EXISTS feature_vectors (
                id SERIAL PRIMARY KEY,
                query_id VARCHAR(255) NOT NULL,
                doc_url TEXT NOT NULL,
                query_length_chars INTEGER,
                query_length_words INTEGER,
                has_quotes INTEGER,
                has_numbers INTEGER,
                has_question_words INTEGER,
                keyword_count INTEGER,
                doc_length_words INTEGER,
                recency_days INTEGER,
                has_author INTEGER,
                has_publication_date INTEGER,
                schema_completeness DOUBLE PRECISION,
                title_length INTEGER,
                description_length INTEGER,
                url_length INTEGER,
                vector_similarity_score DOUBLE PRECISION,
                bm25_score DOUBLE PRECISION,
                keyword_boost DOUBLE PRECISION,
                temporal_boost DOUBLE PRECISION,
                final_retrieval_score DOUBLE PRECISION,
                keyword_overlap_ratio DOUBLE PRECISION,
                title_exact_match INTEGER,
                retrieval_position INTEGER,
                ranking_position INTEGER,
                llm_final_score DOUBLE PRECISION,
                relative_score_to_top DOUBLE PRECISION,
                score_percentile DOUBLE PRECISION,
                position_change INTEGER,
                mmr_diversity_score DOUBLE PRECISION,
                detected_intent INTEGER,
                query_type VARCHAR(50),
                has_temporal_indicator INTEGER,
                has_brand_mention INTEGER,
                doc_length_chars INTEGER,
                domain_authority DOUBLE PRECISION,
                query_term_coverage DOUBLE PRECISION,
                domain_match INTEGER,
                entity_match_count INTEGER,
                partial_match_count INTEGER,
                clicked INTEGER DEFAULT 0,
                dwell_time_ms DOUBLE PRECISION,
                relevance_grade INTEGER,
                schema_version INTEGER DEFAULT 2,
                created_at DOUBLE PRECISION NOT NULL,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'user_feedback': """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id SERIAL PRIMARY KEY,
                query TEXT,
                answer_snippet TEXT,
                rating VARCHAR(20) NOT NULL,
                comment TEXT,
                session_id VARCHAR(255),
                query_id VARCHAR(255),
                user_id VARCHAR(255),
                org_id VARCHAR(255),
                created_at DOUBLE PRECISION NOT NULL,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE SET NULL
            )
        """,
        'tier_6_enrichment': """
            CREATE TABLE IF NOT EXISTS tier_6_enrichment (
                id SERIAL PRIMARY KEY,
                query_id VARCHAR(255) NOT NULL,
                source_type VARCHAR(50) NOT NULL,
                cache_hit INTEGER DEFAULT 0,
                latency_ms INTEGER,
                timeout_occurred INTEGER DEFAULT 0,
                result_count INTEGER,
                timestamp DOUBLE PRECISION NOT NULL,
                metadata TEXT,
                schema_version INTEGER DEFAULT 2,
                FOREIGN KEY (query_id) REFERENCES queries(query_id) ON DELETE CASCADE
            )
        """,
        'guardrail_events': """
            CREATE TABLE IF NOT EXISTS guardrail_events (
                id SERIAL PRIMARY KEY,
                timestamp DOUBLE PRECISION NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                severity VARCHAR(20) NOT NULL DEFAULT 'info',
                user_id VARCHAR(255),
                client_ip VARCHAR(45),
                details TEXT,
                schema_version INTEGER DEFAULT 2
            )
        """,
    }


# ---------------------------------------------------------------------------
# Indexes — same syntax for SQLite and PostgreSQL
# ---------------------------------------------------------------------------

def get_index_sql(db_type: str = 'sqlite') -> List[str]:
    """
    Return CREATE INDEX statements for all analytics tables.

    Args:
        db_type: 'sqlite' or 'postgres' (same SQL syntax for both)

    Returns:
        List of CREATE INDEX IF NOT EXISTS statements
    """
    return [
        # queries
        "CREATE INDEX IF NOT EXISTS idx_queries_timestamp ON queries(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_queries_user_id ON queries(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_queries_mode ON queries(mode)",
        "CREATE INDEX IF NOT EXISTS idx_queries_org_id ON queries(org_id)",
        # retrieved_documents
        "CREATE INDEX IF NOT EXISTS idx_retrieved_docs_query ON retrieved_documents(query_id)",
        # ranking_scores
        "CREATE INDEX IF NOT EXISTS idx_ranking_scores_query ON ranking_scores(query_id)",
        # user_interactions
        "CREATE INDEX IF NOT EXISTS idx_interactions_query ON user_interactions(query_id)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_url ON user_interactions(doc_url)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_user_id ON user_interactions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_interactions_org_id ON user_interactions(org_id)",
        # feature_vectors
        "CREATE INDEX IF NOT EXISTS idx_feature_vectors_query ON feature_vectors(query_id)",
        "CREATE INDEX IF NOT EXISTS idx_feature_vectors_doc ON feature_vectors(doc_url)",
        "CREATE INDEX IF NOT EXISTS idx_feature_vectors_clicked ON feature_vectors(clicked)",
        # user_feedback
        "CREATE INDEX IF NOT EXISTS idx_user_feedback_created ON user_feedback(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_user_feedback_rating ON user_feedback(rating)",
        "CREATE INDEX IF NOT EXISTS idx_user_feedback_query_id ON user_feedback(query_id)",
        # tier_6_enrichment
        "CREATE INDEX IF NOT EXISTS idx_tier_6_query ON tier_6_enrichment(query_id)",
        "CREATE INDEX IF NOT EXISTS idx_tier_6_source_type ON tier_6_enrichment(source_type)",
        # guardrail_events
        "CREATE INDEX IF NOT EXISTS idx_guardrail_timestamp ON guardrail_events(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_guardrail_event_type ON guardrail_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_guardrail_severity ON guardrail_events(severity)",
        "CREATE INDEX IF NOT EXISTS idx_guardrail_user_id ON guardrail_events(user_id)",
    ]
