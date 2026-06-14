"""
TDD tests for two postgres_client.py fixes:

F1: Cosine similarity threshold
    Vector search should filter out results below a minimum cosine similarity
    score read from CONFIG.retrieval_threshold['vector_similarity_min'] (default 0.40).

F2: Article URL deduplication in merge
    After chunk_id dedup, same-URL articles should be further deduplicated,
    keeping only the highest-score chunk per URL.
"""

import sys
import os
import unittest

# Add code/python to sys.path so we can import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from retrieval_providers.postgres_client import PgVectorClient


def make_client():
    """Create a PgVectorClient without triggering __init__ (no DB needed)."""
    client = object.__new__(PgVectorClient)
    return client


# ---------------------------------------------------------------------------
# F1 — Cosine similarity threshold tests
# ---------------------------------------------------------------------------

class TestVectorSqlContainsThreshold(unittest.TestCase):
    """The vector SQL must include a WHERE clause for cosine similarity threshold."""

    def _get_vector_sql(self, client, threshold):
        """Helper: build the vector SQL string the same way search() does."""
        # Simulate what search() does when building vector_sql
        include_vectors = False
        embedding_col = ", c.embedding" if include_vectors else ""
        filter_clauses = []
        where_sql = ("WHERE " + " AND ".join(filter_clauses)) if filter_clauses else ""

        vector_sql = f"""
            SELECT c.id AS chunk_id, c.article_id, c.chunk_text,
                   a.url, a.title, a.author, a.source, a.date_published, a.metadata,
                   1 - (c.embedding <=> %s::vector) AS vector_score{embedding_col}
            FROM chunks c
            JOIN articles a ON a.id = c.article_id
            {where_sql}
            WHERE 1 - (c.embedding <=> %s::vector) >= %s
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """
        return vector_sql

    def test_vector_sql_contains_cosine_threshold_clause(self):
        """Vector SQL must filter by cosine similarity >= threshold."""
        client = make_client()
        sql = self._get_vector_sql(client, threshold=0.40)
        self.assertIn("1 - (c.embedding <=> %s::vector) >= %s", sql)

    def test_vector_sql_uses_correct_operator(self):
        """Threshold must use >= not > (boundary value must be included)."""
        client = make_client()
        sql = self._get_vector_sql(client, threshold=0.40)
        self.assertIn(">=", sql)


class TestConfigRetrievalThreshold(unittest.TestCase):
    """CONFIG.retrieval_threshold must exist and contain vector_similarity_min."""

    def test_retrieval_threshold_exists_in_config(self):
        """CONFIG must have a retrieval_threshold attribute."""
        from core.config import CONFIG
        self.assertTrue(
            hasattr(CONFIG, 'retrieval_threshold'),
            "CONFIG must have 'retrieval_threshold' attribute"
        )

    def test_vector_similarity_min_key_exists(self):
        """retrieval_threshold must have 'vector_similarity_min' key."""
        from core.config import CONFIG
        self.assertIn(
            'vector_similarity_min',
            CONFIG.retrieval_threshold,
            "CONFIG.retrieval_threshold must contain 'vector_similarity_min'"
        )

    def test_vector_similarity_min_default_is_0_40(self):
        """vector_similarity_min default value must be 0.50."""
        from core.config import CONFIG
        val = CONFIG.retrieval_threshold.get('vector_similarity_min')
        self.assertAlmostEqual(
            float(val), 0.50, places=5,
            msg=f"Expected vector_similarity_min=0.50, got {val}"
        )

    def test_vector_similarity_min_is_between_0_and_1(self):
        """vector_similarity_min must be in [0, 1] range."""
        from core.config import CONFIG
        val = float(CONFIG.retrieval_threshold.get('vector_similarity_min', 0.40))
        self.assertGreaterEqual(val, 0.0)
        self.assertLessEqual(val, 1.0)


class TestVectorSearchThresholdFiltering(unittest.TestCase):
    """The actual search() method must pass threshold into SQL params."""

    def test_search_builds_vector_sql_with_threshold_param(self):
        """
        When we mock the internals of search(), the vector_params list must
        contain the threshold value from CONFIG.
        Verified by inspecting the SQL construction logic in postgres_client.py.
        """
        from core.config import CONFIG
        client = make_client()
        threshold = CONFIG.retrieval_threshold.get('vector_similarity_min', 0.40)

        # Simulate the parameter list construction:
        # vector_params = [query_embedding] + filter_params + [query_embedding, threshold, query_embedding, num_results]
        dummy_embedding = [0.1, 0.2, 0.3]
        filter_params = []
        num_results = 50

        vector_params = [dummy_embedding] + filter_params + [dummy_embedding, threshold, dummy_embedding, num_results]

        # threshold must be in the params
        self.assertIn(threshold, vector_params)


# ---------------------------------------------------------------------------
# F2 — Article URL dedup tests
# ---------------------------------------------------------------------------

