"""
TDD tests for title+source deduplication in ranking.py.

Problem:
  Chinatimes stores the same article under multiple URLs (different category
  codes, e.g. -260402 vs -260405). The retrieval-layer URL dedup cannot catch
  this because the URL strings differ. The same article therefore appears 2-3
  times in the final ranking output.

Fix:
  After LLM scoring (filtered + sorted, ~L410), and before MMR, add a
  title+source dedup pass:
  - Dedup key: (name, site)
  - Keep only the highest-scoring result for each (name, site) pair
  - Different sites with identical titles are NOT deduplicated (could be
    different perspectives)

Tests:
A. Same title + same source → only highest score survives
B. Same title + different source → both survive (no dedup)
C. No duplicates → list unchanged
D. Three duplicates → only highest score survives
E. dedup_by_title_and_source is importable from core.ranking
"""

import sys
import os
import unittest

# Add code/python to sys.path so we can import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _make_result(name, site, score, url=None):
    """Helper to build a minimal ranked result dict."""
    return {
        'url': url or f'https://{site}/article/{name.replace(" ", "-")}',
        'name': name,
        'site': site,
        'ranking': {'score': score, 'description': 'test snippet'},
        'schema_object': {},
        'sent': False,
    }


class TestTitleDedup(unittest.TestCase):
    """Tests for the dedup_by_title_and_source helper in core.ranking."""

    def setUp(self):
        from core.ranking import dedup_by_title_and_source
        self.dedup = dedup_by_title_and_source

    # -------------------------------------------------------------------
    # Test A: same title + same source → keep only highest score
    # -------------------------------------------------------------------
    def test_same_title_same_source_keeps_highest_score(self):
        results = [
            _make_result("颱風警報", "chinatimes.com", 85, url="https://chinatimes.com/260402/typhoon"),
            _make_result("颱風警報", "chinatimes.com", 72, url="https://chinatimes.com/260405/typhoon"),
        ]
        deduplicated = self.dedup(results)
        self.assertEqual(len(deduplicated), 1)
        self.assertEqual(deduplicated[0]['ranking']['score'], 85)
        self.assertEqual(deduplicated[0]['url'], "https://chinatimes.com/260402/typhoon")

    # -------------------------------------------------------------------
    # Test B: same title + different source → both survive
    # -------------------------------------------------------------------
    def test_same_title_different_source_both_survive(self):
        results = [
            _make_result("颱風警報", "chinatimes.com", 85),
            _make_result("颱風警報", "udn.com", 80),
        ]
        deduplicated = self.dedup(results)
        self.assertEqual(len(deduplicated), 2)
        sites = {r['site'] for r in deduplicated}
        self.assertIn("chinatimes.com", sites)
        self.assertIn("udn.com", sites)

    # -------------------------------------------------------------------
    # Test C: no duplicates → list unchanged
    # -------------------------------------------------------------------
    def test_no_duplicates_returns_unchanged(self):
        results = [
            _make_result("颱風警報", "chinatimes.com", 85),
            _make_result("地震新聞", "chinatimes.com", 75),
            _make_result("政治新聞", "udn.com", 70),
        ]
        deduplicated = self.dedup(results)
        self.assertEqual(len(deduplicated), 3)

    # -------------------------------------------------------------------
    # Test D: three duplicates → only highest score survives
    # -------------------------------------------------------------------
    def test_three_duplicates_keeps_only_highest(self):
        results = [
            _make_result("颱風警報", "chinatimes.com", 85, url="https://chinatimes.com/260402/typhoon"),
            _make_result("颱風警報", "chinatimes.com", 72, url="https://chinatimes.com/260405/typhoon"),
            _make_result("颱風警報", "chinatimes.com", 78, url="https://chinatimes.com/260406/typhoon"),
        ]
        deduplicated = self.dedup(results)
        self.assertEqual(len(deduplicated), 1)
        self.assertEqual(deduplicated[0]['ranking']['score'], 85)

    # -------------------------------------------------------------------
    # Test E: function is importable from core.ranking
    # -------------------------------------------------------------------
    def test_function_importable(self):
        import core.ranking
        self.assertTrue(
            hasattr(core.ranking, 'dedup_by_title_and_source'),
            "dedup_by_title_and_source should be exported from core.ranking"
        )


if __name__ == '__main__':
    unittest.main()
