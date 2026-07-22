"""
Test script for crawled_registry features.
Run from nlweb root: python -m pytest code/python/tests/test_indexing_updates.py -v
"""

import tempfile
from pathlib import Path

import pytest


class TestCrawledRegistry:
    """Tests for CrawledRegistry SQLite migration."""

    def test_basic_operations(self):
        """Test basic CRUD operations."""
        from crawler.core.crawled_registry import CrawledRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_registry.db"
            registry = CrawledRegistry(db_path)

            # Test mark_crawled and is_crawled
            url = "https://example.com/news/123"
            assert not registry.is_crawled(url)

            registry.mark_crawled(
                url=url,
                source_id="test",
                date_published="2026-01-01T10:00:00",
                date_modified="2026-01-02T15:00:00",
                content="這是測試文章內容，用於生成 content hash。"
            )

            assert registry.is_crawled(url)
            registry.close()

    def test_needs_update(self):
        """Test dateModified comparison for re-crawling."""
        from crawler.core.crawled_registry import CrawledRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_registry.db"
            registry = CrawledRegistry(db_path)

            url = "https://example.com/news/456"

            # New URL always needs update
            assert registry.needs_update(url, "2026-01-01T10:00:00")

            # Mark as crawled
            registry.mark_crawled(
                url=url,
                source_id="test",
                date_modified="2026-01-01T10:00:00"
            )

            # Same date_modified: no update needed
            assert not registry.needs_update(url, "2026-01-01T10:00:00")

            # Older date_modified: no update needed
            assert not registry.needs_update(url, "2025-12-31T10:00:00")

            # Newer date_modified: update needed
            assert registry.needs_update(url, "2026-01-02T10:00:00")

            # None date_modified: no update
            assert not registry.needs_update(url, None)

            registry.close()

    def test_content_hash_dedup(self):
        """Test cross-source deduplication via content hash."""
        from crawler.core.crawled_registry import CrawledRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_registry.db"
            registry = CrawledRegistry(db_path)

            content = "這是一篇新聞文章的內容，前五百字會被用來計算 hash，以便跨來源去重。" * 10

            # First article from udn
            url1 = "https://udn.com/news/123"
            registry.mark_crawled(url1, "udn", content=content)

            # Same content from money_udn should be detected as duplicate
            url2 = "https://money.udn.com/news/456"
            duplicate = registry.find_duplicate_by_hash(content, exclude_url=url2)
            assert duplicate == url1

            # Different content should not be duplicate
            different_content = "這是完全不同的文章內容。" * 20
            duplicate2 = registry.find_duplicate_by_hash(different_content)
            assert duplicate2 is None

            registry.close()

    def test_statistics(self):
        """Test statistics queries."""
        from crawler.core.crawled_registry import CrawledRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_registry.db"
            registry = CrawledRegistry(db_path)

            # Add some test data
            for i in range(5):
                registry.mark_crawled(f"https://udn.com/news/{i}", "udn")
            for i in range(3):
                registry.mark_crawled(f"https://ltn.com/news/{i}", "ltn")

            assert registry.get_total_count() == 8
            assert registry.get_count_by_source("udn") == 5
            assert registry.get_count_by_source("ltn") == 3

            stats = registry.get_stats()
            assert stats['total'] == 8
            assert stats['by_source']['udn'] == 5
            assert stats['by_source']['ltn'] == 3

            registry.close()

    def test_migrate_from_txt(self):
        """Test migration from old txt format."""
        from crawler.core.crawled_registry import CrawledRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create old-style txt file
            txt_path = Path(tmpdir) / "test_source.txt"
            urls = [
                "https://example.com/1",
                "https://example.com/2",
                "https://example.com/3",
            ]
            txt_path.write_text("\n".join(urls) + "\n", encoding="utf-8")

            # Migrate
            db_path = Path(tmpdir) / "test_registry.db"
            registry = CrawledRegistry(db_path)
            migrated = registry.migrate_from_txt("test_source", txt_path)

            assert migrated == 3
            for url in urls:
                assert registry.is_crawled(url)

            # Migrate again should skip duplicates
            migrated_again = registry.migrate_from_txt("test_source", txt_path)
            assert migrated_again == 0

            registry.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
