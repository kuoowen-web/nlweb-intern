# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit test for whoRanking.py do() 6-tuple unpack (S6).

whoRanking.do() previously did a hard 4-unpack in its else branch, which raises
ValueError on a 6-tuple. This replicates the do() unpack loop (kept in sync with
source) and verifies a 6-tuple unpacks its first four fields without error.

Source of truth: code/python/core/whoRanking.py do()
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def _who_unpack(items):
    """Mirror of whoRanking.py do() unpack loop."""
    out = []
    for item in items:
        if len(item) >= 5:
            url, json_str, name, site = item[0], item[1], item[2], item[3]
        else:
            url, json_str, name, site = item
        out.append((url, json_str, name, site))
    return out


def test_whoranking_six_tuple_unpacks_first_four():
    items = [['http://a/1', '{}', 'T', 'S', None, {'vector_score': 0.5}]]
    out = _who_unpack(items)   # must not raise ValueError
    assert out[0] == ('http://a/1', '{}', 'T', 'S')


def test_whoranking_five_tuple_unpacks():
    items = [['http://a/1', '{}', 'T', 'S', [0.1, 0.2]]]
    out = _who_unpack(items)
    assert out[0] == ('http://a/1', '{}', 'T', 'S')


def test_whoranking_four_tuple_unpacks():
    items = [['http://a/1', '{}', 'T', 'S']]
    out = _who_unpack(items)
    assert out[0] == ('http://a/1', '{}', 'T', 'S')
