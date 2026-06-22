# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit tests for Retriever._aggregate_results retrieval_scores preservation.

These tests lock the AGGREGATOR_KEEP_SCORES behaviour:
- aggregator emits a fixed 6-tuple [url, json, name, site, vector_or_None,
  retrieval_scores] so downstream XGBoost shadow ranker features (index 14-18)
  are no longer all-zero.
- dict input (Qdrant-style) and 6-tuple input (postgres-style) both preserve
  scores to index 5.
- same-url multi-endpoint scores merge per-key with max() (not first-occurrence).
"""

import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.retriever import VectorDBClient


def _make_retriever():
    # Bypass __init__ — _aggregate_results needs no DB connection / config.
    return object.__new__(VectorDBClient)


@contextmanager
def _flag(value):
    """Set AGGREGATOR_KEEP_SCORES, restoring the prior value afterwards.

    Restores (not unconditionally pops) so this does not clobber a globally-set
    flag when the whole suite is run with AGGREGATOR_KEEP_SCORES=1 under random
    test ordering.
    """
    prev = os.environ.get('AGGREGATOR_KEEP_SCORES')
    if value is None:
        os.environ.pop('AGGREGATOR_KEEP_SCORES', None)
    else:
        os.environ['AGGREGATOR_KEEP_SCORES'] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop('AGGREGATOR_KEEP_SCORES', None)
        else:
            os.environ['AGGREGATOR_KEEP_SCORES'] = prev


def test_aggregate_preserves_scores_from_dict_input():
    """dict input (Qdrant-style) must preserve retrieval_scores to index 5."""
    r = _make_retriever()
    item = {
        'url': 'http://a/1',
        'schema_json': '{"description":"d"}',
        'title': 'T',
        'site': 'S',
        'retrieval_scores': {
            'vector_score': 0.9, 'bm25_score': 0.3, 'keyword_boost': 0.0,
            'temporal_boost': 0.0, 'final_retrieval_score': 0.9,
        },
    }
    with _flag('1'):
        out = r._aggregate_results({'ep1': [item]})
    assert len(out) == 1
    assert len(out[0]) == 6                      # fixed 6-tuple
    assert isinstance(out[0][5], dict)           # index 5 = scores
    assert out[0][5]['vector_score'] == 0.9
    assert out[0][0] == 'http://a/1'             # index 0 = url


def test_aggregate_preserves_scores_from_six_tuple_input():
    """6-tuple input (postgres-style): vector at index 4, scores at index 5."""
    r = _make_retriever()
    item = ['http://a/1', '{"description":"d"}', 'T', 'S', None,
            {'vector_score': 0.8, 'bm25_score': 0.2, 'keyword_boost': 0.0,
             'temporal_boost': 0.0, 'final_retrieval_score': 0.8}]
    with _flag('1'):
        out = r._aggregate_results({'ep1': [item]})
    assert len(out[0]) == 6
    assert out[0][5]['vector_score'] == 0.8


def test_aggregate_six_tuple_with_vector_keeps_vector_at_index_4():
    """Blocker A: a 6-tuple WITH a vector must keep the vector at index 4."""
    r = _make_retriever()
    vec = [0.1, 0.2, 0.3, 0.4]
    item = ['http://a/1', '{}', 'T', 'S', vec,
            {'vector_score': 0.7, 'bm25_score': 0.1, 'keyword_boost': 0.0,
             'temporal_boost': 0.0, 'final_retrieval_score': 0.7}]
    with _flag('1'):
        out = r._aggregate_results({'ep1': [item]})
    assert len(out[0]) == 6
    assert out[0][4] == vec                       # vector NOT mis-set to None
    assert out[0][5]['vector_score'] == 0.7


def test_aggregate_multisource_scores_take_max():
    """Same url across endpoints: scores merge per-key with max (not first)."""
    r = _make_retriever()
    s1 = {'vector_score': 0.5, 'bm25_score': 0.9, 'keyword_boost': 0.0,
          'temporal_boost': 0.0, 'final_retrieval_score': 0.5}
    s2 = {'vector_score': 0.7, 'bm25_score': 0.1, 'keyword_boost': 0.0,
          'temporal_boost': 0.0, 'final_retrieval_score': 0.7}
    i1 = ['http://a/1', '{}', 'T', 'S', None, s1]
    i2 = ['http://a/1', '{}', 'T', 'S', None, s2]
    with _flag('1'):
        out = r._aggregate_results({'ep1': [i1], 'ep2': [i2]})
    assert len(out) == 1
    assert out[0][5]['vector_score'] == 0.7       # max(0.5, 0.7)
    assert out[0][5]['bm25_score'] == 0.9         # max(0.9, 0.1)


def test_aggregate_flag_off_emits_legacy_4_or_5_tuple():
    """Flag off: emit 4/5-tuple exactly as before — no scores appended."""
    r = _make_retriever()
    with _flag(None):  # explicitly off
        # 4-tuple input, no vector
        out4 = r._aggregate_results({'ep1': [['http://a/1', '{}', 'T', 'S']]})
        # 5-tuple input, with vector
        out5 = r._aggregate_results({'ep1': [['http://a/2', '{}', 'T', 'S', [0.1, 0.2]]]})
    assert len(out4[0]) == 4
    assert len(out5[0]) == 5
    assert out5[0][4] == [0.1, 0.2]