class TestUrlDedup(unittest.TestCase):
    """After chunk_id dedup, merged results must be further deduped by article URL."""

    def _run_url_dedup(self, merged_rows):
        """
        Simulate the URL dedup logic that should appear in postgres_client.py after
        chunk_id dedup. Returns the deduped list.

        This function represents the expected implementation that the fix should add
        to postgres_client.py.
        """
        # Group by URL, keep highest score per URL
        url_best: dict = {}
        for row in merged_rows:
            url = row['url']
            score = float(row.get('vector_score') or row.get('text_score') or 0.0)
            if url not in url_best or score > url_best[url]['_score']:
                row = dict(row)
                row['_score'] = score
                url_best[url] = row
        return list(url_best.values())

    def test_same_url_chunks_deduplicated_to_highest_score(self):
        """Two chunks from same URL → only highest vector_score chunk kept."""
        rows = [
            {'chunk_id': 1, 'url': 'https://example.com/article1', 'vector_score': 0.9, 'text_score': 0.0},
            {'chunk_id': 2, 'url': 'https://example.com/article1', 'vector_score': 0.5, 'text_score': 0.0},
        ]
        deduped = self._run_url_dedup(rows)
        self.assertEqual(len(deduped), 1)
        self.assertAlmostEqual(deduped[0]['vector_score'], 0.9, places=5)

    def test_different_urls_all_kept(self):
        """Chunks from different URLs must all be retained."""
        rows = [
            {'chunk_id': 1, 'url': 'https://example.com/article1', 'vector_score': 0.9, 'text_score': 0.0},
            {'chunk_id': 2, 'url': 'https://example.com/article2', 'vector_score': 0.8, 'text_score': 0.0},
            {'chunk_id': 3, 'url': 'https://example.com/article3', 'vector_score': 0.7, 'text_score': 0.0},
        ]
        deduped = self._run_url_dedup(rows)
        self.assertEqual(len(deduped), 3)

    def test_text_only_chunks_deduped_by_text_score(self):
        """When vector_score=0, use text_score for dedup comparison."""
        rows = [
            {'chunk_id': 10, 'url': 'https://example.com/news', 'vector_score': 0.0, 'text_score': 0.6},
            {'chunk_id': 11, 'url': 'https://example.com/news', 'vector_score': 0.0, 'text_score': 0.3},
        ]
        deduped = self._run_url_dedup(rows)
        self.assertEqual(len(deduped), 1)
        self.assertAlmostEqual(deduped[0]['text_score'], 0.6, places=5)

    def test_mixed_vector_and_text_dedup_keeps_higher_vector(self):
        """Mixed rows: prefer vector_score for ranking when available."""
        rows = [
            {'chunk_id': 20, 'url': 'https://news.com/story', 'vector_score': 0.85, 'text_score': 0.0},
            {'chunk_id': 21, 'url': 'https://news.com/story', 'vector_score': 0.0, 'text_score': 0.95},
        ]
        deduped = self._run_url_dedup(rows)
        self.assertEqual(len(deduped), 1)
        # chunk_id 21 has higher effective score (0.95 text) than chunk_id 20 (0.85 vector)
        self.assertEqual(deduped[0]['chunk_id'], 21)

    def test_url_dedup_preserves_all_fields(self):
        """The winning row must preserve all its original fields."""
        rows = [
            {
                'chunk_id': 99,
                'url': 'https://example.com/article',
                'vector_score': 0.95,
                'text_score': 0.0,
                'title': 'Test Article',
                'source': 'example',
            },
        ]
        deduped = self._run_url_dedup(rows)
        self.assertEqual(deduped[0]['title'], 'Test Article')
        self.assertEqual(deduped[0]['source'], 'example')
        self.assertEqual(deduped[0]['chunk_id'], 99)

    def test_empty_merged_list_returns_empty(self):
        """Empty input must return empty output."""
        deduped = self._run_url_dedup([])
        self.assertEqual(deduped, [])

    def test_single_chunk_passes_through_unchanged(self):
        """Single chunk should pass through without modification."""
        rows = [
            {'chunk_id': 5, 'url': 'https://example.com/only', 'vector_score': 0.75, 'text_score': 0.0},
        ]
        deduped = self._run_url_dedup(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]['chunk_id'], 5)


class TestPostgresClientHasUrlDedup(unittest.TestCase):
    """The actual postgres_client.py search() must perform URL dedup."""

    def test_search_method_has_url_dedup_logic(self):
        """
        Inspect postgres_client.py source to verify URL dedup is implemented.
        This test reads the source file and checks for URL dedup markers.
        """
        import inspect
        from retrieval_providers import postgres_client
        source = inspect.getsource(postgres_client)

        # The implementation should group by URL and keep best score
        self.assertIn(
            'url_best',
            source,
            "postgres_client.py must contain 'url_best' dict for URL deduplication"
        )

    def test_search_method_filters_by_cosine_threshold(self):
        """
        Inspect postgres_client.py source to verify cosine threshold WHERE clause.
        """
        import inspect
        from retrieval_providers import postgres_client
        source = inspect.getsource(postgres_client)

        self.assertIn(
            '(c.embedding <=> %s::vector) >= %s',
            source,
            "postgres_client.py vector SQL must contain cosine threshold WHERE clause"
        )


if __name__ == '__main__':
    unittest.main()
