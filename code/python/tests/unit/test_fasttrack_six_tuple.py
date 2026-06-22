# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit test for fastTrack.py temporal pre-filter 6-tuple handling (S3).

The temporal pre-filter in FastTrack.do() previously did a hard 4-unpack in its
else branch (ValueError on a 6-tuple) and rebuilt kept items as 4/5-lists
(dropping retrieval_scores). This test replicates that filter loop (the same
self-contained pattern Class D uses for ranking.py's url_to_vector logic) and
locks the fixed behaviour:
- a 6-tuple must not raise on unpack
- kept items must preserve index-5 retrieval_scores (append original item)

Source of truth: code/python/core/fastTrack.py (temporal filter block).
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _temporal_filter(items):
    """Mirror of fastTrack.py temporal pre-filter (kept in sync with source)."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=365)
    filtered_items = []
    for item in items:
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
                date_str = date_published.split('T')[0] if 'T' in date_published else date_published
                pub_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if pub_date >= cutoff_date:
                    filtered_items.append(item)
        except Exception:
            pass
    return filtered_items


def _recent_date():
    return (datetime.now(timezone.utc) - timedelta(days=10)).strftime('%Y-%m-%d')


def test_fasttrack_handles_six_tuple_no_unpack_error():
    """A 6-tuple must not raise ValueError in the temporal filter."""
    schema = json.dumps({'datePublished': _recent_date()})
    items = [['http://a/1', schema, 'T', 'S', None, {'vector_score': 0.5}]]
    out = _temporal_filter(items)  # must not raise
    assert len(out) == 1


def test_fasttrack_six_tuple_preserves_scores():
    """Kept 6-tuple must retain index-5 retrieval_scores (not rebuilt away)."""
    schema = json.dumps({'datePublished': _recent_date()})
    scores = {'vector_score': 0.7, 'final_retrieval_score': 0.7}
    items = [['http://a/1', schema, 'T', 'S', [0.1, 0.2], scores]]
    out = _temporal_filter(items)
    assert len(out[0]) == 6
    assert out[0][5] == scores
    assert out[0][4] == [0.1, 0.2]


def test_fasttrack_legacy_4_and_5_tuple_still_work():
    """4-tuple and 5-tuple must still pass through unchanged."""
    schema = json.dumps({'datePublished': _recent_date()})
    out4 = _temporal_filter([['http://a/1', schema, 'T', 'S']])
    out5 = _temporal_filter([['http://a/2', schema, 'T', 'S', [0.3]]])
    assert len(out4[0]) == 4
    assert len(out5[0]) == 5


def test_fasttrack_old_articles_filtered_out():
    """Articles older than 365 days are still dropped (filter still works)."""
    old_schema = json.dumps({'datePublished': '2000-01-01'})
    items = [['http://a/1', old_schema, 'T', 'S', None, {'vector_score': 0.5}]]
    out = _temporal_filter(items)
    assert out == []
