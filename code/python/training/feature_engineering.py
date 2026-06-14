# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Feature Engineering Module for XGBoost Ranking

Extracts 29 ML features from analytics database and populates feature_vectors table.
This module is designed to work in batch mode for Phase C training.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import re
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("feature_engineering")

# Feature version - must match analytics schema
FEATURE_VERSION = 2

# ============================================================================
# Feature Index Constants (0-based)
# Total: 29 features (Phase A)
#
# These constants define the position of each feature in the feature vector.
# DO NOT change the order or indices - trained models depend on this structure.
# Phase C will ADD new constants (30+), not replace existing ones.
# ============================================================================

# Query Features (0-5)
FEATURE_IDX_QUERY_LENGTH = 0
FEATURE_IDX_WORD_COUNT = 1
FEATURE_IDX_HAS_QUOTES = 2
FEATURE_IDX_HAS_NUMBERS = 3
FEATURE_IDX_HAS_QUESTION_WORDS = 4
FEATURE_IDX_KEYWORD_COUNT = 5

# Document Features (6-13)
FEATURE_IDX_DOC_LENGTH = 6
FEATURE_IDX_RECENCY_DAYS = 7
FEATURE_IDX_HAS_AUTHOR = 8
FEATURE_IDX_HAS_PUBLICATION_DATE = 9
FEATURE_IDX_SCHEMA_COMPLETENESS = 10
FEATURE_IDX_TITLE_LENGTH = 11
FEATURE_IDX_DESCRIPTION_LENGTH = 12
FEATURE_IDX_URL_LENGTH = 13

# Query-Document Features (14-20)
FEATURE_IDX_VECTOR_SIMILARITY = 14
FEATURE_IDX_BM25_SCORE = 15
FEATURE_IDX_KEYWORD_BOOST = 16
FEATURE_IDX_TEMPORAL_BOOST = 17
FEATURE_IDX_FINAL_RETRIEVAL_SCORE = 18
FEATURE_IDX_KEYWORD_OVERLAP_RATIO = 19
FEATURE_IDX_TITLE_EXACT_MATCH = 20

# Ranking Features (21-26)
FEATURE_IDX_RETRIEVAL_POSITION = 21
FEATURE_IDX_RANKING_POSITION = 22
FEATURE_IDX_LLM_FINAL_SCORE = 23  # ← CRITICAL: XGBoost uses this
FEATURE_IDX_RELATIVE_SCORE_TO_TOP = 24
FEATURE_IDX_SCORE_PERCENTILE = 25
FEATURE_IDX_POSITION_CHANGE = 26

# MMR Features (27-28)
FEATURE_IDX_MMR_DIVERSITY_SCORE = 27
FEATURE_IDX_DETECTED_INTENT = 28

# Total feature count for Phase A
TOTAL_FEATURES_PHASE_A = 29

# ============================================================================
# Magic Numbers (for better code readability)
# ============================================================================

MISSING_RECENCY_DAYS = 999999  # Placeholder for documents with no publication date

# === Query Feature Extraction ===

def extract_query_features(query_text: str) -> Dict[str, Any]:
    """
    Extract query-level features.

    Args:
        query_text: The search query string

    Returns:
        Dict with 6 query features:
        - query_length: Number of characters
        - word_count: Number of words/tokens
        - has_quotes: Boolean (0/1)
        - has_numbers: Boolean (0/1)
        - has_question_words: Boolean (0/1)
        - keyword_count: Number of keywords extracted
    """
    if not query_text:
        return {
            'query_length': 0,
            'word_count': 0,
            'has_quotes': 0,
            'has_numbers': 0,
            'has_question_words': 0,
            'keyword_count': 0
        }

    # Query length
    query_length = len(query_text)

    # Word count (split on whitespace)
    words = query_text.split()
    word_count = len(words)

    # Has quotes
    has_quotes = 1 if ('"' in query_text or "'" in query_text) else 0

    # Has numbers
    has_numbers = 1 if re.search(r'\d', query_text) else 0

    # Has question words (Chinese + English)
    question_words = [
        '什麼', '為什麼', '如何', '怎麼', '哪裡', '哪些', '誰', '何時',
        'what', 'why', 'how', 'where', 'which', 'who', 'when'
    ]
    has_question_words = 1 if any(qw in query_text.lower() for qw in question_words) else 0

    # Keyword count (simple tokenization: words with length >= 2)
    keywords = [w for w in words if len(w) >= 2]
    keyword_count = len(keywords)

    return {
        'query_length': query_length,
        'word_count': word_count,
        'has_quotes': has_quotes,
        'has_numbers': has_numbers,
        'has_question_words': has_question_words,
        'keyword_count': keyword_count
    }


