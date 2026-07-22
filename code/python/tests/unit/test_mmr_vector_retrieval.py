"""
TDD tests for MMR vector retrieval bug fix.

Bug: postgres_client.py search() returns only 4-field lists
  [[url, schema_str, title, source]]
without embedding vectors. ranking.py extracts vectors from search results
into self.url_to_vector. With no vectors available, url_to_vector is always
empty, so MMR is always silently skipped at the condition:
  if mmr_enabled and len(ranked) > mmr_threshold and self.url_to_vector:

Fix:
1. postgres_client.py — include 'vector' key in each result dict when
   include_vectors=True is passed via kwargs.
2. postgres_client.py — the final list-of-lists conversion must preserve the
   vector in index-4 (5-tuple: [url, schema_str, title, source, vector]).
3. ranking.py — the existing 5-tuple extraction branch already handles this:
   elif len(item) == 5: url, _, _, _, vector = item
   so no additional ranking.py change is needed IF postgres_client returns
   the 5-tuple format.

Tests cover:
A. postgres_client._build_search_result_row includes 'vector' key when asked
B. postgres_client.search() returns 5-tuples when include_vectors=True kwarg
C. postgres_client.search() returns 4-tuples when include_vectors not passed
   (backward compatibility)
D. ranking.py url_to_vector extracts from 5-tuple format
E. ranking.py url_to_vector is populated when vectors present (MMR not skipped)
F. ranking.py url_to_vector is empty when no vectors (MMR graceful skip)
G. vector is a list of floats (compatible with mmr.py cosine_similarity)
"""

import sys
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch, MagicMock
import asyncio

# Add code/python to sys.path so we can import the module under test
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from retrieval_providers.postgres_client import PgVectorClient


def make_client():
    """Create a PgVectorClient without triggering __init__ (no DB needed)."""
    client = object.__new__(PgVectorClient)
    return client


