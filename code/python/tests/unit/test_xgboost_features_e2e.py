# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Mock end-to-end regression test for the retrieval_scores -> XGBoost feature path.

This locks the fix: when ranking results carry a non-empty retrieval_scores dict
(as they now do once the aggregator preserves the 6-tuple index-5 scores), the
XGBoost feature vector's retrieval features (index 14-18) are non-zero — instead
of the all-zero values that polluted shadow training data.

Feature vector layout (verified xgboost_ranker.py:204-244):
  Query (6, idx 0-5) | Document (8, idx 6-13) | Retrieval (7, idx 14-20) | ...
  Retrieval: 14=vector_similarity, 15=bm25, 16=keyword_boost, 17=temporal_boost,
             18=final_retrieval_score, 19=keyword_overlap_ratio, 20=title_exact_match
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.xgboost_ranker import XGBoostRanker


def _make_ranker():
    # enabled=False -> __init__ does not load a model; extract_features needs none.
    return XGBoostRanker({'enabled': False})


def test_xgboost_retrieval_features_nonzero_from_scores():
    """Non-empty retrieval_scores -> retrieval feature vector non-zero."""
    ranking_results = [{
        'url': 'http://a/1',
        'name': 'Taiwan economy report',
        'schema_object': {'description': 'an economy article'},
        'ranking': {'score': 80},
        'retrieval_scores': {
            'vector_score': 0.9, 'bm25_score': 0.3, 'keyword_boost': 0.1,
            'temporal_boost': 0.2, 'final_retrieval_score': 0.85,
        },
    }]
    feats = _make_ranker().extract_features(ranking_results, 'taiwan economy')
    assert feats[0][14] == 0.9    # vector_similarity
    assert feats[0][15] == 0.3    # bm25_score
    assert feats[0][16] == 0.1    # keyword_boost
    assert feats[0][17] == 0.2    # temporal_boost
    assert feats[0][18] == 0.85   # final_retrieval_score


def test_xgboost_gate_features_14_and_18_nonzero():
    """Gate (per CEO): with realistic postgres scores, index 14 and 18 are the
    two features required to be non-zero (keyword_boost/temporal_boost stay 0.0
    by postgres design)."""
    ranking_results = [{
        'url': 'http://a/1', 'name': 'T',
        'schema_object': {'description': 'd'}, 'ranking': {'score': 75},
        'retrieval_scores': {
            'vector_score': 0.72, 'bm25_score': 0.41, 'keyword_boost': 0.0,
            'temporal_boost': 0.0, 'final_retrieval_score': 0.72,
        },
    }]
    feats = _make_ranker().extract_features(ranking_results, 'q')
    assert feats[0][14] != 0.0    # vector_similarity (gate)
    assert feats[0][18] != 0.0    # final_retrieval_score (gate)


def test_xgboost_features_zero_when_scores_empty():
    """Empty scores -> retrieval features 0 (locks the original bug scenario)."""
    ranking_results = [{
        'url': 'http://a/1', 'name': 'T', 'schema_object': {},
        'ranking': {'score': 80}, 'retrieval_scores': {},
    }]
    feats = _make_ranker().extract_features(ranking_results, 'q')
    assert feats[0][14] == 0.0    # this is the bug: empty scores -> zero feature
    assert feats[0][18] == 0.0
