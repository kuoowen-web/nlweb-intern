"""
Database State Verification Script
Verifies that shadow mode data is properly structured for training.
"""

import sqlite3
from pathlib import Path

def get_db_path() -> Path:
    """Get absolute path to analytics database from project root."""
    # Navigate up: verify_db_state.py -> training/ -> python/ -> code/ -> NLWeb/
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    db_path = project_root / "data" / "analytics" / "query_logs.db"
    return db_path

def verify_database_state():
    """Verify the database has valid training data."""

    db_path = get_db_path()

    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=" * 60)
    print("DATABASE STATE VERIFICATION")
    print("=" * 60)

    # Check total records in ranking_scores
    cursor.execute('SELECT COUNT(*) FROM ranking_scores')
    total_records = cursor.fetchone()[0]
    print(f'\nTotal ranking_scores records: {total_records}')

    # Check distribution by ranking_method
    cursor.execute('SELECT ranking_method, COUNT(*) FROM ranking_scores GROUP BY ranking_method')
    print('\nRecords by ranking_method:')
    for row in cursor.fetchall():
        print(f'  {row[0]}: {row[1]}')

    # Check a sample record structure
    cursor.execute('SELECT * FROM ranking_scores WHERE ranking_method = "llm" LIMIT 1')
    llm_cols = [desc[0] for desc in cursor.description]
    print(f'\nLLM record columns: {llm_cols}')

    cursor.execute('SELECT * FROM ranking_scores WHERE ranking_method = "xgboost_shadow" LIMIT 1')
    if cursor.fetchone():
        xgb_cols = [desc[0] for desc in cursor.description]
        print(f'XGBoost Shadow record columns: {xgb_cols}')
    else:
        print('WARNING: No xgboost_shadow records found!')

    # Check how many (query_id, doc_url) pairs have BOTH llm and xgboost_shadow
    cursor.execute('''
        SELECT COUNT(DISTINCT llm.query_id || '|' || llm.doc_url)
        FROM ranking_scores llm
        JOIN ranking_scores xgb
            ON llm.query_id = xgb.query_id
            AND llm.doc_url = xgb.doc_url
        WHERE llm.ranking_method LIKE 'llm%'
          AND xgb.ranking_method = 'xgboost_shadow'
          AND llm.llm_final_score IS NOT NULL
          AND xgb.xgboost_score IS NOT NULL
    ''')
    valid_pairs = cursor.fetchone()[0]
    print(f'\n[OK] Valid training pairs (both LLM + XGBoost): {valid_pairs}')

    # Check query mode distribution
    cursor.execute('''
        SELECT q.mode, COUNT(DISTINCT llm.query_id || '|' || llm.doc_url)
        FROM ranking_scores llm
        JOIN ranking_scores xgb
            ON llm.query_id = xgb.query_id
            AND llm.doc_url = xgb.doc_url
        JOIN queries q ON llm.query_id = q.query_id
        WHERE llm.ranking_method LIKE 'llm%'
          AND xgb.ranking_method = 'xgboost_shadow'
          AND llm.llm_final_score IS NOT NULL
          AND xgb.xgboost_score IS NOT NULL
        GROUP BY q.mode
    ''')
    print('\nValid pairs by query mode:')
    for row in cursor.fetchall():
        print(f'  {row[0]}: {row[1]}')

    # Count unique queries (for GroupKFold)
    cursor.execute('''
        SELECT COUNT(DISTINCT llm.query_id)
        FROM ranking_scores llm
        JOIN ranking_scores xgb
            ON llm.query_id = xgb.query_id
            AND llm.doc_url = xgb.doc_url
        JOIN queries q ON llm.query_id = q.query_id
        WHERE llm.ranking_method LIKE 'llm%'
          AND xgb.ranking_method = 'xgboost_shadow'
          AND llm.llm_final_score IS NOT NULL
          AND xgb.xgboost_score IS NOT NULL
          AND q.mode = 'summarize'
    ''')
    unique_queries = cursor.fetchone()[0]
    print(f'\n[OK] Unique summarize queries: {unique_queries}')

    # Sample a few records to verify structure
    cursor.execute('''
        SELECT llm.query_id, llm.doc_url, llm.llm_final_score, xgb.xgboost_score
        FROM ranking_scores llm
        JOIN ranking_scores xgb
            ON llm.query_id = xgb.query_id
            AND llm.doc_url = xgb.doc_url
        WHERE llm.ranking_method LIKE 'llm%'
          AND xgb.ranking_method = 'xgboost_shadow'
        LIMIT 3
    ''')
    print('\nSample records (query_id, doc_url, llm_score, xgb_score):')
    for row in cursor.fetchall():
        print(f'  {row[0][:20]}... | {row[1][:40]}... | LLM:{row[2]:.2f} | XGB:{row[3]:.4f}')

    conn.close()

    print("\n" + "=" * 60)
    if valid_pairs > 0 and unique_queries > 0:
        print("[PASS] Database verification PASSED")
        print(f"[PASS] Ready to export {valid_pairs} training samples from {unique_queries} queries")
        return True
    else:
        print("[FAIL] Database verification FAILED - insufficient data")
        return False

if __name__ == '__main__':
    verify_database_state()
