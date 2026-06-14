# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Google Custom Search Client - Interface for Google Custom Search API operations.
Provides read-only access to web search results.

API Documentation: https://developers.google.com/custom-search/v1/overview
Free Tier: 100 queries per day

Features:
- In-memory caching with configurable TTL
- Timeout protection with graceful fallback
- Snippet truncation for token optimization
"""

import json
import httpx
import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import quote
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("google_search_client")


class GoogleSearchClient:
    """
    Client for Google Custom Search API operations.

    Setup Instructions:
    1. Go to https://console.cloud.google.com/
    2. Create a new project or select existing
    3. Enable "Custom Search API"
    4. Create credentials (API Key)
    5. Go to https://programmablesearchengine.google.com/
    6. Create a new search engine
    7. Get the Search Engine ID (cx parameter)

    Features:
    - Caching: Reduces API quota usage, improves latency for repeated queries
    - Timeout: Prevents slow API calls from blocking the reasoning pipeline
    - Snippet truncation: Reduces token consumption
    """

    def __init__(self):
        """Initialize Google Search client."""
        # Get API key from environment or config
        import os
        self.api_key = os.getenv('GOOGLE_SEARCH_API_KEY') or CONFIG.get('google_search_api_key')
        self.search_engine_id = os.getenv('GOOGLE_SEARCH_ENGINE_ID') or CONFIG.get('google_search_engine_id')

        if not self.api_key:
            logger.warning("Google Search API Key not configured. Set GOOGLE_SEARCH_API_KEY environment variable.")
        if not self.search_engine_id:
            logger.warning("Google Search Engine ID not configured. Set GOOGLE_SEARCH_ENGINE_ID environment variable.")

        self.api_endpoint = "https://www.googleapis.com/customsearch/v1"

        # Cache configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        web_config = tier_6_config.get("web_search", {})
        cache_config = web_config.get("cache", {})

        # Cache layer
        self._cache: Dict[str, Tuple[List, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 1))
        self._cache_max_size = cache_config.get("max_size", 100)

        # Timeout configuration
        self._timeout = web_config.get("timeout", 3.0)
        self._fallback_to_local = web_config.get("fallback_to_local", True)

        # Token optimization
        self._max_snippet_length = web_config.get("max_snippet_length", 200)

        logger.info(
            f"Initialized GoogleSearchClient (cache={self._cache_enabled}, "
            f"ttl={self._cache_ttl}, timeout={self._timeout}s)"
        )

    async def search_all_sites(
        self,
        query: str,
        num_results: int = 5,
        timeout: Optional[float] = None,
        query_id: Optional[str] = None
    ) -> List[tuple]:
        """
        Search across all sites using Google Custom Search.

        Args:
            query: Search query string
            num_results: Number of results to return (max 10 per request)
            timeout: Optional timeout override (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of tuples: (url, schema_json, title, site, [])
        """
        if not self.api_key or not self.search_engine_id:
            logger.error("Google Search API not configured. Returning empty results.")
            return []

        # Google Custom Search API limits to 10 results per request
        num_results = min(num_results, 10)

        # Use configured timeout if not specified
        timeout = timeout or self._timeout

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = f"{query}:{num_results}"
            if self._cache_enabled and cache_key in self._cache:
                cached_results, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"Cache HIT for query: '{query}' ({len(cached_results)} results)")
                    results = cached_results
                else:
                    # Cache expired, remove it
                    del self._cache[cache_key]
                    logger.debug(f"Cache EXPIRED for query: '{query}'")

            # Cache miss - perform actual search with timeout
            if not cache_hit:
                try:
                    results = await asyncio.wait_for(
                        self._do_search(query, num_results),
                        timeout=timeout
                    )

                    # Update cache
                    if self._cache_enabled:
                        self._update_cache(cache_key, results)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"Web search TIMEOUT after {timeout}s for query: '{query}'")

                    # Try to return stale cache as fallback
                    if self._fallback_to_local and cache_key in self._cache:
                        stale_results, _ = self._cache[cache_key]
                        logger.info(f"Returning stale cache ({len(stale_results)} results) as fallback")
                        results = stale_results
                    else:
                        results = []

            return results

        except Exception as e:
            logger.error(f"Error during Google search: {e}", exc_info=True)
            return []

        finally:
            # Log analytics (if query_id provided and logger available)
            latency_ms = int((time.time() - start_time) * 1000)
            if query_id:
                try:
                    from core.query_logger import get_query_logger
                    query_logger = get_query_logger()
                    query_logger.log_tier_6_enrichment(
                        query_id=query_id,
                        source_type="google_search",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"query": query, "num_results": num_results}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log analytics: {e}")

    async def _do_search(self, query: str, num_results: int) -> List[tuple]:
        """
        Perform the actual API search.

        Args:
            query: Search query string
            num_results: Number of results to return

        Returns:
            List of tuples: (url, schema_json, title, site, [])
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {
                'key': self.api_key,
                'cx': self.search_engine_id,
                'q': query,
                'num': num_results
            }

            logger.info(f"Google Search API call: '{query}' (num_results={num_results})")

            response = await client.get(self.api_endpoint, params=params)
            response.raise_for_status()

            data = response.json()

            # Parse results
            results = []
            items = data.get('items', [])

            logger.info(f"Google Search returned {len(items)} results")

            for item in items:
                processed = self._process_search_result(item)
                if processed:
                    results.append(processed)

            return results

    def _process_search_result(self, item: dict) -> Optional[tuple]:
        """
        Process and truncate a search result.

        Args:
            item: Raw search result from Google API

        Returns:
            Tuple: (url, schema_json, title, site, []) or None if invalid
        """
        url = item.get('link', '')
        if not url:
            return None

        title = item.get('title', 'No Title')
        snippet = item.get('snippet', '')

        # Truncate snippet intelligently (at sentence boundary)
        if len(snippet) > self._max_snippet_length:
            # Try to cut at last Chinese period before limit
            truncate_at = snippet.rfind('。', 0, self._max_snippet_length)
            if truncate_at == -1:
                # Try English period
                truncate_at = snippet.rfind('.', 0, self._max_snippet_length)
            if truncate_at == -1:
                # Just cut at limit
                truncate_at = self._max_snippet_length

            snippet = snippet[:truncate_at + 1] if truncate_at > 0 else snippet[:self._max_snippet_length]
            if not snippet.endswith(('。', '.', '！', '？', '!', '?')):
                snippet += "..."

        # Extract domain from URL
        site = self._extract_domain(url)

        # Build schema_json (compatible with existing format)
        schema_obj = {
            "description": snippet,
            "headline": title,
            "url": url,
            "provider": "Google Search"
        }

        schema_json = json.dumps(schema_obj, ensure_ascii=False)

        # Return tuple format: (url, schema_json, name, site, [vector])
        return (url, schema_json, title, site, [])

    def _update_cache(self, cache_key: str, results: List[tuple]) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key (query:num_results)
            results: Search results to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug(f"Cache evicted oldest entry: '{oldest_key}'")

        self._cache[cache_key] = (results, datetime.now())
        logger.debug(f"Cache UPDATED for key: '{cache_key}' ({len(results)} results)")

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc
            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return "unknown"

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Cache cleared ({count} entries)")
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
            "oldest_entry": min(
                (ts for _, ts in self._cache.values()),
                default=None
            )
        }
