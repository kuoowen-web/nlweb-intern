# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Wikipedia Client - Interface for Wikipedia API operations.
Provides encyclopedic background knowledge for entity-heavy queries.

Features:
- Multi-language support (Chinese, English)
- Async wrapper for synchronous Wikipedia library
- Caching with configurable TTL
- Disambiguation handling
- Token optimization via summary truncation
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("wikipedia_client")

# Try to import wikipedia library
try:
    import wikipedia
    WIKIPEDIA_AVAILABLE = True
except ImportError:
    WIKIPEDIA_AVAILABLE = False
    logger.warning("Wikipedia library not available. Install with: pip install wikipedia")


class WikipediaClient:
    """
    Client for Wikipedia API operations.

    Provides encyclopedic background knowledge as Tier 6 enrichment source.
    Complements Google Search by providing authoritative, structured content.

    Features:
    - Caching: Reduces API calls, improves latency
    - Timeout: Prevents slow API calls from blocking pipeline
    - Language support: Chinese (zh) and English (en)
    """

    def __init__(self, language: str = None):
        """
        Initialize Wikipedia client.

        Args:
            language: Wikipedia language code (zh for Chinese, en for English).
                     Defaults to config value or 'zh'.
        """
        if not WIKIPEDIA_AVAILABLE:
            logger.error("Wikipedia library not installed. WikipediaClient will return empty results.")
            self._enabled = False
            return

        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        wiki_config = tier_6_config.get("wikipedia", {})

        self._enabled = wiki_config.get("enabled", False)
        self._language = language or wiki_config.get("language", "zh")
        self._max_results = wiki_config.get("max_results", 3)
        self._max_summary_length = wiki_config.get("max_summary_length", 500)
        self._timeout = wiki_config.get("timeout", 5.0)

        # Set Wikipedia language
        wikipedia.set_lang(self._language)

        # Cache configuration
        cache_config = wiki_config.get("cache", {})
        self._cache: Dict[str, Tuple[List, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 24))  # Wikipedia content changes less often
        self._cache_max_size = cache_config.get("max_size", 200)

        logger.info(
            f"Initialized WikipediaClient (enabled={self._enabled}, "
            f"lang={self._language}, cache={self._cache_enabled})"
        )

    async def search(
        self,
        query: str,
        max_results: int = None,
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Search Wikipedia and return summaries.

        Args:
            query: Search query string
            max_results: Maximum number of results (defaults to config value)
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not WIKIPEDIA_AVAILABLE or not self._enabled:
            logger.debug("Wikipedia client disabled or library not available")
            return []

        max_results = max_results or self._max_results
        timeout = timeout or self._timeout

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = f"{query}:{max_results}:{self._language}"
            if self._cache_enabled and cache_key in self._cache:
                cached_results, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"Wikipedia cache HIT for query: '{query}' ({len(cached_results)} results)")
                    results = cached_results
                else:
                    del self._cache[cache_key]
                    logger.debug(f"Wikipedia cache EXPIRED for query: '{query}'")

            # Cache miss - perform actual search with timeout
            if not cache_hit:
                try:
                    results = await asyncio.wait_for(
                        self._do_search(query, max_results),
                        timeout=timeout
                    )

                    # Update cache
                    if self._cache_enabled and results:
                        self._update_cache(cache_key, results)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"Wikipedia search TIMEOUT after {timeout}s for query: '{query}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_results, _ = self._cache[cache_key]
                        logger.info(f"Returning stale Wikipedia cache ({len(stale_results)} results)")
                        results = stale_results

            return results

        except Exception as e:
            logger.error(f"Error during Wikipedia search: {e}", exc_info=True)
            return []

        finally:
            # Log analytics (if query_id provided)
            latency_ms = int((time.time() - start_time) * 1000)
            if query_id:
                try:
                    from core.query_logger import get_query_logger
                    query_logger = get_query_logger()
                    query_logger.log_tier_6_enrichment(
                        query_id=query_id,
                        source_type="wikipedia",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"query": query, "language": self._language}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log Wikipedia analytics: {e}")

    async def _do_search(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        """
        Perform the actual Wikipedia search.

        Args:
            query: Search query string
            max_results: Maximum number of results

        Returns:
            List of Wikipedia article summaries
        """
        loop = asyncio.get_event_loop()

        # Run synchronous Wikipedia search in thread pool
        search_results = await loop.run_in_executor(
            None,
            lambda: wikipedia.search(query, results=max_results)
        )

        logger.info(f"Wikipedia search for '{query}': found {len(search_results)} titles")

        summaries = []
        for title in search_results:
            try:
                # Get page content in thread pool
                page = await loop.run_in_executor(
                    None,
                    lambda t=title: wikipedia.page(t, auto_suggest=False)
                )

                # Truncate summary at sentence boundary
                summary = self._truncate_summary(page.summary)

                summaries.append({
                    'title': f"[維基百科] {page.title}",
                    'snippet': summary,
                    'link': page.url,
                    'tier': 6,
                    'type': 'encyclopedia',
                    'source': 'wikipedia'
                })

                logger.debug(f"Wikipedia: Got summary for '{page.title}' ({len(summary)} chars)")

            except wikipedia.exceptions.DisambiguationError as e:
                # Skip disambiguation pages
                logger.debug(f"Wikipedia disambiguation page for '{title}', skipping")
                continue

            except wikipedia.exceptions.PageError:
                logger.warning(f"Wikipedia page not found: '{title}'")
                continue

            except Exception as e:
                logger.warning(f"Error fetching Wikipedia page '{title}': {e}")
                continue

        return summaries

    def _truncate_summary(self, summary: str) -> str:
        """
        Truncate summary at sentence boundary.

        Args:
            summary: Full Wikipedia summary

        Returns:
            Truncated summary
        """
        if len(summary) <= self._max_summary_length:
            return summary

        # Try to cut at last Chinese period before limit
        truncate_at = summary.rfind('。', 0, self._max_summary_length)
        if truncate_at == -1:
            # Try English period
            truncate_at = summary.rfind('.', 0, self._max_summary_length)
        if truncate_at == -1:
            # Just cut at limit
            truncate_at = self._max_summary_length - 3  # Leave room for "..."

        result = summary[:truncate_at + 1] if truncate_at > 0 else summary[:self._max_summary_length - 3]
        if not result.endswith(('。', '.', '！', '？', '!', '?')):
            result += "..."

        return result

    def _update_cache(self, cache_key: str, results: List[Dict]) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key
            results: Search results to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug(f"Wikipedia cache evicted oldest entry")

        self._cache[cache_key] = (results, datetime.now())
        logger.debug(f"Wikipedia cache UPDATED: '{cache_key}' ({len(results)} results)")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Wikipedia cache cleared ({count} entries)")
        return count

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats
        """
        return {
            "enabled": self._cache_enabled,
            "size": len(self._cache),
            "max_size": self._cache_max_size,
            "ttl_hours": self._cache_ttl.total_seconds() / 3600,
            "language": self._language
        }

    def is_available(self) -> bool:
        """
        Check if Wikipedia client is available and enabled.

        Returns:
            True if client can be used
        """
        return WIKIPEDIA_AVAILABLE and self._enabled
