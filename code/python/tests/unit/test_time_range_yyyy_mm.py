# Tests for time_range_extractor.py - yyyy_mm pattern and silent fall-through bug
#
# Covers:
# 1. yyyy_mm pattern correctly parses "2025年12月" → start=2025-12-01, end=2025-12-31
# 2. yyyy_mm pattern correctly parses "2024年1月" → start=2024-01-01, end=2024-01-31
# 3. Backward compatibility: existing pattern (past_x_days) still works
# 4. Silent fall-through: unknown pattern match logs a warning

import unittest
from unittest.mock import MagicMock, patch
import calendar

from core.query_analysis.time_range_extractor import TimeRangeExtractor


def _make_handler():
    """Create a minimal mock handler satisfying TimeRangeExtractor.__init__."""
    handler = MagicMock()
    handler.query = ""
    handler.query_params = {}
    handler.state = MagicMock()
    handler.temporal_range = None
    return handler


class TestYyyyMmPattern(unittest.TestCase):
    """Bug 1: yyyy_mm regex has no handler — silent fall-through."""

    def setUp(self):
        handler = _make_handler()
        self.extractor = TimeRangeExtractor(handler)

    def test_parses_december_2025(self):
        """'2025年12月 台灣經濟' should return start=2025-12-01 end=2025-12-31."""
        result = self.extractor._try_regex_parsing("2025年12月 台灣經濟")

        self.assertIsNotNone(result, "Expected a result dict, got None")
        self.assertTrue(result.get("is_temporal"), "is_temporal should be True")
        self.assertEqual(result.get("start_date"), "2025-12-01")
        self.assertEqual(result.get("end_date"), "2025-12-31")
        self.assertEqual(result.get("confidence"), 1.0)
        self.assertEqual(result.get("method"), "regex")

    def test_parses_january_2024(self):
        """'2024年1月' should return start=2024-01-01 end=2024-01-31."""
        result = self.extractor._try_regex_parsing("2024年1月")

        self.assertIsNotNone(result, "Expected a result dict, got None")
        self.assertTrue(result.get("is_temporal"))
        self.assertEqual(result.get("start_date"), "2024-01-01")
        self.assertEqual(result.get("end_date"), "2024-01-31")
        self.assertEqual(result.get("confidence"), 1.0)

    def test_parses_february_leap_year(self):
        """'2024年2月' (leap year) should end on 2024-02-29."""
        result = self.extractor._try_regex_parsing("2024年2月")

        self.assertIsNotNone(result)
        self.assertEqual(result.get("start_date"), "2024-02-01")
        self.assertEqual(result.get("end_date"), "2024-02-29")

    def test_parses_february_non_leap_year(self):
        """'2023年2月' (non-leap year) should end on 2023-02-28."""
        result = self.extractor._try_regex_parsing("2023年2月")

        self.assertIsNotNone(result)
        self.assertEqual(result.get("start_date"), "2023-02-01")
        self.assertEqual(result.get("end_date"), "2023-02-28")

    def test_original_expression_preserved(self):
        """original_expression should capture the matched text."""
        result = self.extractor._try_regex_parsing("2025年12月 台灣經濟")

        self.assertIsNotNone(result)
        self.assertIn("2025年12月", result.get("original_expression", ""))


class TestBackwardCompatibility(unittest.TestCase):
    """Existing patterns must not be affected by the yyyy_mm fix."""

    def setUp(self):
        handler = _make_handler()
        self.extractor = TimeRangeExtractor(handler)

    def test_past_x_days_zh_still_works(self):
        """'過去三天的新聞' should still return a relative 3-day result."""
        result = self.extractor._try_regex_parsing("過去三天的新聞")

        self.assertIsNotNone(result)
        self.assertTrue(result.get("is_temporal"))
        self.assertEqual(result.get("relative_days"), 3)

    def test_no_temporal_returns_none(self):
        """Query without temporal info should return None."""
        result = self.extractor._try_regex_parsing("台灣政治分析")

        self.assertIsNone(result)


class TestSilentFallThroughWarning(unittest.TestCase):
    """Bug 2: unknown pattern match must emit a warning, not silently fall through."""

    def setUp(self):
        handler = _make_handler()
        self.extractor = TimeRangeExtractor(handler)

    def test_unknown_pattern_match_logs_warning(self):
        """If a new regex pattern matches but has no handler, a warning must be logged."""
        # Inject a fake pattern that will match our test query but has no handler
        fake_pattern_name = "unknown_future_pattern"
        self.extractor.REGEX_PATTERNS = {
            fake_pattern_name: r"TESTMATCH\d+",
        }

        with patch("core.query_analysis.time_range_extractor.logger") as mock_logger:
            result = self.extractor._try_regex_parsing("prefix TESTMATCH123 suffix")

        # Should NOT return a result (no handler)
        self.assertIsNone(result, "Should return None when pattern has no handler")

        # But MUST have logged a warning
        warning_calls = mock_logger.warning.call_args_list
        self.assertTrue(
            len(warning_calls) > 0,
            "Expected at least one logger.warning call for unhandled pattern match"
        )
        # Warning message should mention the pattern name
        warning_messages = [str(call) for call in warning_calls]
        matched_msg = any(fake_pattern_name in msg for msg in warning_messages)
        self.assertTrue(
            matched_msg,
            f"Warning should mention pattern name '{fake_pattern_name}'. Got: {warning_messages}"
        )


if __name__ == "__main__":
    unittest.main()
