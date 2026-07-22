# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit test for ranking.py sendMessageOnSitesBeingAsked 6-tuple handling (S8).

sendMessageOnSitesBeingAsked counted sites by unpacking each item; its else
branch did a hard 4-unpack which raises ValueError on a 6-tuple. This replicates
the site-extraction loop (kept in sync with source) and verifies a 6-tuple's
site is read from index 3 without error, including the dict format.

Source of truth: code/python/core/ranking.py sendMessageOnSitesBeingAsked
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


def _count_sites(top_embeddings):
    """Mirror of sendMessageOnSitesBeingAsked site-extraction loop."""
    sites_in_embeddings = {}
    for item in top_embeddings:
        if isinstance(item, dict):
            site = item.get('site', '')
        elif len(item) >= 4:
            site = item[3]
        else:
            url, json_str, name, site = item
        sites_in_embeddings[site] = sites_in_embeddings.get(site, 0) + 1
    return sites_in_embeddings


def test_s8_six_tuple_reads_site_no_unpack_error():
    items = [['http://a/1', '{}', 'T', 'siteA', None, {'vector_score': 0.5}]]
    out = _count_sites(items)   # must not raise ValueError
    assert out == {'siteA': 1}


def test_s8_mixed_tuple_lengths_count_correctly():
    items = [
        ['http://a/1', '{}', 'T', 'siteA'],                       # 4-tuple
        ['http://a/2', '{}', 'T', 'siteA', [0.1]],                # 5-tuple
        ['http://a/3', '{}', 'T', 'siteB', None, {'v': 0.5}],     # 6-tuple
    ]
    out = _count_sites(items)
    assert out == {'siteA': 2, 'siteB': 1}


def test_s8_dict_format_reads_site():
    items = [{'url': 'http://a/1', 'site': 'siteC'}]
    out = _count_sites(items)
    assert out == {'siteC': 1}
