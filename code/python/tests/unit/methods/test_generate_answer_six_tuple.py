# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit tests for generate_answer.py 6-tuple handling (S4 + S5 + S9).

generate_answer.py had three points that break / degrade on a 6-tuple:
- S9: temporal pre-filter — else hard 4-unpack (ValueError) + rebuild drops scores
- S4: rank loop — else hard 4-unpack (ValueError)
- S5: URL-match unpack `(url, json_str, name, site) = item` — ValueError

S4/S9 logic is inline in a large async method; these tests replicate those
loops (kept in sync with source) plus verify the S5 unpack form directly.

Source of truth: code/python/methods/generate_answer.py
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()


def _s9_temporal_filter(top_embeddings):
    """Mirror of generate_answer.py temporal pre-filter (S9)."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=365)
    filtered = []
    for item in top_embeddings:
        if len(item) >= 6:
            url, json_str = item[0], item[1]
        elif len(item) == 5:
            url, json_str, name, site, vector = item
        else:
            url, json_str, name, site = item
            vector = None
        try:
            schema_obj = json.loads(json_str)
            date_published = schema_obj.get('datePublished', 'Unknown')
            if date_published != 'Unknown':
                pub_date = datetime.fromisoformat(date_published.replace('Z', '+00:00'))
                if pub_date >= cutoff_date:
                    filtered.append(item)
        except Exception:
            pass
    return filtered


def _s4_rank_loop_unpack(top_embeddings):
    """Mirror of generate_answer.py rank-loop unpack (S4)."""
    out = []
    for item in top_embeddings:
        if len(item) >= 5:
            url, json_str, name, site = item[0], item[1], item[2], item[3]
        else:
            url, json_str, name, site = item
        out.append((url, json_str, name, site))
    return out


# --- S9 temporal filter ----------------------------------------------------

def test_s9_six_tuple_no_unpack_error_and_keeps_scores():
    schema = json.dumps({'datePublished': _recent_iso()})
    scores = {'vector_score': 0.6, 'final_retrieval_score': 0.6}
    items = [['http://a/1', schema, 'T', 'S', [0.1], scores]]
    out = _s9_temporal_filter(items)
    assert len(out[0]) == 6
    assert out[0][5] == scores       # scores preserved (not rebuilt away)


def test_s9_legacy_tuples_still_work():
    schema = json.dumps({'datePublished': _recent_iso()})
    out4 = _s9_temporal_filter([['http://a/1', schema, 'T', 'S']])
    out5 = _s9_temporal_filter([['http://a/2', schema, 'T', 'S', [0.2]]])
    assert len(out4[0]) == 4 and len(out5[0]) == 5


# --- S4 rank loop ----------------------------------------------------------

def test_s4_six_tuple_unpacks_first_four():
    items = [['http://a/1', '{}', 'T', 'S', None, {'vector_score': 0.5}]]
    out = _s4_rank_loop_unpack(items)   # must not raise
    assert out[0] == ('http://a/1', '{}', 'T', 'S')


def test_s4_four_and_five_tuple():
    out = _s4_rank_loop_unpack([['u', '{}', 'T', 'S'],
                                ['u2', '{}', 'T2', 'S2', [0.1]]])
    assert out[0] == ('u', '{}', 'T', 'S')
    assert out[1] == ('u2', '{}', 'T2', 'S2')


# --- S5 URL-match unpack ---------------------------------------------------

def test_s5_six_tuple_unpack_via_slice():
    item = ['http://a/1', '{}', 'T', 'S', None, {'vector_score': 0.5}]
    (url, json_str, name, site) = item[:4]   # the :879 fixed form
    assert url == 'http://a/1'
    assert site == 'S'


def test_s5_four_tuple_unpack_via_slice():
    item = ['http://a/1', '{}', 'T', 'S']
    (url, json_str, name, site) = item[:4]
    assert name == 'T'
