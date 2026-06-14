# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Global Weather Client - Interface for international weather data via OpenWeatherMap API.
Provides weather forecasts for cities worldwide.

Features:
- In-memory caching with configurable TTL (default 1 hour)
- Timeout protection with graceful fallback
- Multi-language support (default: Traditional Chinese)
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

logger = get_configured_logger("global_weather_client")

# OpenWeatherMap API endpoint
OWM_API_URL = "https://api.openweathermap.org/data/2.5/weather"


class GlobalWeatherClient:
    """
    Client for OpenWeatherMap API operations.

    Provides global weather data as Tier 6 enrichment source.
    Supports cities worldwide with multi-language responses.

    Features:
    - Caching: Reduces API calls, improves latency (1 hour TTL)
    - Timeout: Prevents slow API calls from blocking pipeline
    - Temperature units: Celsius (metric)
    """

    def __init__(self):
        """Initialize Global Weather client."""
        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        owm_config = tier_6_config.get("openweathermap", {})

        self._enabled = owm_config.get("enabled", False)
        self._timeout = owm_config.get("timeout", 5.0)
        self._language = owm_config.get("language", "zh_tw")

        # API key from config or environment
        self._api_key = owm_config.get("api_key") or os.getenv("OPENWEATHERMAP_API_KEY")

        # Cache configuration
        cache_config = owm_config.get("cache", {})
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 1))
        self._cache_max_size = cache_config.get("max_size", 100)

        if not self._api_key:
            logger.warning("OpenWeatherMap API key not configured. Set OPENWEATHERMAP_API_KEY environment variable.")
            self._enabled = False

        logger.info(
            f"Initialized GlobalWeatherClient (enabled={self._enabled}, "
            f"cache={self._cache_enabled}, ttl={self._cache_ttl})"
        )

    async def search(
        self,
        city: str,
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get weather data for a city.

        Args:
            city: City name (e.g., "Tokyo", "New York", "London")
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not self._enabled:
            logger.debug("Global Weather client disabled or API key not configured")
            return []

        timeout = timeout or self._timeout

        # Normalize city name
        city = city.strip()

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = city.lower()
            if self._cache_enabled and cache_key in self._cache:
                cached_result, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"Global Weather cache HIT for city: '{city}'")
                    results = [cached_result]
                else:
                    del self._cache[cache_key]
                    logger.debug(f"Global Weather cache EXPIRED for city: '{city}'")

            # Cache miss - fetch data with timeout
            if not cache_hit:
                try:
                    result = await asyncio.wait_for(
                        self._fetch_weather_data(city),
                        timeout=timeout
                    )

                    if result:
                        results = [result]
                        # Update cache
                        if self._cache_enabled:
                            self._update_cache(cache_key, result)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"Global Weather TIMEOUT after {timeout}s for city: '{city}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_result, _ = self._cache[cache_key]
                        logger.info(f"Returning stale Global Weather cache for '{city}'")
                        results = [stale_result]

            return results

        except Exception as e:
            logger.error(f"Error during Global Weather fetch: {e}", exc_info=True)
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
                        source_type="openweathermap",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"city": city}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log Global Weather analytics: {e}")

    async def _fetch_weather_data(self, city: str) -> Optional[Dict[str, Any]]:
        """
        Fetch weather data from OpenWeatherMap API.

        Args:
            city: City name

        Returns:
            Dict with weather data or None if not found
        """
        params = {
            "q": city,
            "appid": self._api_key,
            "units": "metric",  # Celsius
            "lang": self._language
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    OWM_API_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as response:
                    if response.status == 404:
                        logger.debug(f"Global Weather: City '{city}' not found")
                        return None
                    elif response.status != 200:
                        logger.warning(f"OpenWeatherMap API returned status {response.status}")
                        return None

                    data = await response.json()

                    # Extract weather information
                    city_name = data.get("name", city)
                    country = data.get("sys", {}).get("country", "")

                    # Main weather data
                    main = data.get("main", {})
                    temp = main.get("temp", "")
                    feels_like = main.get("feels_like", "")
                    humidity = main.get("humidity", "")
                    temp_min = main.get("temp_min", "")
                    temp_max = main.get("temp_max", "")

                    # Weather description
                    weather_list = data.get("weather", [])
                    description = weather_list[0].get("description", "") if weather_list else ""
                    icon = weather_list[0].get("icon", "") if weather_list else ""

                    # Wind
                    wind = data.get("wind", {})
                    wind_speed = wind.get("speed", "")

                    # Build snippet
                    snippet_parts = []
                    if description:
                        snippet_parts.append(f"天氣: {description}")
                    if temp:
                        snippet_parts.append(f"溫度: {temp:.1f}°C")
                    if temp_min and temp_max:
                        snippet_parts.append(f"最低/最高: {temp_min:.1f}°C / {temp_max:.1f}°C")
                    if humidity:
                        snippet_parts.append(f"濕度: {humidity}%")
                    if wind_speed:
                        snippet_parts.append(f"風速: {wind_speed} m/s")

                    snippet = " | ".join(snippet_parts) if snippet_parts else "無資料"

                    # Location label
                    location_label = f"{city_name}, {country}" if country else city_name

                    return {
                        'title': f"[國際天氣] {location_label}",
                        'snippet': snippet,
                        'link': f"https://openweathermap.org/city/{data.get('id', '')}",
                        'tier': 6,
                        'type': 'weather_global',
                        'source': 'openweathermap'
                    }

        except aiohttp.ClientError as e:
            logger.warning(f"OpenWeatherMap API client error: {e}")
            return None
        except Exception as e:
            logger.error(f"OpenWeatherMap API error: {e}")
            return None

    def _update_cache(self, cache_key: str, result: Dict) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key (city name, lowercase)
            result: Weather data to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug("Global Weather cache evicted oldest entry")

        self._cache[cache_key] = (result, datetime.now())
        logger.debug(f"Global Weather cache UPDATED: '{cache_key}'")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Global Weather cache cleared ({count} entries)")
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
        Check if Global Weather client is available and enabled.

        Returns:
            True if client can be used
        """
        return self._enabled and bool(self._api_key)
