# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Wikidata Client - Interface for global company and entity data via Wikidata SPARQL.
Provides structured data about companies, organizations, and people.

Features:
- In-memory caching with configurable TTL (default 24 hours)
- Timeout protection with graceful fallback
- SPARQL queries for structured entity data
- No external dependencies (pure HTTP)
"""

import asyncio
import time
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("wikidata_client")

# Wikidata SPARQL endpoint
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# User-Agent for Wikidata API (required)
USER_AGENT = "NLWeb/1.0 (https://github.com/anthropics/nlweb; contact@example.com)"


class WikidataClient:
    """
    Client for Wikidata SPARQL API operations.

    Provides global company and entity data as Tier 6 enrichment source.
    Uses SPARQL queries to fetch structured information.

    Features:
    - Caching: Reduces API calls, improves latency (24 hour TTL)
    - Timeout: Prevents slow API calls from blocking pipeline
    - Entity search: Finds companies, organizations, people by name
    """

    def __init__(self):
        """Initialize Wikidata client."""
        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        wikidata_config = tier_6_config.get("wikidata", {})

        self._enabled = wikidata_config.get("enabled", False)
        self._timeout = wikidata_config.get("timeout", 8.0)

        # Cache configuration
        cache_config = wikidata_config.get("cache", {})
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 24))
        self._cache_max_size = cache_config.get("max_size", 200)

        logger.info(
            f"Initialized WikidataClient (enabled={self._enabled}, "
            f"cache={self._cache_enabled}, ttl={self._cache_ttl})"
        )

    async def search(
        self,
        name: str,
        entity_type: str = "company",
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Search for an entity on Wikidata.

        Args:
            name: Entity name (e.g., "Apple", "Microsoft", "Elon Musk")
            entity_type: Type of entity ("company", "person", "organization")
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not self._enabled:
            logger.debug("Wikidata client disabled")
            return []

        timeout = timeout or self._timeout

        # Normalize name
        name = name.strip()

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = f"{entity_type}:{name}"
            if self._cache_enabled and cache_key in self._cache:
                cached_result, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"Wikidata cache HIT for: '{name}'")
                    results = [cached_result]
                else:
                    del self._cache[cache_key]
                    logger.debug(f"Wikidata cache EXPIRED for: '{name}'")

            # Cache miss - fetch data with timeout
            if not cache_hit:
                try:
                    result = await asyncio.wait_for(
                        self._fetch_entity_data(name, entity_type),
                        timeout=timeout
                    )

                    if result:
                        results = [result]
                        # Update cache
                        if self._cache_enabled:
                            self._update_cache(cache_key, result)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"Wikidata TIMEOUT after {timeout}s for: '{name}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_result, _ = self._cache[cache_key]
                        logger.info(f"Returning stale Wikidata cache for '{name}'")
                        results = [stale_result]

            return results

        except Exception as e:
            logger.error(f"Error during Wikidata fetch: {e}", exc_info=True)
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
                        source_type="wikidata",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"name": name, "entity_type": entity_type}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log Wikidata analytics: {e}")

    async def _fetch_entity_data(
        self,
        name: str,
        entity_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch entity data from Wikidata using SPARQL.

        Args:
            name: Entity name
            entity_type: Type of entity

        Returns:
            Dict with entity data or None if not found
        """
        # Build SPARQL query based on entity type
        if entity_type == "company":
            sparql = self._build_company_query(name)
        elif entity_type == "person":
            sparql = self._build_person_query(name)
        else:
            sparql = self._build_generic_query(name)

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "application/sparql-results+json",
                    "User-Agent": USER_AGENT
                }

                async with session.get(
                    WIKIDATA_SPARQL_URL,
                    params={"query": sparql, "format": "json"},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"Wikidata API returned status {response.status}")
                        return None

                    data = await response.json()

                    # Parse results
                    bindings = data.get("results", {}).get("bindings", [])
                    if not bindings:
                        logger.debug(f"Wikidata: No results for '{name}'")
                        return None

                    # Get first result
                    result = bindings[0]

                    # Extract fields
                    entity_label = result.get("itemLabel", {}).get("value", name)
                    description = result.get("itemDescription", {}).get("value", "")
                    entity_uri = result.get("item", {}).get("value", "")

                    # Extract additional properties based on type
                    snippet_parts = []
                    if description:
                        snippet_parts.append(description)

                    # Company-specific fields
                    if entity_type == "company":
                        inception = result.get("inception", {}).get("value", "")
                        if inception:
                            # Format date (YYYY-MM-DD -> YYYY年)
                            year = inception[:4] if len(inception) >= 4 else inception
                            snippet_parts.append(f"成立: {year}年")

                        headquarters = result.get("headquartersLabel", {}).get("value", "")
                        if headquarters:
                            snippet_parts.append(f"總部: {headquarters}")

                        ceo = result.get("ceoLabel", {}).get("value", "")
                        if ceo:
                            snippet_parts.append(f"CEO: {ceo}")

                        industry = result.get("industryLabel", {}).get("value", "")
                        if industry:
                            snippet_parts.append(f"產業: {industry}")

                    # Person-specific fields
                    elif entity_type == "person":
                        birth_date = result.get("birthDate", {}).get("value", "")
                        if birth_date:
                            year = birth_date[:4] if len(birth_date) >= 4 else birth_date
                            snippet_parts.append(f"出生: {year}年")

                        occupation = result.get("occupationLabel", {}).get("value", "")
                        if occupation:
                            snippet_parts.append(f"職業: {occupation}")

                        nationality = result.get("nationalityLabel", {}).get("value", "")
                        if nationality:
                            snippet_parts.append(f"國籍: {nationality}")

                    snippet = " | ".join(snippet_parts) if snippet_parts else "無詳細資料"

                    # Convert Wikidata URI to Wikipedia-style link
                    entity_id = entity_uri.split("/")[-1] if entity_uri else ""
                    link = f"https://www.wikidata.org/wiki/{entity_id}" if entity_id else entity_uri

                    return {
                        'title': f"[Wikidata] {entity_label}",
                        'snippet': snippet,
                        'link': link,
                        'tier': 6,
                        'type': 'company_global',
                        'source': 'wikidata'
                    }

        except aiohttp.ClientError as e:
            logger.warning(f"Wikidata API client error: {e}")
            return None
        except Exception as e:
            logger.error(f"Wikidata API error: {e}")
            return None

    def _build_company_query(self, name: str) -> str:
        """Build SPARQL query for company search."""
        # Escape special characters in name
        escaped_name = name.replace('"', '\\"')

        return f"""
        SELECT ?item ?itemLabel ?itemDescription ?inception ?headquartersLabel ?ceoLabel ?industryLabel
        WHERE {{
            ?item rdfs:label "{escaped_name}"@en .
            ?item wdt:P31/wdt:P279* wd:Q4830453 .  # instance of business enterprise
            OPTIONAL {{ ?item wdt:P571 ?inception . }}
            OPTIONAL {{ ?item wdt:P159 ?headquarters . }}
            OPTIONAL {{ ?item wdt:P169 ?ceo . }}
            OPTIONAL {{ ?item wdt:P452 ?industry . }}
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en" . }}
        }}
        LIMIT 1
        """

    def _build_person_query(self, name: str) -> str:
        """Build SPARQL query for person search."""
        escaped_name = name.replace('"', '\\"')

        return f"""
        SELECT ?item ?itemLabel ?itemDescription ?birthDate ?occupationLabel ?nationalityLabel
        WHERE {{
            ?item rdfs:label "{escaped_name}"@en .
            ?item wdt:P31 wd:Q5 .  # instance of human
            OPTIONAL {{ ?item wdt:P569 ?birthDate . }}
            OPTIONAL {{ ?item wdt:P106 ?occupation . }}
            OPTIONAL {{ ?item wdt:P27 ?nationality . }}
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en" . }}
        }}
        LIMIT 1
        """

    def _build_generic_query(self, name: str) -> str:
        """Build generic SPARQL query for any entity."""
        escaped_name = name.replace('"', '\\"')

        return f"""
        SELECT ?item ?itemLabel ?itemDescription
        WHERE {{
            ?item rdfs:label "{escaped_name}"@en .
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,en" . }}
        }}
        LIMIT 1
        """

    def _update_cache(self, cache_key: str, result: Dict) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key
            result: Entity data to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug("Wikidata cache evicted oldest entry")

        self._cache[cache_key] = (result, datetime.now())
        logger.debug(f"Wikidata cache UPDATED: '{cache_key}'")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Wikidata cache cleared ({count} entries)")
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
            "ttl_hours": self._cache_ttl.total_seconds() / 3600
        }

    def is_available(self) -> bool:
        """
        Check if Wikidata client is available and enabled.

        Returns:
            True if client can be used
        """
        return self._enabled
