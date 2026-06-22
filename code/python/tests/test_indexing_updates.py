"""
Test script for crawled_registry and overlap features.
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


class TestChunkingOverlap:
    """Tests for chunking overlap feature."""

    def test_overlap_basic(self):
        """Test basic overlap functionality."""
        from indexing.chunking_engine import ChunkingEngine
        from indexing.ingestion_engine import CanonicalDataModel

        engine = ChunkingEngine()

        # Create a CDM with enough text to generate multiple chunks
        cdm = CanonicalDataModel(
            url="https://example.com/news/1",
            headline="測試標題",
            article_body="第一段文字內容。" * 20 + "第二段文字內容。" * 20 + "第三段文字內容。" * 20,
            source_id="test"
        )

        chunks = engine.chunk_article(cdm, add_overlap=True)

        # Should have multiple chunks
        assert len(chunks) >= 2, f"Expected at least 2 chunks, got {len(chunks)}"

        # Check overlap properties
        for i, chunk in enumerate(chunks):
            # embedding_text should be set
            assert chunk.embedding_text, f"Chunk {i} has no embedding_text"

            # embedding_text should be longer than full_text (except possibly edge cases)
            if i > 0 and i < len(chunks) - 1:
                # Middle chunks should have both prefix and suffix
                assert len(chunk.embedding_text) > len(chunk.full_text), \
                    f"Chunk {i} embedding_text should be longer than full_text"

            # char_start and char_end should remain unchanged (original positions)
            assert chunk.char_start >= 0
            assert chunk.char_end > chunk.char_start

    def test_overlap_first_chunk(self):
        """Test that first chunk has no prefix."""
        from indexing.chunking_engine import ChunkingEngine
        from indexing.ingestion_engine import CanonicalDataModel

        engine = ChunkingEngine()

        cdm = CanonicalDataModel(
            url="https://example.com/news/2",
            headline="測試",
            article_body="第一句話。" * 50 + "第二句話。" * 50,
            source_id="test"
        )

        chunks = engine.chunk_article(cdm, add_overlap=True)
        assert len(chunks) >= 2

        first_chunk = chunks[0]
        # First chunk's embedding_text should start with the same content as full_text
        assert first_chunk.embedding_text.startswith(first_chunk.full_text[:10])

    def test_overlap_last_chunk(self):
        """Test that last chunk has no suffix."""
        from indexing.chunking_engine import ChunkingEngine
        from indexing.ingestion_engine import CanonicalDataModel

        engine = ChunkingEngine()

        cdm = CanonicalDataModel(
            url="https://example.com/news/3",
            headline="測試",
            article_body="第一句話。" * 50 + "第二句話。" * 50,
            source_id="test"
        )

        chunks = engine.chunk_article(cdm, add_overlap=True)
        assert len(chunks) >= 2

        last_chunk = chunks[-1]
        # Last chunk's embedding_text should end with the same content as full_text
        assert last_chunk.embedding_text.endswith(last_chunk.full_text[-10:])

    def test_single_chunk_no_overlap(self):
        """Test that single chunk has embedding_text = full_text."""
        from indexing.chunking_engine import ChunkingEngine
        from indexing.ingestion_engine import CanonicalDataModel

        engine = ChunkingEngine()

        # Short article that becomes single chunk
        cdm = CanonicalDataModel(
            url="https://example.com/news/4",
            headline="短文",
            article_body="這是一篇短文。",
            source_id="test"
        )

        chunks = engine.chunk_article(cdm, add_overlap=True)
        assert len(chunks) == 1

        # Single chunk: embedding_text should equal full_text
        assert chunks[0].embedding_text == chunks[0].full_text

    def test_overlap_disabled(self):
        """Test that overlap can be disabled."""
        from indexing.chunking_engine import ChunkingEngine
        from indexing.ingestion_engine import CanonicalDataModel

        engine = ChunkingEngine()

        cdm = CanonicalDataModel(
            url="https://example.com/news/5",
            headline="測試",
            article_body="第一句話。" * 50,
            source_id="test"
        )

        chunks = engine.chunk_article(cdm, add_overlap=False)

        # Without overlap, embedding_text should be empty (default)
        for chunk in chunks:
            assert chunk.embedding_text == ""

    def test_overlap_size(self):
        """Test that overlap is approximately the configured size."""
        from indexing.chunking_engine import ChunkingEngine
        from indexing.ingestion_engine import CanonicalDataModel

        engine = ChunkingEngine()
        overlap_chars = engine.overlap_chars  # Should be 30

        cdm = CanonicalDataModel(
            url="https://example.com/news/6",
            headline="測試",
            article_body="這是第一句話內容。" * 30 + "這是第二句話內容。" * 30 + "這是第三句話內容。" * 30,
            source_id="test"
        )

        chunks = engine.chunk_article(cdm, add_overlap=True)
        assert len(chunks) >= 3

        # Middle chunk should have overlap on both sides
        middle_idx = len(chunks) // 2
        middle_chunk = chunks[middle_idx]

        # embedding_text should be approximately full_text + 2 * overlap_chars
        expected_min_length = len(middle_chunk.full_text) + overlap_chars
        assert len(middle_chunk.embedding_text) >= expected_min_length, \
            f"Middle chunk embedding_text ({len(middle_chunk.embedding_text)}) " \
            f"should be at least {expected_min_length}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
