# Tests for retrieval low-relevance / low-keyword-match warning signals.
# These test the pure signal-computation helpers; the DB query itself is not exercised.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from retrieval_providers.postgres_client import (
    compute_low_relevance_warning,
    compute_low_keyword_match_warning,
    LOW_RELEVANCE_VECTOR_MAX,
    KEYWORD_HIT_MIN,
)


def _item(vector_score, text_score, keyword_hit=None):
    # Mirrors the real assembled row shape in postgres_client._search_docs:
    # every row carries a `keyword_hit` bool stamped from the pg_bigm text-hit
    # sets (chunk_id OR url). Default derives from text_score (a row with a real
    # text_score > 0 came from the text path, hence was a pg_bigm hit); pass
    # keyword_hit=True explicitly to model a vector-path row whose chunk/url was
    # ALSO keyword-hit (its text_score stays a placeholder 0.0).
    if keyword_hit is None:
        keyword_hit = text_score > 0.0
    return {'vector_score': vector_score, 'text_score': text_score,
            'keyword_hit': keyword_hit}


def test_signal_a_fires_when_max_vector_below_threshold():
    results = [_item(0.42, 0.0), _item(0.45, 0.0), _item(0.41, 0.0)]
    assert max(r['vector_score'] for r in results) < LOW_RELEVANCE_VECTOR_MAX
    assert compute_low_relevance_warning(results) is True


def test_signal_a_silent_when_a_strong_result_exists():
    results = [_item(0.41, 0.0), _item(0.80, 0.0), _item(0.42, 0.0)]
    assert compute_low_relevance_warning(results) is False


def test_signal_a_silent_on_empty():
    assert compute_low_relevance_warning([]) is False


def test_signal_a_silent_on_pure_text_path():
    # Regression guard for the false-positive bug: a strong keyword-only query like
    # "立法院" hits 50 docs purely via pg_bigm. Those rows have NO vector evidence, so
    # postgres_client fills vector_score with a placeholder 0.0 (not a DB-computed cosine).
    # Before the fix, max([0.0, 0.0, 0.0]) = 0.0 < 0.55 wrongly FIRED the low-relevance
    # warning. The fix excludes placeholder 0.0 scores and abstains when no vector hit exists.
    results = [_item(0.0, 0.14), _item(0.0, 0.10), _item(0.0, 0.08)]
    assert compute_low_relevance_warning(results) is False


def test_signal_a_fires_on_weak_vector_hits():
    # The fix must not over-suppress: genuine weak vector hits (real cosine 0.41/0.45,
    # both below 0.55) should still FIRE the low-relevance warning.
    results = [_item(0.41, 0.0), _item(0.45, 0.0)]
    assert compute_low_relevance_warning(results) is True


def test_signal_b_fires_when_few_keyword_hits():
    results = [_item(0.9, 0.3), _item(0.9, 0.1), _item(0.9, 0.05)] + [_item(0.9, 0.0)] * 5
    assert sum(1 for r in results if r['text_score'] > 0.0) == 3
    assert compute_low_keyword_match_warning(results) is True


def test_signal_b_silent_when_enough_keyword_hits():
    results = [_item(0.9, 0.2, keyword_hit=True)] * 10
    assert compute_low_keyword_match_warning(results) is False


def test_signal_b_silent_on_empty():
    assert compute_low_keyword_match_warning([]) is False


def test_signal_b_counts_vector_path_keyword_hits():
    # Regression guard for the mirror under-count bug (CDE plan Section D):
    # vector-path rows carry a PLACEHOLDER text_score of 0.0 (the pg_bigm query
    # never scored them — "not queried", not "no hit"), but the same chunk/URL
    # WAS matched by pg_bigm, so assembly stamps keyword_hit=True. The old logic
    # counted text_score > 0 only -> counted 0 of these 12 hits -> over-warned.
    # Fixed logic counts keyword_hit -> 12 >= KEYWORD_HIT_MIN -> silent.
    results = [_item(0.8, 0.0, keyword_hit=True)] * 12
    assert compute_low_keyword_match_warning(results) is False


def test_signal_b_still_fires_on_genuine_low_keyword():
    # The fix must not over-suppress: only 3 of 10 rows were genuinely
    # keyword-hit -> 3 < KEYWORD_HIT_MIN -> the warning still fires.
    results = ([_item(0.8, 0.0, keyword_hit=True)] * 3
               + [_item(0.8, 0.0, keyword_hit=False)] * 7)
    assert compute_low_keyword_match_warning(results) is True


def test_both_signals_can_fire_together():
    results = [_item(0.42, 0.3), _item(0.43, 0.0), _item(0.41, 0.0)]
    assert compute_low_relevance_warning(results) is True
    assert compute_low_keyword_match_warning(results) is True


def test_signals_do_not_mutate_raw_results():
    # The warning computation must be read-only: it must not change the result set
    # content or length (the "warn, never block" invariant).
    import copy
    results = [_item(0.42, 0.3), _item(0.43, 0.0), _item(0.41, 0.0)]
    snapshot = copy.deepcopy(results)
    compute_low_relevance_warning(results)
    compute_low_keyword_match_warning(results)
    assert results == snapshot
    assert len(results) == len(snapshot)


