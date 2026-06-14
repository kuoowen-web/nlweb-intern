#!/usr/bin/env python3
"""
Migration script: Schema v1 → Schema v2
Adds new ML feature columns to existing analytics tables.

Usage:
    python migrate_schema_v2.py

This script will:
1. Connect to the analytics database (SQLite or PostgreSQL)
2. Check current schema version
3. Add new v2 columns to existing tables
4. Create new feature_vectors table
5. Update schema_version to 2
"""

import os
import sys
sys.path.insert(0, 'code/python')

from core.analytics_db import AnalyticsDB
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("schema_migration")


def migrate_to_v2(db: AnalyticsDB):
    """Migrate existing database from v1 to v2."""

    conn = db.connect()
    cursor = conn.cursor()

    logger.info(f"Starting Schema v2 migration on {db.db_type} database")

    try:
        # Check if migration already done (check for schema_version column)
        if db.db_type == 'postgres':
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='queries' AND column_name='schema_version'
            """)
        else:
            cursor.execute("PRAGMA table_info(queries)")
            columns = [row[1] for row in cursor.fetchall()]

        if db.db_type == 'sqlite':
            has_schema_version = 'schema_version' in columns
        else:
            has_schema_version = cursor.fetchone() is not None

        if has_schema_version:
            logger.info("Schema v2 columns already exist. Skipping migration.")
            conn.close()
            return

        logger.info("Adding new v2 columns to queries table...")
        if db.db_type == 'postgres':
            cursor.execute("""
                ALTER TABLE queries
                ADD COLUMN IF NOT EXISTS query_length_words INTEGER,
                ADD COLUMN IF NOT EXISTS query_length_chars INTEGER,
                ADD COLUMN IF NOT EXISTS has_temporal_indicator INTEGER DEFAULT 0,
                ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100),
                ADD COLUMN IF NOT EXISTS parent_query_id VARCHAR(255),
                ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
            """)
        else:
            # SQLite doesn't support multiple ADD COLUMN in one statement
            alter_queries = [
                "ALTER TABLE queries ADD COLUMN query_length_words INTEGER",
                "ALTER TABLE queries ADD COLUMN query_length_chars INTEGER",
                "ALTER TABLE queries ADD COLUMN has_temporal_indicator INTEGER DEFAULT 0",
                "ALTER TABLE queries ADD COLUMN embedding_model TEXT",
                "ALTER TABLE queries ADD COLUMN parent_query_id TEXT",
                "ALTER TABLE queries ADD COLUMN schema_version INTEGER DEFAULT 2"
            ]
            for sql in alter_queries:
                try:
                    cursor.execute(sql)
                except Exception as e:
                    if "duplicate column name" not in str(e).lower():
                        raise

        logger.info("Adding new v2 columns to retrieved_documents table...")
        if db.db_type == 'postgres':
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
        else:
            alter_retrieved = [
                "ALTER TABLE retrieved_documents ADD COLUMN query_term_count INTEGER",
                "ALTER TABLE retrieved_documents ADD COLUMN doc_length INTEGER",
                "ALTER TABLE retrieved_documents ADD COLUMN title_exact_match INTEGER DEFAULT 0",
                "ALTER TABLE retrieved_documents ADD COLUMN desc_exact_match INTEGER DEFAULT 0",
                "ALTER TABLE retrieved_documents ADD COLUMN keyword_overlap_ratio REAL",
                "ALTER TABLE retrieved_documents ADD COLUMN recency_days INTEGER",
                "ALTER TABLE retrieved_documents ADD COLUMN has_author INTEGER DEFAULT 0",
                "ALTER TABLE retrieved_documents ADD COLUMN retrieval_algorithm TEXT",
                "ALTER TABLE retrieved_documents ADD COLUMN schema_version INTEGER DEFAULT 2"
            ]
            for sql in alter_retrieved:
                try:
                    cursor.execute(sql)
                except Exception as e:
                    if "duplicate column name" not in str(e).lower():
                        raise

        logger.info("Adding new v2 columns to ranking_scores table...")
        if db.db_type == 'postgres':
            cursor.execute("""
                ALTER TABLE ranking_scores
                ADD COLUMN IF NOT EXISTS relative_score DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS score_percentile DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
            """)
        else:
            alter_ranking = [
                "ALTER TABLE ranking_scores ADD COLUMN relative_score REAL",
                "ALTER TABLE ranking_scores ADD COLUMN score_percentile REAL",
                "ALTER TABLE ranking_scores ADD COLUMN schema_version INTEGER DEFAULT 2"
            ]
            for sql in alter_ranking:
                try:
                    cursor.execute(sql)
                except Exception as e:
                    if "duplicate column name" not in str(e).lower():
                        raise

        logger.info("Adding new v2 column to user_interactions table...")
        if db.db_type == 'postgres':
            cursor.execute("""
                ALTER TABLE user_interactions
                ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 2
            """)
        else:
            try:
                cursor.execute("ALTER TABLE user_interactions ADD COLUMN schema_version INTEGER DEFAULT 2")
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    raise

        logger.info("Creating feature_vectors table...")
        # Get schema from AnalyticsDB
        from core.query_logger import QueryLogger
        query_logger = QueryLogger()
        schema = query_logger._get_postgres_schema() if db.db_type == 'postgres' else query_logger._get_sqlite_schema()

        cursor.execute(schema['feature_vectors'])

        conn.commit()
        logger.info("✅ Schema v2 migration completed successfully!")

    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db = AnalyticsDB()

    logger.info(f"Database type: {db.db_type}")
    if db.db_type == 'sqlite':
        logger.info(f"Database path: {db.db_path.absolute()}")
    else:
        logger.info("Using PostgreSQL (Neon)")

    response = input("\nProceed with migration to Schema v2? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        migrate_to_v2(db)
    else:
        logger.info("Migration cancelled by user")
