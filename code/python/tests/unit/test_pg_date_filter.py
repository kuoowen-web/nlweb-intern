"""
TDD tests for PostgreSQL date filter bug fix.

Bug: baseHandler.py constructs search_filters as
  [{"field": "datePublished", "operator": "gte", "value": start_date}]
and passes them via kwargs['filters'], but postgres_client.py search()
never reads kwargs['filters'], causing date filters to be silently dropped.

Fix: _build_filters() must accept a third 'kwargs_filters' parameter and
convert {"field": "datePublished", "operator": "gte"/"lte", "value": ...}
entries to SQL WHERE clauses. search() must pass kwargs.get('filters', []).
"""

import sys
import os
import unittest

# Add code/python to sys.path so we can import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# We test _build_filters() in isolation — no DB connection needed.
# We instantiate PgVectorClient only for unit tests on _build_filters(),
# bypassing __init__ by using object.__new__.
from retrieval_providers.postgres_client import PgVectorClient


def make_client():
    """Create a PgVectorClient without triggering __init__ (no DB needed)."""
    client = object.__new__(PgVectorClient)
    return client


class TestBuildFiltersKwargsGte(unittest.TestCase):
    """_build_filters should translate operator=gte into a >= SQL clause."""

    def test_gte_date_filter_generates_correct_sql_clause(self):
        client = make_client()
        kwargs_filters = [
            {"field": "datePublished", "operator": "gte", "value": "2026-01-01"}
        ]
        clauses, params = client._build_filters(sites=[], query_params=None, kwargs_filters=kwargs_filters)
        # Exactly one clause
        self.assertEqual(len(clauses), 1)
        self.assertIn("a.date_published >= %s", clauses)
        # Param is the date string
        self.assertIn("2026-01-01", params)

    def test_gte_date_filter_param_value_is_correct(self):
        client = make_client()
        kwargs_filters = [
            {"field": "datePublished", "operator": "gte", "value": "2025-06-15"}
        ]
        clauses, params = client._build_filters(sites=[], query_params=None, kwargs_filters=kwargs_filters)
        self.assertEqual(params[0], "2025-06-15")


class TestBuildFiltersKwargsLte(unittest.TestCase):
    """_build_filters should translate operator=lte into a <= SQL clause."""

    def test_lte_date_filter_generates_correct_sql_clause(self):
        client = make_client()
        kwargs_filters = [
            {"field": "datePublished", "operator": "lte", "value": "2026-03-01"}
        ]
        clauses, params = client._build_filters(sites=[], query_params=None, kwargs_filters=kwargs_filters)
        self.assertEqual(len(clauses), 1)
        self.assertIn("a.date_published <= %s", clauses)
        self.assertIn("2026-03-01", params)


class TestBuildFiltersBothOperators(unittest.TestCase):
    """Both gte and lte can coexist — AND condition."""

    def test_gte_and_lte_together_produce_two_clauses(self):
        client = make_client()
        kwargs_filters = [
            {"field": "datePublished", "operator": "gte", "value": "2026-01-01"},
            {"field": "datePublished", "operator": "lte", "value": "2026-03-31"},
        ]
        clauses, params = client._build_filters(sites=[], query_params=None, kwargs_filters=kwargs_filters)
        self.assertEqual(len(clauses), 2)
        self.assertIn("a.date_published >= %s", clauses)
        self.assertIn("a.date_published <= %s", clauses)
        self.assertIn("2026-01-01", params)
        self.assertIn("2026-03-31", params)


