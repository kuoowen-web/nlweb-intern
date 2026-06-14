# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
CWB Weather Client - Interface for Taiwan weather data via Central Weather Bureau API.
Provides weather forecasts for Taiwan cities and townships.

Features:
- In-memory caching with configurable TTL (default 1 hour)
- Timeout protection with graceful fallback
- Township-level forecast data
- No external dependencies (pure HTTP)
"""

import asyncio
import os
import time
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("cwb_weather_client")

# CWB Open Data API endpoint
CWB_API_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

# Township forecast dataset ID (全台鄉鎮天氣預報)
TOWNSHIP_FORECAST_ID = "F-D0047-091"

# City name mapping (Chinese to location code)
CITY_MAPPING = {
    "台北": "臺北市",
    "臺北": "臺北市",
    "台北市": "臺北市",
    "臺北市": "臺北市",
    "新北": "新北市",
    "新北市": "新北市",
    "桃園": "桃園市",
    "桃園市": "桃園市",
    "台中": "臺中市",
    "臺中": "臺中市",
    "台中市": "臺中市",
    "臺中市": "臺中市",
    "台南": "臺南市",
    "臺南": "臺南市",
    "台南市": "臺南市",
    "臺南市": "臺南市",
    "高雄": "高雄市",
    "高雄市": "高雄市",
    "基隆": "基隆市",
    "基隆市": "基隆市",
    "新竹": "新竹市",
    "新竹市": "新竹市",
    "新竹縣": "新竹縣",
    "苗栗": "苗栗縣",
    "苗栗縣": "苗栗縣",
    "彰化": "彰化縣",
    "彰化縣": "彰化縣",
    "南投": "南投縣",
    "南投縣": "南投縣",
    "雲林": "雲林縣",
    "雲林縣": "雲林縣",
    "嘉義": "嘉義市",
    "嘉義市": "嘉義市",
    "嘉義縣": "嘉義縣",
    "屏東": "屏東縣",
    "屏東縣": "屏東縣",
    "宜蘭": "宜蘭縣",
    "宜蘭縣": "宜蘭縣",
    "花蓮": "花蓮縣",
    "花蓮縣": "花蓮縣",
    "台東": "臺東縣",
    "臺東": "臺東縣",
    "台東縣": "臺東縣",
    "臺東縣": "臺東縣",
    "澎湖": "澎湖縣",
    "澎湖縣": "澎湖縣",
    "金門": "金門縣",
    "金門縣": "金門縣",
    "連江": "連江縣",
    "連江縣": "連江縣",
    "馬祖": "連江縣",
}


class CwbWeatherClient:
    """
    Client for CWB (Central Weather Bureau) API operations.

    Provides Taiwan weather forecast data as Tier 6 enrichment source.
    Supports city and township level forecasts.

    Features:
    - Caching: Reduces API calls, improves latency (1 hour TTL)
    - Timeout: Prevents slow API calls from blocking pipeline
    - Location mapping: Handles various Chinese location name formats
    """

    def __init__(self):
        """Initialize CWB Weather client."""
        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        cwb_config = tier_6_config.get("cwb_weather", {})

        self._enabled = cwb_config.get("enabled", False)
        self._timeout = cwb_config.get("timeout", 5.0)

        # API key from config or environment
        self._api_key = cwb_config.get("api_key") or os.getenv("CWB_API_KEY")

        # Cache configuration
        cache_config = cwb_config.get("cache", {})
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 1))
        self._cache_max_size = cache_config.get("max_size", 100)

        if not self._api_key:
            logger.warning("CWB API key not configured. Set CWB_API_KEY environment variable.")
            self._enabled = False

        logger.info(
            f"Initialized CwbWeatherClient (enabled={self._enabled}, "
            f"cache={self._cache_enabled}, ttl={self._cache_ttl})"
        )

    async def search(
        self,
        location: str,
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get weather forecast for a Taiwan location.

        Args:
            location: Location name (e.g., "台北", "高雄市", "新竹")
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not self._enabled:
            logger.debug("CWB Weather client disabled or API key not configured")
            return []

        timeout = timeout or self._timeout

        # Normalize location name
        normalized_location = self._normalize_location(location)
        if not normalized_location:
            logger.warning(f"CWB: Unknown location '{location}'")
            return []

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = normalized_location
            if self._cache_enabled and cache_key in self._cache:
                cached_result, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"CWB cache HIT for location: '{normalized_location}'")
                    results = [cached_result]
                else:
                    del self._cache[cache_key]
                    logger.debug(f"CWB cache EXPIRED for location: '{normalized_location}'")

            # Cache miss - fetch data with timeout
            if not cache_hit:
                try:
                    result = await asyncio.wait_for(
                        self._fetch_weather_data(normalized_location),
                        timeout=timeout
                    )

                    if result:
                        results = [result]
                        # Update cache
                        if self._cache_enabled:
                            self._update_cache(cache_key, result)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"CWB TIMEOUT after {timeout}s for location: '{normalized_location}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_result, _ = self._cache[cache_key]
                        logger.info(f"Returning stale CWB cache for '{normalized_location}'")
                        results = [stale_result]

            return results

        except Exception as e:
            logger.error(f"Error during CWB fetch: {e}", exc_info=True)
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
                        source_type="cwb_weather",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"location": normalized_location}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log CWB analytics: {e}")

    def _normalize_location(self, location: str) -> Optional[str]:
        """
        Normalize location name to CWB format.

        Args:
            location: User input location name

        Returns:
            Normalized location name or None if not found
        """
        location = location.strip()

        # Direct mapping
        if location in CITY_MAPPING:
            return CITY_MAPPING[location]

        # Try partial match
        for key, value in CITY_MAPPING.items():
            if key in location or location in key:
                return value

        return None

    async def _fetch_weather_data(self, location: str) -> Optional[Dict[str, Any]]:
        """
        Fetch weather data from CWB API.

        Args:
            location: Normalized location name

        Returns:
            Dict with weather data or None if not found
        """
        url = f"{CWB_API_BASE}/{TOWNSHIP_FORECAST_ID}"
        params = {
            "Authorization": self._api_key,
            "locationName": location,
            "format": "JSON"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"CWB API returned status {response.status}")
                        return None

                    data = await response.json()

                    # Parse response
                    records = data.get("records", {})
                    locations = records.get("locations", [])

                    if not locations:
                        logger.warning(f"CWB: No data for location '{location}'")
                        return None

                    # Get first matching location
                    loc_data = locations[0].get("location", [])
                    if not loc_data:
                        return None

                    # Use city-level data (first location in the list)
                    weather_info = loc_data[0]
                    loc_name = weather_info.get("locationName", location)

                    # Extract weather elements
                    weather_elements = {}
                    for element in weather_info.get("weatherElement", []):
                        element_name = element.get("elementName")
                        time_data = element.get("time", [])
                        if time_data:
                            # Get the first (current/nearest) time period
                            current = time_data[0]
                            element_value = current.get("elementValue", [])
                            if element_value:
                                weather_elements[element_name] = element_value[0].get("value", "")

                    # Build snippet
                    wx = weather_elements.get("Wx", "")  # 天氣現象
                    min_t = weather_elements.get("MinT", "")  # 最低溫
                    max_t = weather_elements.get("MaxT", "")  # 最高溫
                    pop = weather_elements.get("PoP12h", "")  # 降雨機率

                    snippet_parts = []
                    if wx:
                        snippet_parts.append(f"天氣: {wx}")
                    if min_t and max_t:
                        snippet_parts.append(f"溫度: {min_t}-{max_t}°C")
                    if pop:
                        snippet_parts.append(f"降雨機率: {pop}%")

                    snippet = " | ".join(snippet_parts) if snippet_parts else "無資料"

                    return {
                        'title': f"[氣象] {loc_name} 天氣預報",
                        'snippet': snippet,
                        'link': f"https://www.cwa.gov.tw/V8/C/W/County/County.html?CID={location}",
                        'tier': 6,
                        'type': 'weather_tw',
                        'source': 'cwb'
                    }

        except aiohttp.ClientError as e:
            logger.warning(f"CWB API client error: {e}")
            return None
        except Exception as e:
            logger.error(f"CWB API error: {e}")
            return None

    def _update_cache(self, cache_key: str, result: Dict) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key (location)
            result: Weather data to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug("CWB cache evicted oldest entry")

        self._cache[cache_key] = (result, datetime.now())
        logger.debug(f"CWB cache UPDATED: '{cache_key}'")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"CWB cache cleared ({count} entries)")
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
        Check if CWB Weather client is available and enabled.

        Returns:
            True if client can be used
        """
        return self._enabled and bool(self._api_key)