# === Document Feature Extraction ===

def extract_document_features(
    doc_title: str,
    doc_description: str,
    published_date: Optional[str],
    author: Optional[str],
    url: str
) -> Dict[str, Any]:
    """
    Extract document-level features.

    Args:
        doc_title: Document title
        doc_description: Document description/content
        published_date: Publication date (ISO format or None)
        author: Author name (or None)
        url: Document URL

    Returns:
        Dict with 8 document features:
        - doc_length: Word count in description
        - recency_days: Days since publication (MISSING_RECENCY_DAYS if no date)
        - has_author: Boolean (0/1)
        - has_publication_date: Boolean (0/1)
        - schema_completeness: % of fields populated (0-1)
        - title_length: Number of characters in title
        - description_length: Number of characters in description
        - url_length: Number of characters in URL
    """
    # Document length (word count in description)
    doc_length = len(doc_description.split()) if doc_description else 0

    # Recency days
    if published_date:
        try:
            pub_dt = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
            recency_days = (datetime.now(pub_dt.tzinfo) - pub_dt).days
        except Exception:
            recency_days = MISSING_RECENCY_DAYS  # Invalid date
    else:
        recency_days = MISSING_RECENCY_DAYS  # No date

    # Has author
    has_author = 1 if (author and len(author) > 0) else 0

    # Has publication date
    has_publication_date = 1 if published_date else 0

    # Schema completeness (% of fields populated)
    fields = [doc_title, doc_description, published_date, author, url]
    populated_fields = sum(1 for f in fields if f and len(str(f)) > 0)
    schema_completeness = populated_fields / len(fields)

    # Title length
    title_length = len(doc_title) if doc_title else 0

    # Description length
    description_length = len(doc_description) if doc_description else 0

    # URL length
    url_length = len(url) if url else 0

    return {
        'doc_length': doc_length,
        'recency_days': recency_days,
        'has_author': has_author,
        'has_publication_date': has_publication_date,
        'schema_completeness': schema_completeness,
        'title_length': title_length,
        'description_length': description_length,
        'url_length': url_length
    }


# === Query-Document Feature Extraction ===

def extract_query_doc_features(
    query_text: str,
    doc_title: str,
    doc_description: str,
    bm25_score: float,
    vector_score: float,
    keyword_boost: float,
    temporal_boost: float,
    final_retrieval_score: float
) -> Dict[str, Any]:
    """
    Extract query-document interaction features.

    Args:
        query_text: The search query
        doc_title: Document title
        doc_description: Document description
        bm25_score: BM25 keyword relevance score
        vector_score: Vector similarity score
        keyword_boost: Keyword boosting score
        temporal_boost: Temporal boosting score
        final_retrieval_score: Combined retrieval score

    Returns:
        Dict with 7 retrieval features:
        - vector_similarity_score: Cosine similarity (0-1)
        - bm25_score: Keyword relevance
        - keyword_boost: Keyword boosting
        - temporal_boost: Recency boosting
        - final_retrieval_score: Combined score
        - keyword_overlap_ratio: Query-doc keyword overlap (0-1)
        - title_exact_match: Boolean (0/1)
    """
    # Extract keywords from query and document
    query_keywords = set(query_text.lower().split())
    doc_keywords = set((doc_title + " " + doc_description).lower().split())

    # Keyword overlap ratio
    if len(query_keywords) > 0:
        overlap = query_keywords.intersection(doc_keywords)
        keyword_overlap_ratio = len(overlap) / len(query_keywords)
    else:
        keyword_overlap_ratio = 0.0

    # Title exact match
    title_exact_match = 1 if (doc_title and query_text.lower() in doc_title.lower()) else 0

    return {
        'vector_similarity_score': vector_score,
        'bm25_score': bm25_score,
        'keyword_boost': keyword_boost,
        'temporal_boost': temporal_boost,
        'final_retrieval_score': final_retrieval_score,
        'keyword_overlap_ratio': keyword_overlap_ratio,
        'title_exact_match': title_exact_match
    }


