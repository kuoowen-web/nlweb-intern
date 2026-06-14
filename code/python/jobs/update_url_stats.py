"""
URL Stats Aggregation Job (Task B3)

Aggregates user interaction data into url_stats table for historical features.
This prepares for Phase C feature expansion (29 -> 35 features).

Schema:
    CREATE TABLE url_stats (
        doc_url TEXT PRIMARY KEY,
        ctr_7d REAL DEFAULT 0.0,
        ctr_30d REAL DEFAULT 0.0,
        avg_dwell_time_ms REAL DEFAULT 0.0,
        times_shown_30d INTEGER DEFAULT 0,
        last_updated REAL NOT NULL
    );

Run this job periodically (e.g., daily cron) to keep stats fresh.
"""

import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta

def get_db_path() -> Path:
    """Get database path."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return project_root / "data" / "analytics" / "query_logs.db"

def create_url_stats_table(cursor):
    """Create url_stats table if it doesn't exist."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS url_stats (
            doc_url TEXT PRIMARY KEY,
            ctr_7d REAL DEFAULT 0.0,
            ctr_30d REAL DEFAULT 0.0,
            avg_dwell_time_ms REAL DEFAULT 0.0,
            times_shown_30d INTEGER DEFAULT 0,
            last_updated REAL NOT NULL
        )
    ''')
    print("[OK] url_stats table ready")

def update_url_stats(cursor):
    """Update aggregated stats for all URLs."""
    now = time.time()
    seven_days_ago = now - (7 * 24 * 3600)
    thirty_days_ago = now - (30 * 24 * 3600)

    print("\nAggregating URL statistics...")

    # Aggregate stats using SQL
    cursor.execute('''
        WITH url_aggregates AS (
            SELECT
                rd.doc_url,

                -- Times shown in last 30 days
                COUNT(DISTINCT rd.query_id) as times_shown_30d,

                -- Clicks in last 7 days
                SUM(CASE
                    WHEN ui.clicked = 1
                    AND q.timestamp >= ?
                    THEN 1 ELSE 0
                END) as clicks_7d,

                -- Clicks in last 30 days
                SUM(CASE
                    WHEN ui.clicked = 1
                    AND q.timestamp >= ?
                    THEN 1 ELSE 0
                END) as clicks_30d,

                -- Average dwell time (for clicked results)
                AVG(CASE
                    WHEN ui.clicked = 1 AND ui.dwell_time_ms IS NOT NULL
                    THEN ui.dwell_time_ms
                    ELSE NULL
                END) as avg_dwell_time_ms

            FROM retrieved_documents rd
            JOIN queries q ON rd.query_id = q.query_id
            LEFT JOIN user_interactions ui
                ON rd.query_id = ui.query_id
                AND rd.doc_url = ui.doc_url
            WHERE q.timestamp >= ?
            GROUP BY rd.doc_url
        )
        SELECT
            doc_url,
            CAST(clicks_7d AS REAL) / NULLIF(times_shown_30d, 0) as ctr_7d,
            CAST(clicks_30d AS REAL) / NULLIF(times_shown_30d, 0) as ctr_30d,
            COALESCE(avg_dwell_time_ms, 0.0) as avg_dwell_time_ms,
            times_shown_30d
        FROM url_aggregates
    ''', (seven_days_ago, thirty_days_ago, thirty_days_ago))

    results = cursor.fetchall()

    # Upsert into url_stats
    update_count = 0
    for row in results:
        doc_url, ctr_7d, ctr_30d, avg_dwell, times_shown = row

        cursor.execute('''
            INSERT INTO url_stats (doc_url, ctr_7d, ctr_30d, avg_dwell_time_ms, times_shown_30d, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_url) DO UPDATE SET
                ctr_7d = excluded.ctr_7d,
                ctr_30d = excluded.ctr_30d,
                avg_dwell_time_ms = excluded.avg_dwell_time_ms,
                times_shown_30d = excluded.times_shown_30d,
                last_updated = excluded.last_updated
        ''', (doc_url, ctr_7d or 0.0, ctr_30d or 0.0, avg_dwell or 0.0, times_shown or 0, now))

        update_count += 1

    print(f"  Updated {update_count} URLs")
    return update_count

def print_sample_stats(cursor):
    """Print sample statistics."""
    cursor.execute('''
        SELECT doc_url, ctr_30d, avg_dwell_time_ms, times_shown_30d
        FROM url_stats
        WHERE times_shown_30d > 0
        ORDER BY ctr_30d DESC
        LIMIT 5
    ''')

    print("\nTop 5 URLs by CTR (30-day):")
    print("-" * 80)
    for row in cursor.fetchall():
        url, ctr, dwell, times_shown = row
        print(f"  {url[:60]}")
        print(f"    CTR: {ctr*100:.1f}%, Avg Dwell: {dwell:.0f}ms, Shown: {times_shown}x")

def main():
    """Main function."""
    print("=" * 60)
    print("URL Stats Aggregation Job (Task B3)")
    print("=" * 60)

    db_path = get_db_path()
    print(f"\nDatabase: {db_path}")

    if not db_path.exists():
        print("ERROR: Database not found")
        return 1

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Create table
        create_url_stats_table(cursor)

        # Update stats
        update_count = update_url_stats(cursor)

        # Commit
        conn.commit()

        # Show samples
        if update_count > 0:
            print_sample_stats(cursor)

        print("\n" + "=" * 60)
        print(f"[SUCCESS] Updated {update_count} URL statistics")
        print("=" * 60)
        print("\nNext: Schedule this job to run daily:")
        print("  cron: 0 2 * * * cd /path/to/NLWeb && python code/python/jobs/update_url_stats.py")

        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        conn.close()

if __name__ == '__main__':
    import sys
    sys.exit(main())
