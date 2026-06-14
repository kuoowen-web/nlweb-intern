"""
Export Training Data for XGBoost (Task B1)

Exports training data from analytics database to CSV format.
Uses synthetic labels (LLM scores) for Phase C1 training.

Output:
- training_data.csv: 29 features + llm_final_score (label)
- training_metadata.json: query_groups for GroupKFold validation
"""

import sqlite3
import csv
import json
import sys
from pathlib import Path
from typing import List, Dict, Tuple
from collections import defaultdict

# Add parent directory to path to import feature_engineering
# Also add code/python to path for misc.logger imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from feature_engineering import (
    extract_query_features,
    extract_document_features,
    extract_query_doc_features,
    extract_ranking_features,
    extract_mmr_features,
    TOTAL_FEATURES_PHASE_A
)

def get_db_path() -> Path:
    """Get absolute path to analytics database from project root."""
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    db_path = project_root / "data" / "analytics" / "query_logs.db"
    return db_path

def get_output_dir() -> Path:
    """Get output directory for training data."""
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    output_dir = project_root / "data" / "training"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir

def fetch_training_data(db_path: Path) -> List[Dict]:
    """
    Fetch all training samples from database.

    Returns:
        List of dicts, each containing all raw data needed for feature extraction
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Complex JOIN to get all data needed for feature extraction
    query = '''
        SELECT
            q.query_id,
            q.query_text,
            q.mode,
            rd.doc_url,
            rd.doc_title,
            rd.doc_description,
            rd.doc_published_date AS published_date,
            rd.doc_author AS author,
            rd.vector_similarity_score AS vector_similarity,
            rd.bm25_score,
            rd.keyword_boost_score AS keyword_boost,
            rd.temporal_boost,
            rd.final_retrieval_score,
            rd.retrieval_position,
            llm.llm_final_score,
            llm.ranking_position AS llm_ranking_position,
            llm.relative_score AS llm_relative_score,
            llm.score_percentile AS llm_score_percentile,
            llm.ranking_method AS llm_ranking_method,
            xgb.xgboost_score,
            xgb.mmr_diversity_score
        FROM queries q
        JOIN retrieved_documents rd ON q.query_id = rd.query_id
        JOIN ranking_scores llm
            ON rd.query_id = llm.query_id
            AND rd.doc_url = llm.doc_url
        JOIN ranking_scores xgb
            ON rd.query_id = xgb.query_id
            AND rd.doc_url = xgb.doc_url
        WHERE q.mode = 'summarize'
          AND llm.ranking_method LIKE 'llm%'
          AND xgb.ranking_method = 'xgboost_shadow'
          AND llm.llm_final_score IS NOT NULL
          AND xgb.xgboost_score IS NOT NULL
        ORDER BY q.query_id, rd.retrieval_position
    '''

    cursor.execute(query)

    # Get column names
    columns = [desc[0] for desc in cursor.description]

    # Fetch all rows as dicts
    rows = []
    for row in cursor.fetchall():
        row_dict = dict(zip(columns, row))
        rows.append(row_dict)

    conn.close()

    print(f"Fetched {len(rows)} training samples from database")
    return rows

def compute_all_llm_scores_per_query(rows: List[Dict]) -> Dict[str, List[float]]:
    """
    Pre-compute all LLM scores per query for percentile calculation.

    Returns:
        Dict mapping query_id -> list of all LLM scores for that query
    """
    scores_by_query = defaultdict(list)
    for row in rows:
        scores_by_query[row['query_id']].append(row['llm_final_score'])

    return dict(scores_by_query)

def extract_features_from_row(row: Dict, all_llm_scores: List[float]) -> List[float]:
    """
    Extract all 29 features from a database row.

    Args:
        row: Dict from database query
        all_llm_scores: List of all LLM scores for this query (for percentile)

    Returns:
        List of 29 feature values
    """
    # Query features (6)
    query_feats = extract_query_features(row['query_text'])

    # Document features (8)
    doc_feats = extract_document_features(
        doc_title=row['doc_title'] or '',
        doc_description=row['doc_description'] or '',
        published_date=row['published_date'],
        author=row['author'],
        url=row['doc_url']
    )

    # Query-Document features (7)
    query_doc_feats = extract_query_doc_features(
        query_text=row['query_text'],
        doc_title=row['doc_title'] or '',
        doc_description=row['doc_description'] or '',
        bm25_score=row['bm25_score'] or 0.0,
        vector_score=row['vector_similarity'] or 0.0,
        keyword_boost=row['keyword_boost'] or 0.0,
        temporal_boost=row['temporal_boost'] or 0.0,
        final_retrieval_score=row['final_retrieval_score'] or 0.0
    )

    # Ranking features (6)
    ranking_feats = extract_ranking_features(
        retrieval_position=row['retrieval_position'] or 0,
        ranking_position=row['llm_ranking_position'] or 0,
        llm_final_score=row['llm_final_score'],
        all_llm_scores=all_llm_scores
    )

    # MMR features (2)
    # Note: detected_intent is not stored in DB yet (Phase A), defaulting to None
    mmr_feats = extract_mmr_features(
        mmr_diversity_score=row['mmr_diversity_score'],
        detected_intent=None  # Will default to BALANCED (encoded as 2)
    )

    # Combine all features in order
    feature_vector = [
        # Query (6)
        query_feats['query_length'],
        query_feats['word_count'],
        query_feats['has_quotes'],
        query_feats['has_numbers'],
        query_feats['has_question_words'],
        query_feats['keyword_count'],
        # Document (8)
        doc_feats['doc_length'],
        doc_feats['recency_days'],
        doc_feats['has_author'],
        doc_feats['has_publication_date'],
        doc_feats['schema_completeness'],
        doc_feats['title_length'],
        doc_feats['description_length'],
        doc_feats['url_length'],
        # Query-Document (7)
        query_doc_feats['vector_similarity_score'],
        query_doc_feats['bm25_score'],
        query_doc_feats['keyword_boost'],
        query_doc_feats['temporal_boost'],
        query_doc_feats['final_retrieval_score'],
        query_doc_feats['keyword_overlap_ratio'],
        query_doc_feats['title_exact_match'],
        # Ranking (6)
        ranking_feats['retrieval_position'],
        ranking_feats['ranking_position'],
        ranking_feats['llm_final_score'],
        ranking_feats['relative_score_to_top'],
        ranking_feats['score_percentile'],
        ranking_feats['position_change'],
        # MMR (2)
        mmr_feats['mmr_diversity_score'],
        mmr_feats['detected_intent']
    ]

    assert len(feature_vector) == TOTAL_FEATURES_PHASE_A, \
        f"Expected {TOTAL_FEATURES_PHASE_A} features, got {len(feature_vector)}"

    return feature_vector

def export_to_csv(rows: List[Dict], output_path: Path) -> Tuple[int, Dict]:
    """
    Export training data to CSV with metadata.

    Args:
        rows: List of database rows
        output_path: Path to output CSV file

    Returns:
        Tuple of (num_rows_exported, metadata_dict)
    """
    # Pre-compute all LLM scores per query
    all_scores_map = compute_all_llm_scores_per_query(rows)

    # Track query groups for GroupKFold
    query_groups = []
    current_query_id = None
    current_group_size = 0

    # Feature names for CSV header
    feature_names = [
        # Query (6)
        'query_length', 'word_count', 'has_quotes', 'has_numbers',
        'has_question_words', 'keyword_count',
        # Document (8)
        'doc_length', 'recency_days', 'has_author', 'has_publication_date',
        'schema_completeness', 'title_length', 'description_length', 'url_length',
        # Query-Document (7)
        'vector_similarity', 'bm25_score', 'keyword_boost', 'temporal_boost',
        'final_retrieval_score', 'keyword_overlap_ratio', 'title_exact_match',
        # Ranking (6)
        'retrieval_position', 'ranking_position', 'llm_final_score',
        'relative_score_to_top', 'score_percentile', 'position_change',
        # MMR (2)
        'mmr_diversity_score', 'detected_intent',
        # Label
        'label'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(feature_names)

        for row in rows:
            # Track query group boundaries
            if row['query_id'] != current_query_id:
                if current_query_id is not None:
                    query_groups.append(current_group_size)
                current_query_id = row['query_id']
                current_group_size = 0
            current_group_size += 1

            # Extract features
            all_llm_scores = all_scores_map[row['query_id']]
            features = extract_features_from_row(row, all_llm_scores)

            # Label is LLM final score (synthetic label for Phase C1)
            label = row['llm_final_score']

            # Write row
            writer.writerow(features + [label])

        # Don't forget the last group
        if current_group_size > 0:
            query_groups.append(current_group_size)

    # Create metadata
    metadata = {
        'feature_version': 'phase_a',
        'expected_features': TOTAL_FEATURES_PHASE_A,
        'feature_names': feature_names[:-1],  # Exclude 'label'
        'total_samples': len(rows),
        'total_queries': len(query_groups),
        'query_groups': query_groups,
        'label_type': 'llm_final_score',
        'label_description': 'Synthetic labels from LLM ranking (0-100)',
        'export_timestamp': Path(__file__).stat().st_mtime
    }

    return len(rows), metadata

def main():
    """Main export function."""
    print("=" * 60)
    print("XGBoost Training Data Export (Task B1)")
    print("=" * 60)

    # Get paths
    db_path = get_db_path()
    output_dir = get_output_dir()
    csv_path = output_dir / "training_data.csv"
    metadata_path = output_dir / "training_metadata.json"

    print(f"\nDatabase: {db_path}")
    print(f"Output CSV: {csv_path}")
    print(f"Output Metadata: {metadata_path}")

    # Fetch data
    print("\nFetching training data from database...")
    rows = fetch_training_data(db_path)

    if len(rows) == 0:
        print("\nERROR: No training data found!")
        print("Verify that shadow mode has run and generated xgboost_shadow records.")
        return

    # Export to CSV
    print(f"\nExporting {len(rows)} samples to CSV...")
    num_exported, metadata = export_to_csv(rows, csv_path)

    # Save metadata
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    # Summary
    print("\n" + "=" * 60)
    print("[SUCCESS] Training data export complete!")
    print("=" * 60)
    print(f"Exported: {num_exported} samples")
    print(f"Queries: {metadata['total_queries']}")
    print(f"Features: {metadata['expected_features']}")
    print(f"Label: {metadata['label_type']}")
    print(f"\nQuery group sizes: {metadata['query_groups']}")
    print(f"\nFiles created:")
    print(f"  - {csv_path}")
    print(f"  - {metadata_path}")
    print("\nNext step: Run validate_training_data.py to verify data quality")

if __name__ == '__main__':
    main()