# === Ranking Feature Extraction ===

def extract_ranking_features(
    retrieval_position: int,
    ranking_position: int,
    llm_final_score: float,
    all_llm_scores: List[float]
) -> Dict[str, Any]:
    """
    Extract ranking-related features.

    Args:
        retrieval_position: Position in retrieval results (0-based)
        ranking_position: Position after LLM ranking (0-based)
        llm_final_score: LLM-assigned relevance score
        all_llm_scores: List of all LLM scores for this query (for percentile calculation)

    Returns:
        Dict with 6 ranking features:
        - retrieval_position: 0-based position
        - ranking_position: 0-based position after LLM
        - llm_final_score: LLM score
        - relative_score_to_top: Score / top_score (0-1)
        - score_percentile: Percentile ranking (0-100)
        - position_change: retrieval_position - ranking_position
    """
    # Relative score to top
    if all_llm_scores and max(all_llm_scores) > 0:
        relative_score_to_top = llm_final_score / max(all_llm_scores)
    else:
        relative_score_to_top = 1.0

    # Score percentile (0-100)
    if all_llm_scores and len(all_llm_scores) > 1:
        sorted_scores = sorted(all_llm_scores)
        rank = sorted_scores.index(llm_final_score) if llm_final_score in sorted_scores else 0
        score_percentile = (rank / (len(sorted_scores) - 1)) * 100
    else:
        score_percentile = 50.0

    # Position change
    position_change = retrieval_position - ranking_position

    return {
        'retrieval_position': retrieval_position,
        'ranking_position': ranking_position,
        'llm_final_score': llm_final_score,
        'relative_score_to_top': relative_score_to_top,
        'score_percentile': score_percentile,
        'position_change': position_change
    }


# === MMR and Intent Features ===

def extract_mmr_features(
    mmr_diversity_score: Optional[float],
    detected_intent: Optional[str]
) -> Dict[str, Any]:
    """
    Extract MMR diversity and intent features.

    Args:
        mmr_diversity_score: Diversity score from MMR (may be None in Phase A)
        detected_intent: Intent type (SPECIFIC, EXPLORATORY, BALANCED)

    Returns:
        Dict with 2 MMR features:
        - mmr_diversity_score: 0.0 if None
        - detected_intent: Encoded (SPECIFIC=0, EXPLORATORY=1, BALANCED=2)
    """
    # MMR diversity score (default 0.0 if not available)
    mmr_score = mmr_diversity_score if mmr_diversity_score is not None else 0.0

    # Detected intent encoding
    intent_map = {
        'SPECIFIC': 0,
        'EXPLORATORY': 1,
        'BALANCED': 2
    }
    intent_encoded = intent_map.get(detected_intent, 2)  # Default to BALANCED

    return {
        'mmr_diversity_score': mmr_score,
        'detected_intent': intent_encoded
    }


# === Batch Feature Extraction from Database ===