class TestBuildFiltersBackwardCompat(unittest.TestCase):
    """Not passing kwargs_filters must leave existing behaviour unchanged."""

    def test_no_kwargs_filters_no_sites_returns_empty(self):
        client = make_client()
        clauses, params = client._build_filters(sites=[], query_params=None)
        self.assertEqual(clauses, [])
        self.assertEqual(params, [])

    def test_no_kwargs_filters_with_sites_returns_site_clause_only(self):
        client = make_client()
        clauses, params = client._build_filters(
            sites=["cna.com.tw"],
            query_params=None
        )
        self.assertEqual(len(clauses), 1)
        self.assertIn("a.source IN (%s)", clauses)
        self.assertIn("cna.com.tw", params)

    def test_existing_query_params_date_from_still_works(self):
        """query_params['date_from'] path must still produce a clause (backward compat)."""
        client = make_client()
        clauses, params = client._build_filters(
            sites=[],
            query_params={"date_from": "2025-01-01"}
        )
        self.assertEqual(len(clauses), 1)
        self.assertIn("a.date_published >= %s", clauses)
        self.assertIn("2025-01-01", params)

    def test_existing_query_params_date_to_still_works(self):
        client = make_client()
        clauses, params = client._build_filters(
            sites=[],
            query_params={"date_to": "2025-12-31"}
        )
        self.assertEqual(len(clauses), 1)
        self.assertIn("a.date_published <= %s", clauses)
        self.assertIn("2025-12-31", params)


class TestBuildFiltersMergesWithQueryParams(unittest.TestCase):
    """kwargs_filters and query_params date filters should both be included (AND)."""

    def test_kwargs_gte_plus_query_params_date_to_gives_two_clauses(self):
        client = make_client()
        kwargs_filters = [
            {"field": "datePublished", "operator": "gte", "value": "2026-01-01"},
        ]
        clauses, params = client._build_filters(
            sites=[],
            query_params={"date_to": "2026-12-31"},
            kwargs_filters=kwargs_filters
        )
        self.assertEqual(len(clauses), 2)
        self.assertIn("a.date_published >= %s", clauses)
        self.assertIn("a.date_published <= %s", clauses)


class TestBuildFiltersIgnoresUnknownFields(unittest.TestCase):
    """kwargs_filters with an unknown field name should be silently ignored (no SQL injection)."""

    def test_unknown_field_is_ignored(self):
        client = make_client()
        kwargs_filters = [
            {"field": "unknown_field", "operator": "gte", "value": "some_value"}
        ]
        clauses, params = client._build_filters(sites=[], query_params=None, kwargs_filters=kwargs_filters)
        self.assertEqual(clauses, [])
        self.assertEqual(params, [])

    def test_unknown_operator_is_ignored(self):
        client = make_client()
        kwargs_filters = [
            {"field": "datePublished", "operator": "badop", "value": "2026-01-01"}
        ]
        clauses, params = client._build_filters(sites=[], query_params=None, kwargs_filters=kwargs_filters)
        self.assertEqual(clauses, [])
        self.assertEqual(params, [])


class TestSearchPassesFiltersToQueryBuilder(unittest.TestCase):
    """
    Integration-style unit test: verify that _build_filters is called with
    the kwargs_filters extracted from kwargs['filters'] inside search().

    We monkey-patch _build_filters to capture what it receives.
    """

    def test_search_passes_kwargs_filters_to_build_filters(self):
        """search() must extract kwargs['filters'] and pass to _build_filters."""
        client = make_client()

        captured = {}

        def fake_build_filters(sites, query_params=None, kwargs_filters=None):
            if 'kwargs_filters' not in captured:
                captured['kwargs_filters'] = kwargs_filters
            return [], []

        client._build_filters = fake_build_filters

        import asyncio

        async def fake_get_embedding(query, query_params=None):
            return [0.0] * 1536

        async def fake_execute_with_retry(query_func, **kwargs):
            return []

        # Patch dependencies
        import retrieval_providers.postgres_client as pg_mod
        original_get_embedding = pg_mod.get_embedding
        pg_mod.get_embedding = fake_get_embedding
        client._execute_with_retry = fake_execute_with_retry

        filters = [{"field": "datePublished", "operator": "gte", "value": "2026-01-01"}]
        try:
            asyncio.get_event_loop().run_until_complete(
                client.search("test query", site=[], num_results=10, filters=filters)
            )
        except Exception:
            pass  # May fail for other reasons; we only care about captured
        finally:
            pg_mod.get_embedding = original_get_embedding

        self.assertEqual(
            captured.get('kwargs_filters'),
            filters,
            "search() did not pass kwargs['filters'] to _build_filters()"
        )


if __name__ == '__main__':
    unittest.main()