def test_low_relevance_sse_payload_verbatim():
    # The exact frontend-facing copy must never be altered.
    expected = "以下結果與您的搜尋可能關聯性較鬆，建議交叉參考其他來源"
    import os
    base = os.path.join(os.path.dirname(__file__), '..', 'core', 'baseHandler.py')
    with open(base, encoding='utf-8') as f:
        src = f.read()
    assert expected in src
    assert '"message_type": "low_relevance_warning"' in src


def test_low_keyword_sse_payload_verbatim():
    expected = "以下結果與關鍵字的字面吻合度較低，建議留意是否切合您的需求"
    import os
    base = os.path.join(os.path.dirname(__file__), '..', 'core', 'baseHandler.py')
    with open(base, encoding='utf-8') as f:
        src = f.read()
    assert expected in src
    assert '"message_type": "low_keyword_match_warning"' in src


EMPTY_RESULTS_COPY = "在目前的資料範圍中沒有找到相關內容。這個主題可能尚未被收錄，或不在本系統的新聞涵蓋範圍內。"


def _empty_notice_handler(author_no_results=False):
    # Minimal handler: skip the heavy __init__ (DB/config pipeline) — the fixture
    # cut is at "flags already set", only the emit helper is under test.
    import asyncio  # noqa: F401  (used by callers)
    from unittest.mock import AsyncMock
    from core.baseHandler import NLWebHandler
    h = object.__new__(NLWebHandler)
    h.author_search_no_results = author_no_results
    h.message_sender = AsyncMock()
    return h


def test_empty_results_notice_emitted_when_zero_items_non_author():
    import asyncio
    h = _empty_notice_handler(author_no_results=False)
    asyncio.run(h._maybe_emit_empty_results_notice([]))
    h.message_sender.send_message.assert_awaited_once()
    payload = h.message_sender.send_message.await_args.args[0]
    assert payload['message_type'] == 'empty_results'
    assert payload['content'] == EMPTY_RESULTS_COPY


def test_empty_results_notice_not_emitted_when_items_exist():
    import asyncio
    h = _empty_notice_handler(author_no_results=False)
    asyncio.run(h._maybe_emit_empty_results_notice([{'url': 'https://example.com/a'}]))
    h.message_sender.send_message.assert_not_awaited()


def test_empty_results_notice_suppressed_for_author_empty():
    # Author-search empty results keep the more specific author_search_no_results
    # copy (emitted by the existing author block in prepare()); the generic empty
    # notice must NOT double-fire on the same stream.
    import asyncio
    h = _empty_notice_handler(author_no_results=True)
    asyncio.run(h._maybe_emit_empty_results_notice([]))
    h.message_sender.send_message.assert_not_awaited()
    # Mutual-exclusion partner still present: the author emit block in baseHandler.
    import os
    base = os.path.join(os.path.dirname(__file__), '..', 'core', 'baseHandler.py')
    with open(base, encoding='utf-8') as f:
        src = f.read()
    assert '"message_type": "author_search_no_results"' in src


def test_per_loop_retrieval_does_not_pass_handler():
    # Guard: the LR per-loop retriever_search call must not pass handler= (would
    # spuriously fire low-relevance/low-keyword warnings mid-research). Source-level check.
    import os
    p = os.path.join(os.path.dirname(__file__), '..', 'reasoning', 'live_research', 'loop_engine.py')
    with open(p, encoding='utf-8') as f:
        src = f.read()
    # The per-loop call uses num_results=5; assert no handler= kwarg appears in that call block.
    assert 'num_results=5' in src
    idx = src.find('retriever_search(')
    assert idx != -1
    call_block = src[idx:idx + 400]
    assert 'handler=' not in call_block, "per-loop retriever_search must not pass handler="


def test_dr_gap_search_does_not_pass_handler():
    # Guard: the DR orchestrator gap (secondary) search must not pass handler= (would
    # re-trigger low-relevance/low-keyword warnings mid-research via the actor-critic
    # research() re-entry, since the flags are monotonic set-only). The warnings must
    # fire only on the initial research-start retrieval, never on web-augment gap search.
    # Source-level check, anchored on the gap-search marker (num_results=20).
    import os
    p = os.path.join(os.path.dirname(__file__), '..', 'reasoning', 'orchestrator.py')
    with open(p, encoding='utf-8') as f:
        src = f.read()
    marker = 'num_results=20,  # Smaller batch for gap search'
    assert marker in src, "gap-search marker not found; anchor may have drifted"
    # Locate the retriever_search( call that contains the gap-search marker.
    m_idx = src.index(marker)
    call_start = src.rfind('retriever_search(', 0, m_idx)
    assert call_start != -1, "could not locate gap-search retriever_search( call"
    call_end = src.index(')', m_idx)
    call_block = src[call_start:call_end + 1]
    assert 'handler=' not in call_block, "DR gap-search retriever_search must not pass handler="
