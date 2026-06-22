# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unit tests for ranking.Ranking.rankItem 6-tuple retrieval_scores handling (S1/S2).

S1 (core read point): rankItem must read retrieval_scores from a 6-tuple's
index 5 and carry it into ansr['retrieval_scores'] (so XGBoost gets non-zero
retrieval features). A 6-tuple must NOT raise ValueError (the old else branch
did a hard 4-unpack).
"""

import os
import sys
import asyncio
import threading
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import core.ranking as ranking_mod
from core.ranking import Ranking


class _State:
    def should_abort_fast_track(self):
        return False


class _Handler:
    def __init__(self):
        self.state = _State()
        self.required_item_type = None
        self.generate_mode = 'list'  # not 'unified', avoids batch send path
        self.query_params = {}
        self.connection_alive_event = threading.Event()
        self.connection_alive_event.set()
        # No query_id attribute -> analytics logging block skipped via hasattr.


def _make_ranking():
    r = object.__new__(Ranking)
    r.ranking_type = Ranking.REGULAR_TRACK
    r.ranking_type_str = "REGULAR_TRACK"
    r.handler = _Handler()
    r.level = "low"
    r.EARLY_SEND_THRESHOLD = 200  # above any score -> no early send path
    r.NUM_RESULTS_TO_SEND = 10
    r.num_results_sent = 0
    r.rankedAnswers = []
    return r


async def _fake_ask_llm(prompt, ans_struc, level=None, query_params=None):
    return {"score": 80, "description": "ok", "final_score": 80}


def _run_rank_item(item):
    r = _make_ranking()
    with patch.object(ranking_mod, 'ask_llm', _fake_ask_llm), \
         patch.object(Ranking, 'get_ranking_prompt',
                      lambda self: ("prompt", {})):
        return asyncio.run(r.rankItem(item))


def test_rankitem_six_tuple_preserves_scores_to_ansr():
    """6-tuple: retrieval_scores (index 5) must reach ansr['retrieval_scores']."""
    scores = {'vector_score': 0.9, 'bm25_score': 0.3, 'keyword_boost': 0.0,
              'temporal_boost': 0.0, 'final_retrieval_score': 0.9}
    item = ['http://a/1', '{"description":"d"}', 'T', 'S', None, scores]
    ansr = _run_rank_item(item)
    assert ansr is not None
    assert ansr['retrieval_scores']['vector_score'] == 0.9
    assert ansr['retrieval_scores']['final_retrieval_score'] == 0.9


def test_rankitem_six_tuple_with_vector_no_unpack_error():
    """6-tuple with a vector must not raise and must carry vector + scores."""
    item = ['http://a/1', '{}', 'T', 'S', [0.1, 0.2],
            {'vector_score': 0.5, 'bm25_score': 0.0, 'keyword_boost': 0.0,
             'temporal_boost': 0.0, 'final_retrieval_score': 0.5}]
    ansr = _run_rank_item(item)
    assert ansr is not None
    assert ansr['vector'] == [0.1, 0.2]
    assert ansr['retrieval_scores']['vector_score'] == 0.5


def test_rankitem_five_tuple_still_empty_scores():
    """Legacy 5-tuple: no scores -> empty dict (backward compat)."""
    item = ['http://a/1', '{}', 'T', 'S', [0.1, 0.2]]
    ansr = _run_rank_item(item)
    assert ansr is not None
    assert ansr['retrieval_scores'] == {}


def test_rankitem_four_tuple_still_empty_scores():
    """Legacy 4-tuple: no scores, no vector (backward compat)."""
    item = ['http://a/1', '{}', 'T', 'S']
    ansr = _run_rank_item(item)
    assert ansr is not None
    assert ansr['retrieval_scores'] == {}
    assert 'vector' not in ansr