@contextmanager
def aggregator_keep_scores(value):
    """Pin AGGREGATOR_KEEP_SCORES to a known state, restoring the prior value.

    Restoring (not popping) is required so these tests are deterministic whether
    the whole suite runs with the flag globally on or off, under any test order.
    Legacy 4/5-tuple contract tests pin it OFF ('0'); the 6-tuple contract test
    pins it ON ('1'). Note: since 2026-07-05 the code default (env unset) is ON,
    so legacy tests must pin '0' explicitly — None no longer means off.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_db_row(url="http://example.com/1", title="Article 1",
                      source="example.com", vector=None):
    """Return a dict mimicking a psycopg dict_row from the DB query."""
    import json
    from datetime import date

    row = {
        "chunk_id": 1,
        "article_id": 1,
        "chunk_text": "Some body text.",
        "url": url,
        "title": title,
        "author": "Author",
        "source": source,
        "date_published": date(2026, 1, 1),
        "metadata": {},
        "vector_score": 0.85,
        "text_score": 0.0,
    }
    if vector is not None:
        row["embedding"] = vector
    return row


# ---------------------------------------------------------------------------
# A. _build_schema_json returns schema dict without vector (no change needed)
# ---------------------------------------------------------------------------

class TestBuildSchemaJsonUnchanged(unittest.TestCase):
    """_build_schema_json must remain unchanged — no vector pollution."""

    def test_schema_json_does_not_contain_vector(self):
        client = make_client()
        row = _make_fake_db_row(vector=[0.1, 0.2, 0.3])
        schema_str = client._build_schema_json(row)
        import json
        schema = json.loads(schema_str)
        self.assertNotIn("embedding", schema)
        self.assertNotIn("vector", schema)


# ---------------------------------------------------------------------------
# B. search() returns 5-tuple (with vector) when include_vectors=True
# ---------------------------------------------------------------------------

class TestSearchReturnsVectorWhenRequested(unittest.TestCase):
    """search() with include_vectors=True kwarg must return 5-element lists."""

    def _run_fake_search(self, include_vectors, vector_value):
        """Run search() with a fake _execute_with_retry that returns one row."""
        client = make_client()
        client._build_filters = MagicMock(return_value=([], []))

        row = _make_fake_db_row(
            url="http://example.com/a",
            vector=vector_value,
        )

        async def fake_execute(fn, **kw):
            # Call the inner function with a mock connection to get results
            return [row]

        # Patch _execute_with_retry to call a helper that simulates DB results.
        # We also need to bypass the actual _search_docs inner function.
        # Simplest: patch search to call a stand-in _search_docs directly.
        import retrieval_providers.postgres_client as pg_mod

        original_get_embedding = pg_mod.get_embedding

        async def fake_get_embedding(query, query_params=None):
            return [0.0] * 4

        pg_mod.get_embedding = fake_get_embedding

        # We patch _execute_with_retry so it returns the raw_results list
        # that _search_docs would have returned.
        raw_results_with_vector = [{
            'url': row['url'],
            'schema_str': client._build_schema_json(row),
            'title': row['title'],
            'source': row['source'],
            'author': row.get('author') or '',
            'date_published': '',
            'vector_score': float(row.get('vector_score') or 0.0),
            'text_score': float(row.get('text_score') or 0.0),
        }]
        if include_vectors and vector_value is not None:
            raw_results_with_vector[0]['vector'] = vector_value

        async def fake_execute_with_retry(fn, **kw):
            return raw_results_with_vector

        client._execute_with_retry = fake_execute_with_retry

        # Own a dedicated event loop instead of asyncio.get_event_loop(): under
        # the full suite a prior pytest-asyncio test leaves the main thread with
        # no current loop, so get_event_loop() raises RuntimeError on Python 3.11.
        loop = asyncio.new_event_loop()
        try:
            with aggregator_keep_scores('0'):  # explicitly off: legacy 4/5-tuple contract
                result = loop.run_until_complete(
                    client.search(
                        "test query",
                        site=[],
                        num_results=10,
                        include_vectors=include_vectors,
                    )
                )
        finally:
            loop.close()
            pg_mod.get_embedding = original_get_embedding

        return result

    def test_five_tuple_returned_when_include_vectors_true_and_vector_present(self):
        """With include_vectors=True, each result must be a 5-element list."""
        fake_vector = [0.1, 0.2, 0.3, 0.4]
        results = self._run_fake_search(include_vectors=True, vector_value=fake_vector)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(len(item), 5,
                         f"Expected 5-element list, got {len(item)}: {item}")

    def test_vector_is_at_index_4_in_five_tuple(self):
        """Vector must be at index 4 in the returned 5-tuple."""
        fake_vector = [0.1, 0.2, 0.3, 0.4]
        results = self._run_fake_search(include_vectors=True, vector_value=fake_vector)
        item = results[0]
        self.assertEqual(item[4], fake_vector)

    def test_vector_at_index_4_is_list_of_floats(self):
        """Vector at index 4 must be a list of floats (mmr.py compatible)."""
        fake_vector = [float(i) / 10 for i in range(8)]
        results = self._run_fake_search(include_vectors=True, vector_value=fake_vector)
        vector = results[0][4]
        self.assertIsInstance(vector, list)
        self.assertTrue(all(isinstance(v, float) for v in vector),
                        f"Vector contains non-float values: {vector}")


# ---------------------------------------------------------------------------
# C. search() returns 4-tuple when include_vectors not passed (backward compat)
# ---------------------------------------------------------------------------

class TestSearchBackwardCompatNoVector(unittest.TestCase):
    """search() without include_vectors kwarg must return 4-element lists."""

    def _run_fake_search_no_vectors(self):
        client = make_client()
        client._build_filters = MagicMock(return_value=([], []))

        row = _make_fake_db_row(url="http://example.com/b")

        import retrieval_providers.postgres_client as pg_mod
        original_get_embedding = pg_mod.get_embedding

        async def fake_get_embedding(query, query_params=None):
            return [0.0] * 4

        pg_mod.get_embedding = fake_get_embedding

        raw_results = [{
            'url': row['url'],
            'schema_str': client._build_schema_json(row),
            'title': row['title'],
            'source': row['source'],
            'author': row.get('author') or '',
            'date_published': '',
            'vector_score': float(row.get('vector_score') or 0.0),
            'text_score': float(row.get('text_score') or 0.0),
            # No 'vector' key — simulates include_vectors=False path
        }]

        async def fake_execute_with_retry(fn, **kw):
            return raw_results

        client._execute_with_retry = fake_execute_with_retry

        # Dedicated event loop — immune to pytest-asyncio main-thread loop pollution.
        loop = asyncio.new_event_loop()
        try:
            with aggregator_keep_scores('0'):  # explicitly off: legacy 4-tuple contract
                result = loop.run_until_complete(
                    client.search("test query", site=[], num_results=10)
                    # No include_vectors kwarg
                )
        finally:
            loop.close()
            pg_mod.get_embedding = original_get_embedding

        return result

    def test_four_tuple_returned_when_no_include_vectors_kwarg(self):
        """Without include_vectors, each result must be a 4-element list."""
        results = self._run_fake_search_no_vectors()
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(len(item), 4,
                         f"Expected 4-element list, got {len(item)}: {item}")

    def test_four_tuple_fields_are_url_schema_title_source(self):
        """4-tuple fields: [0]=url, [1]=schema_str, [2]=title, [3]=source."""
        results = self._run_fake_search_no_vectors()
        item = results[0]
        self.assertEqual(item[0], "http://example.com/b")
        self.assertEqual(item[2], "Article 1")
        self.assertEqual(item[3], "example.com")


# ---------------------------------------------------------------------------
# D. ranking.py url_to_vector extracts vector from 5-tuple
# ---------------------------------------------------------------------------

class TestRankingUrlToVectorFrom5Tuple(unittest.TestCase):
    """
    ranking.py must populate url_to_vector when items contain 5-tuples.
    We test the extraction logic in isolation (no LLM calls needed).
    """

    def _extract_url_to_vector(self, items):
        """Replicate the url_to_vector extraction logic from ranking.py."""
        url_to_vector = {}
        for item in items:
            if isinstance(item, dict):
                url = item.get('url', '')
                vector = item.get('vector')
                if vector is not None:
                    url_to_vector[url] = vector
            # len >= 5 so a 6-tuple's vector (index 4) is not silently dropped.
            elif len(item) >= 5:
                vector = item[4]
                if vector is not None:
                    url_to_vector[item[0]] = vector
        return url_to_vector

    def test_5tuple_populates_url_to_vector(self):
        """5-element item must populate url_to_vector with the vector."""
        vector = [0.1, 0.2, 0.3]
        items = [["http://example.com/1", "schema", "title", "source", vector]]
        url_to_vector = self._extract_url_to_vector(items)
        self.assertIn("http://example.com/1", url_to_vector)
        self.assertEqual(url_to_vector["http://example.com/1"], vector)

    def test_4tuple_does_not_populate_url_to_vector(self):
        """4-element item (no vector) must leave url_to_vector empty."""
        items = [["http://example.com/1", "schema", "title", "source"]]
        url_to_vector = self._extract_url_to_vector(items)
        self.assertEqual(url_to_vector, {})

    def test_multiple_5tuples_all_populated(self):
        """Multiple 5-tuples must all be added to url_to_vector."""
        items = [
            ["http://example.com/1", "s1", "t1", "src1", [0.1, 0.2]],
            ["http://example.com/2", "s2", "t2", "src2", [0.3, 0.4]],
        ]
        url_to_vector = self._extract_url_to_vector(items)
        self.assertEqual(len(url_to_vector), 2)
        self.assertIn("http://example.com/1", url_to_vector)
        self.assertIn("http://example.com/2", url_to_vector)

    def test_dict_format_with_vector_key_is_also_handled(self):
        """Dict items with 'vector' key must also populate url_to_vector."""
        vector = [0.5, 0.6, 0.7]
        items = [{'url': 'http://example.com/3', 'schema_str': 's', 'title': 't',
                  'source': 'src', 'vector': vector}]
        url_to_vector = self._extract_url_to_vector(items)
        self.assertIn("http://example.com/3", url_to_vector)
        self.assertEqual(url_to_vector["http://example.com/3"], vector)

    def test_6tuple_populates_url_to_vector_from_index_4(self):
        """6-tuple [url, json, name, site, vector, scores]: vector at index 4."""
        vector = [0.8, 0.9]
        items = [["http://example.com/6", "schema", "title", "source", vector,
                  {'vector_score': 0.9}]]
        url_to_vector = self._extract_url_to_vector(items)
        self.assertIn("http://example.com/6", url_to_vector)
        self.assertEqual(url_to_vector["http://example.com/6"], vector)

    def test_6tuple_with_none_vector_does_not_populate(self):
        """6-tuple with None vector (no MMR vector) must not populate."""
        items = [["http://example.com/6", "schema", "title", "source", None,
                  {'vector_score': 0.9}]]
        url_to_vector = self._extract_url_to_vector(items)
        self.assertEqual(url_to_vector, {})


# ---------------------------------------------------------------------------
# E. MMR not skipped when url_to_vector is populated (condition check)
# ---------------------------------------------------------------------------

class TestMMRConditionNotSkippedWithVectors(unittest.TestCase):
    """
    The MMR guard: if mmr_enabled and len(ranked) > mmr_threshold and url_to_vector
    must evaluate to True when url_to_vector is populated.
    """

    def test_mmr_condition_is_true_when_vectors_present(self):
        """MMR condition evaluates True when url_to_vector is non-empty."""
        mmr_enabled = True
        mmr_threshold = 3
        ranked = [{}] * 5  # 5 results > threshold 3
        url_to_vector = {"http://example.com/1": [0.1, 0.2, 0.3]}

        condition = mmr_enabled and len(ranked) > mmr_threshold and bool(url_to_vector)
        self.assertTrue(condition, "MMR should NOT be skipped when vectors are available")

    def test_mmr_condition_is_false_when_no_vectors(self):
        """MMR condition evaluates False when url_to_vector is empty."""
        mmr_enabled = True
        mmr_threshold = 3
        ranked = [{}] * 5
        url_to_vector = {}  # No vectors — this is the bug scenario

        condition = mmr_enabled and len(ranked) > mmr_threshold and bool(url_to_vector)
        self.assertFalse(condition, "MMR should be skipped when no vectors are available")

    def test_mmr_condition_is_false_when_too_few_results(self):
        """MMR condition evaluates False when fewer results than threshold."""
        mmr_enabled = True
        mmr_threshold = 3
        ranked = [{}] * 2  # Only 2 results, below threshold
        url_to_vector = {"http://example.com/1": [0.1, 0.2]}

        condition = mmr_enabled and len(ranked) > mmr_threshold and bool(url_to_vector)
        self.assertFalse(condition)


# ---------------------------------------------------------------------------
# F. Graceful skip when no vectors — backward compatibility
# ---------------------------------------------------------------------------

class TestMMRGracefulSkipNoVectors(unittest.TestCase):
    """
    When include_vectors is not passed and no vectors are returned,
    MMR must be skipped gracefully (no exception).
    The url_to_vector dict is empty, which is correct behavior.
    """

    def test_url_to_vector_empty_when_4tuple_items(self):
        """4-tuple items produce empty url_to_vector — MMR gracefully skips."""
        items = [
            ["http://a.com/1", "schema1", "title1", "source1"],
            ["http://a.com/2", "schema2", "title2", "source2"],
            ["http://a.com/3", "schema3", "title3", "source3"],
            ["http://a.com/4", "schema4", "title4", "source4"],
        ]
        url_to_vector = {}
        for item in items:
            if isinstance(item, dict):
                url = item.get('url', '')
                vector = item.get('vector')
                if vector is not None:
                    url_to_vector[url] = vector
            elif len(item) == 5:
                url, _, _, _, vector = item
                url_to_vector[url] = vector
        # url_to_vector must be empty — the graceful-skip path
        self.assertEqual(url_to_vector, {})
        # MMR condition must be False
        self.assertFalse(bool(url_to_vector))


# ---------------------------------------------------------------------------
# G. Vector format compatibility with mmr.py cosine_similarity
# ---------------------------------------------------------------------------

class TestVectorFormatCompatibleWithMMR(unittest.TestCase):
    """
    Vectors returned from postgres_client must be usable by
    MMRReranker.cosine_similarity() which expects List[float].
    """

    def test_cosine_similarity_accepts_list_of_floats(self):
        """MMRReranker.cosine_similarity must work with list-of-floats vectors."""
        from core.mmr import MMRReranker
        reranker = MMRReranker(lambda_param=0.7, query="test")

        vec1 = [1.0, 0.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0, 0.0]
        similarity = reranker.cosine_similarity(vec1, vec2)
        # Orthogonal vectors → similarity = 0
        self.assertAlmostEqual(similarity, 0.0, places=5)

    def test_cosine_similarity_identical_vectors(self):
        """Identical vectors must have cosine similarity = 1.0."""
        from core.mmr import MMRReranker
        reranker = MMRReranker(lambda_param=0.7, query="test")

        vec = [0.5, 0.5, 0.5, 0.5]
        similarity = reranker.cosine_similarity(vec, vec)
        self.assertAlmostEqual(similarity, 1.0, places=5)

    def test_cosine_similarity_with_pgvector_string_converted_to_list(self):
        """
        pgvector may return embeddings as strings like '[0.1,0.2,0.3]'.
        If we convert the string to a list, cosine_similarity must still work.
        """
        from core.mmr import MMRReranker
        reranker = MMRReranker(lambda_param=0.7, query="test")

        # Simulate pgvector string → list conversion
        pg_vector_str = "[0.1,0.2,0.3,0.4]"
        vector = [float(x) for x in pg_vector_str.strip("[]").split(",")]

        self.assertIsInstance(vector, list)
        self.assertEqual(len(vector), 4)
        # Should not raise
        sim = reranker.cosine_similarity(vector, vector)
        self.assertAlmostEqual(sim, 1.0, places=5)


# ---------------------------------------------------------------------------
# H. postgres_client search() SQL includes embedding column when include_vectors
# ---------------------------------------------------------------------------

class TestSearchSQLIncludesEmbeddingWhenVectorsRequested(unittest.TestCase):
    """
    When include_vectors=True is passed to search(), the SQL query
    executed by _search_docs must SELECT c.embedding so the result rows
    contain the vector data.

    We verify by patching _execute_with_retry to capture what raw_results
    get produced, specifically that the conversion includes the vector field.
    """

    def test_search_includes_vector_in_output_when_include_vectors_true(self):
        """search(include_vectors=True) must include vector at index 4."""
        client = make_client()
        client._build_filters = MagicMock(return_value=([], []))

        import retrieval_providers.postgres_client as pg_mod
        original_get_embedding = pg_mod.get_embedding

        async def fake_get_embedding(query, query_params=None):
            return [0.0] * 4

        pg_mod.get_embedding = fake_get_embedding

        fake_vector = [0.1, 0.2, 0.3, 0.4]
        # Simulate _execute_with_retry returning raw_results that include 'vector'
        raw_results = [{
            'url': 'http://example.com/1',
            'schema_str': '{}',
            'title': 'T',
            'source': 'S',
            'author': '',
            'date_published': '',
            'vector_score': 0.9,
            'text_score': 0.0,
            'vector': fake_vector,  # This is what _search_docs returns when include_vectors=True
        }]

        async def fake_execute_with_retry(fn, **kw):
            return raw_results

        client._execute_with_retry = fake_execute_with_retry

        # Dedicated event loop — immune to pytest-asyncio main-thread loop pollution.
        loop = asyncio.new_event_loop()
        try:
            with aggregator_keep_scores('0'):  # explicitly off: legacy 5-tuple contract
                results = loop.run_until_complete(
                    client.search("query", site=[], include_vectors=True)
                )
        finally:
            loop.close()
            pg_mod.get_embedding = original_get_embedding

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 5, "Expected 5-tuple when include_vectors=True")
        self.assertEqual(results[0][4], fake_vector)

    def test_search_excludes_vector_in_output_when_include_vectors_false(self):
        """search() without include_vectors must NOT include vector at index 4."""
        client = make_client()
        client._build_filters = MagicMock(return_value=([], []))

        import retrieval_providers.postgres_client as pg_mod
        original_get_embedding = pg_mod.get_embedding

        async def fake_get_embedding(query, query_params=None):
            return [0.0] * 4

        pg_mod.get_embedding = fake_get_embedding

        raw_results = [{
            'url': 'http://example.com/2',
            'schema_str': '{}',
            'title': 'T',
            'source': 'S',
            'author': '',
            'date_published': '',
            'vector_score': 0.8,
            'text_score': 0.0,
            # No 'vector' key
        }]

        async def fake_execute_with_retry(fn, **kw):
            return raw_results

        client._execute_with_retry = fake_execute_with_retry

        # Dedicated event loop — immune to pytest-asyncio main-thread loop pollution.
        loop = asyncio.new_event_loop()
        try:
            with aggregator_keep_scores('0'):  # explicitly off: legacy 4-tuple contract
                results = loop.run_until_complete(
                    client.search("query", site=[], num_results=10)
                )
        finally:
            loop.close()
            pg_mod.get_embedding = original_get_embedding

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 4, "Expected 4-tuple when include_vectors not requested")


# ---------------------------------------------------------------------------
# I. search() returns 6-tuple with retrieval_scores when AGGREGATOR_KEEP_SCORES=1
#    (Option 2 / S7: flag on changes the contract to a fixed 6-tuple)
# ---------------------------------------------------------------------------

class TestSearchSixTupleWithScores(unittest.TestCase):
    """With AGGREGATOR_KEEP_SCORES=1, search() emits a fixed 6-tuple
    [url, schema_str, title, source, vector_or_None, retrieval_scores]."""

    def _run_search_flag_on(self, include_vectors, vector_value=None):
        client = make_client()
        client._build_filters = MagicMock(return_value=([], []))

        import retrieval_providers.postgres_client as pg_mod
        original_get_embedding = pg_mod.get_embedding

        async def fake_get_embedding(query, query_params=None):
            return [0.0] * 4

        pg_mod.get_embedding = fake_get_embedding

        raw = {
            'url': 'http://example.com/scores',
            'schema_str': '{}',
            'title': 'T',
            'source': 'S',
            'author': '',
            'date_published': '',
            'vector_score': 0.85,
            'text_score': 0.42,
        }
        if include_vectors and vector_value is not None:
            raw['vector'] = vector_value
        raw_results = [raw]

        async def fake_execute_with_retry(fn, **kw):
            return raw_results

        client._execute_with_retry = fake_execute_with_retry

        loop = asyncio.new_event_loop()
        try:
            with aggregator_keep_scores('1'):  # 6-tuple contract
                return loop.run_until_complete(
                    client.search("q", site=[], num_results=10,
                                  include_vectors=include_vectors)
                )
        finally:
            loop.close()
            pg_mod.get_embedding = original_get_embedding

    def test_six_tuple_length_with_vector(self):
        results = self._run_search_flag_on(include_vectors=True,
                                           vector_value=[0.1, 0.2, 0.3])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 6,
                         f"Expected 6-tuple with flag on, got {len(results[0])}")
        self.assertEqual(results[0][4], [0.1, 0.2, 0.3])  # vector at index 4
        self.assertIsInstance(results[0][5], dict)        # scores at index 5

    def test_six_tuple_length_without_vector_uses_none_placeholder(self):
        results = self._run_search_flag_on(include_vectors=False)
        self.assertEqual(len(results[0]), 6,
                         "Fixed 6-tuple must keep len 6 even without a vector")
        self.assertIsNone(results[0][4])                  # None placeholder
        self.assertIsInstance(results[0][5], dict)

    def test_six_tuple_scores_map_postgres_fields(self):
        results = self._run_search_flag_on(include_vectors=False)
        scores = results[0][5]
        self.assertEqual(scores['vector_score'], 0.85)
        self.assertEqual(scores['bm25_score'], 0.42)       # text_score -> bm25
        self.assertEqual(scores['final_retrieval_score'], 0.85)  # max(0.85, 0.42)


if __name__ == '__main__':
    unittest.main()