def populate_feature_vectors(
    days: int = 30,
    batch_size: int = 100,
    min_clicks: int = 0
) -> int:
    """
    Extract features from analytics database and populate feature_vectors table.

    This function runs in batch mode to process historical queries and prepare
    training data for XGBoost models.

    Args:
        days: Number of days to look back
        batch_size: Number of rows to insert per batch
        min_clicks: Minimum number of clicks required (0 = include all)

    Returns:
        Number of rows inserted into feature_vectors table

    Process:
        1. Query queries, retrieved_documents, ranking_scores, user_interactions tables
        2. For each query-document pair:
           - Extract 29 features
           - Determine label (clicked, dwell_time_ms, relevance_grade)
        3. Insert into feature_vectors table in batches

    Note: This is a placeholder for Phase A. Full implementation requires:
        - Database connection handling (SQLite/PostgreSQL)
        - JOIN queries across 4 analytics tables
        - Batch INSERT with proper error handling
        - Progress logging and resumption support
    """
    logger.info(f"Starting feature extraction: days={days}, batch_size={batch_size}, min_clicks={min_clicks}")

    # Phase A: Return placeholder
    # Phase C: Implement full database extraction
    logger.warning("populate_feature_vectors() is not yet implemented (Phase A placeholder)")
    logger.info("Full implementation will be added in Phase C when training data is available")

    return 0


# === Validation Utilities ===

def validate_feature_quality(days: int = 30) -> Dict[str, Any]:
    """
    Check data quality in feature_vectors table.

    Args:
        days: Number of days to analyze

    Returns:
        Dict with quality metrics:
        - total_rows: Total number of feature vectors
        - null_counts: Dict of null counts per feature
        - value_ranges: Dict of min/max per feature
        - click_rate: Overall click rate
        - avg_dwell_time: Average dwell time for clicked results

    Note: Placeholder for Phase A. Full implementation in Phase C.
    """
    logger.info(f"Validating feature quality for last {days} days")

    # Phase A: Return placeholder
    logger.warning("validate_feature_quality() is not yet implemented (Phase A placeholder)")

    return {
        'total_rows': 0,
        'null_counts': {},
        'value_ranges': {},
        'click_rate': 0.0,
        'avg_dwell_time': 0.0
    }


if __name__ == "__main__":
    # Test feature extraction functions
    print("Testing Feature Engineering Module")
    print("=" * 50)

    # Test query features
    query = "如何使用 XGBoost 進行排序？"
    query_features = extract_query_features(query)
    print(f"\nQuery: {query}")
    print(f"Query Features: {query_features}")

    # Test document features
    doc_features = extract_document_features(
        doc_title="XGBoost 機器學習入門指南",
        doc_description="這是一篇關於 XGBoost 的詳細教學文章，包含範例程式碼和實作技巧。",
        published_date="2025-01-20T10:00:00Z",
        author="John Doe",
        url="https://example.com/xgboost-guide"
    )
    print(f"\nDocument Features: {doc_features}")

    # Test query-doc features
    query_doc_features = extract_query_doc_features(
        query_text=query,
        doc_title="XGBoost 機器學習入門指南",
        doc_description="這是一篇關於 XGBoost 的詳細教學文章",
        bm25_score=150.5,
        vector_score=0.85,
        keyword_boost=10.0,
        temporal_boost=5.0,
        final_retrieval_score=165.5
    )
    print(f"\nQuery-Doc Features: {query_doc_features}")

    # Test ranking features
    ranking_features = extract_ranking_features(
        retrieval_position=5,
        ranking_position=2,
        llm_final_score=92.5,
        all_llm_scores=[95.0, 93.0, 92.5, 88.0, 85.0, 80.0]
    )
    print(f"\nRanking Features: {ranking_features}")

    # Test MMR features
    mmr_features = extract_mmr_features(
        mmr_diversity_score=0.75,
        detected_intent="EXPLORATORY"
    )
    print(f"\nMMR Features: {mmr_features}")

    # Count total features
    total_features = (
        len(query_features) +
        len(doc_features) +
        len(query_doc_features) +
        len(ranking_features) +
        len(mmr_features)
    )
    print(f"\n{'=' * 50}")
    print(f"Total Features Extracted: {total_features}")
    print(f"Expected: 29 (6 + 8 + 7 + 6 + 2)")
    print(f"Status: {'✓ PASS' if total_features == 29 else '✗ FAIL'}")
