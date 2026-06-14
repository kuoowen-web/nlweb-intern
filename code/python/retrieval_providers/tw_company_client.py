# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Taiwan Company Client - Interface for Taiwan company registration data.
Provides company information from Taiwan government open data.

Features:
- In-memory caching with configurable TTL (default 7 days)
- Timeout protection with graceful fallback
- Search by company name or unified business number
- No external dependencies (pure HTTP)
"""

import asyncio
import time
import re
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("tw_company_client")

# Taiwan Government Open Data API endpoint
# 經濟部商業司 - 公司登記基本資料
MOEA_API_URL = "https://data.gcis.nat.gov.tw/od/data/api/5F64D864-61CB-4D0D-8AD9-492047CC1EA6"


class TwCompanyClient:
    """
    Client for Taiwan company registration data API.

    Provides Taiwan company data as Tier 6 enrichment source.
    Uses Taiwan government open data portal.

    Features:
    - Caching: Reduces API calls, improves latency (7 day TTL for company data)
    - Timeout: Prevents slow API calls from blocking pipeline
    - Flexible search: By company name or unified business number (統一編號)
    """

    def __init__(self):
        """Initialize Taiwan Company client."""
        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        tw_company_config = tier_6_config.get("tw_company", {})

        self._enabled = tw_company_config.get("enabled", False)
        self._timeout = tw_company_config.get("timeout", 5.0)

        # Cache configuration
        cache_config = tw_company_config.get("cache", {})
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 168))  # 7 days
        self._cache_max_size = cache_config.get("max_size", 200)

        logger.info(
            f"Initialized TwCompanyClient (enabled={self._enabled}, "
            f"cache={self._cache_enabled}, ttl={self._cache_ttl})"
        )

    async def search(
        self,
        query: str,
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Search for a Taiwan company by name or unified business number.

        Args:
            query: Company name or unified business number (統一編號)
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not self._enabled:
            logger.debug("Taiwan Company client disabled")
            return []

        timeout = timeout or self._timeout

        # Normalize query
        query = query.strip()

        # Detect if query is a unified business number (8 digits)
        is_ubn = bool(re.match(r'^\d{8}$', query))

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = query
            if self._cache_enabled and cache_key in self._cache:
                cached_result, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"TW Company cache HIT for: '{query}'")
                    results = [cached_result]
                else:
                    del self._cache[cache_key]
                    logger.debug(f"TW Company cache EXPIRED for: '{query}'")

            # Cache miss - fetch data with timeout
            if not cache_hit:
                try:
                    result = await asyncio.wait_for(
                        self._fetch_company_data(query, is_ubn),
                        timeout=timeout
                    )

                    if result:
                        results = [result]
                        # Update cache
                        if self._cache_enabled:
                            self._update_cache(cache_key, result)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"TW Company TIMEOUT after {timeout}s for: '{query}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_result, _ = self._cache[cache_key]
                        logger.info(f"Returning stale TW Company cache for '{query}'")
                        results = [stale_result]

            return results

        except Exception as e:
            logger.error(f"Error during TW Company fetch: {e}", exc_info=True)
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
                        source_type="tw_company",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"query": query, "is_ubn": is_ubn}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log TW Company analytics: {e}")

    async def _fetch_company_data(
        self,
        query: str,
        is_ubn: bool
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch company data from Taiwan government API.

        Args:
            query: Company name or unified business number
            is_ubn: True if query is a unified business number

        Returns:
            Dict with company data or None if not found
        """
        # Build API parameters
        params = {
            "$format": "json",
            "$top": "1"
        }

        if is_ubn:
            # Search by unified business number
            params["$filter"] = f"Business_Accounting_NO eq {query}"
        else:
            # Search by company name (contains)
            params["$filter"] = f"contains(Company_Name,'{query}')"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    MOEA_API_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"TW Company API returned status {response.status}")
                        return None

                    data = await response.json()

                    # Check if we got results
                    if not data or len(data) == 0:
                        logger.debug(f"TW Company: No results for '{query}'")
                        return None

                    # Get first result
                    company = data[0]

                    # Extract fields
                    company_name = company.get("Company_Name", query)
                    ubn = company.get("Business_Accounting_NO", "")
                    capital = company.get("Capital_Stock_Amount", "")
                    representative = company.get("Responsible_Name", "")
                    address = company.get("Company_Location", "")
                    status = company.get("Company_Status_Desc", "")
                    established_date = company.get("Approved_Date", "")

                    # Format capital (add commas)
                    if capital:
                        try:
                            capital_int = int(capital)
                            if capital_int >= 100000000:  # 億
                                capital_formatted = f"{capital_int / 100000000:.2f}億"
                            elif capital_int >= 10000:  # 萬
                                capital_formatted = f"{capital_int / 10000:.0f}萬"
                            else:
                                capital_formatted = f"{capital_int:,}"
                        except ValueError:
                            capital_formatted = capital
                    else:
                        capital_formatted = ""

                    # Build snippet
                    snippet_parts = []
                    if ubn:
                        snippet_parts.append(f"統編: {ubn}")
                    if capital_formatted:
                        snippet_parts.append(f"資本額: {capital_formatted}")
                    if representative:
                        snippet_parts.append(f"代表人: {representative}")
                    if status:
                        snippet_parts.append(f"狀態: {status}")

                    snippet = " | ".join(snippet_parts) if snippet_parts else "無詳細資料"

                    # Add address if available
                    if address:
                        snippet += f"\n地址: {address}"

                    return {
                        'title': f"[公司登記] {company_name}",
                        'snippet': snippet,
                        'link': f"https://findbiz.nat.gov.tw/fts/query/QueryBar/queryInit.do?banNo={ubn}" if ubn else "https://findbiz.nat.gov.tw/",
                        'tier': 6,
                        'type': 'company_tw',
                        'source': 'moea'
                    }

        except aiohttp.ClientError as e:
            logger.warning(f"TW Company API client error: {e}")
            return None
        except Exception as e:
            logger.error(f"TW Company API error: {e}")
            return None

    def _update_cache(self, cache_key: str, result: Dict) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key
            result: Company data to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug("TW Company cache evicted oldest entry")

        self._cache[cache_key] = (result, datetime.now())
        logger.debug(f"TW Company cache UPDATED: '{cache_key}'")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"TW Company cache cleared ({count} entries)")
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
        Check if Taiwan Company client is available and enabled.

        Returns:
            True if client can be used
        """
        return self._enabled
