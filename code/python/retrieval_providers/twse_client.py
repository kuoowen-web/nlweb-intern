# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
TWSE/TPEX Client - Interface for Taiwan stock price data.
Provides real-time stock quotes for Taiwan Stock Exchange (TWSE) and
Taipei Exchange (TPEX/OTC) markets.

Features:
- In-memory caching with configurable TTL (default 5 minutes)
- Timeout protection with graceful fallback
- Automatic exchange detection (TWSE vs TPEX)
- No external dependencies (pure HTTP)
"""

import asyncio
import time
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("twse_client")

# TWSE/TPEX API endpoints
TWSE_API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TPEX_API_URL = "https://mis.tpex.org.tw/stock/api/getStockInfo.jsp"


class TwseClient:
    """
    Client for TWSE/TPEX API operations.

    Provides Taiwan stock price data as Tier 6 enrichment source.
    Supports both listed (上市) and OTC (上櫃) stocks.

    Features:
    - Caching: Reduces API calls, improves latency (5 min TTL for stock data)
    - Timeout: Prevents slow API calls from blocking pipeline
    - Auto-detection: Tries TWSE first, falls back to TPEX
    """

    def __init__(self):
        """Initialize TWSE client."""
        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        twse_config = tier_6_config.get("twse", {})

        self._enabled = twse_config.get("enabled", False)
        self._timeout = twse_config.get("timeout", 3.0)

        # Cache configuration
        cache_config = twse_config.get("cache", {})
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 0.083))  # 5 min default
        self._cache_max_size = cache_config.get("max_size", 200)

        logger.info(
            f"Initialized TwseClient (enabled={self._enabled}, "
            f"cache={self._cache_enabled}, ttl={self._cache_ttl})"
        )

    async def search(
        self,
        symbol: str,
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get stock price data for a Taiwan stock symbol.

        Args:
            symbol: Stock code (e.g., "2330", "2317", "6547")
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not self._enabled:
            logger.debug("TWSE client disabled")
            return []

        timeout = timeout or self._timeout

        # Normalize symbol (remove any non-numeric characters except for special suffixes)
        symbol = symbol.strip()

        # Track metrics for analytics
        start_time = time.time()
        cache_hit = False
        timeout_occurred = False
        results = []

        try:
            # Check cache first
            cache_key = symbol
            if self._cache_enabled and cache_key in self._cache:
                cached_result, timestamp = self._cache[cache_key]
                if datetime.now() - timestamp < self._cache_ttl:
                    cache_hit = True
                    logger.info(f"TWSE cache HIT for symbol: '{symbol}'")
                    results = [cached_result]
                else:
                    del self._cache[cache_key]
                    logger.debug(f"TWSE cache EXPIRED for symbol: '{symbol}'")

            # Cache miss - fetch data with timeout
            if not cache_hit:
                try:
                    result = await asyncio.wait_for(
                        self._fetch_stock_data(symbol),
                        timeout=timeout
                    )

                    if result:
                        results = [result]
                        # Update cache
                        if self._cache_enabled:
                            self._update_cache(cache_key, result)

                except asyncio.TimeoutError:
                    timeout_occurred = True
                    logger.warning(f"TWSE TIMEOUT after {timeout}s for symbol: '{symbol}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_result, _ = self._cache[cache_key]
                        logger.info(f"Returning stale TWSE cache for '{symbol}'")
                        results = [stale_result]

            return results

        except Exception as e:
            logger.error(f"Error during TWSE fetch: {e}", exc_info=True)
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
                        source_type="twse",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"symbol": symbol}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log TWSE analytics: {e}")

    async def _fetch_stock_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch stock data from TWSE/TPEX API.

        Args:
            symbol: Stock code

        Returns:
            Dict with stock data or None if not found
        """
        # Try TWSE first (listed stocks), then TPEX (OTC stocks)
        for api_url, exchange in [(TWSE_API_URL, "TWSE"), (TPEX_API_URL, "TPEX")]:
            result = await self._fetch_from_exchange(symbol, api_url, exchange)
            if result:
                return result

        logger.warning(f"TWSE/TPEX: No data found for symbol '{symbol}'")
        return None

    async def _fetch_from_exchange(
        self,
        symbol: str,
        api_url: str,
        exchange: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch stock data from a specific exchange API.

        Args:
            symbol: Stock code
            api_url: API endpoint URL
            exchange: Exchange name (TWSE or TPEX)

        Returns:
            Dict with stock data or None if not found
        """
        # Build request URL with stock code
        # Format: tse_{symbol}.tw for TWSE, otc_{symbol}.tw for TPEX
        prefix = "tse" if exchange == "TWSE" else "otc"
        ex_ch = f"{prefix}_{symbol}.tw"

        params = {"ex_ch": ex_ch, "json": "1", "delay": "0"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=self._timeout)) as response:
                    if response.status != 200:
                        logger.debug(f"{exchange} API returned status {response.status}")
                        return None

                    data = await response.json()

                    # Check if we got valid data
                    if not data or "msgArray" not in data or not data["msgArray"]:
                        return None

                    stock_info = data["msgArray"][0]

                    # Extract fields
                    stock_name = stock_info.get("n", symbol)  # Name
                    last_price = stock_info.get("z", "-")      # Last trade price
                    yesterday_close = stock_info.get("y", "0") # Yesterday close
                    volume = stock_info.get("v", "0")          # Volume (in lots)

                    # Handle "-" for no trade
                    if last_price == "-":
                        last_price = stock_info.get("o", yesterday_close)  # Use open or yesterday close

                    try:
                        last_price_float = float(last_price)
                        yesterday_float = float(yesterday_close)
                        change = last_price_float - yesterday_float
                        change_pct = (change / yesterday_float * 100) if yesterday_float else 0
                        change_sign = "+" if change >= 0 else ""

                        # Format volume (convert to 張)
                        volume_int = int(float(volume))

                        snippet = (
                            f"最新價: {last_price_float:,.2f} | "
                            f"漲跌: {change_sign}{change:,.2f} ({change_sign}{change_pct:.2f}%) | "
                            f"成交量: {volume_int:,} 張"
                        )
                    except (ValueError, TypeError):
                        snippet = f"最新價: {last_price} | 昨收: {yesterday_close}"

                    exchange_label = "上市" if exchange == "TWSE" else "上櫃"

                    return {
                        'title': f"[台股-{exchange_label}] {stock_name} ({symbol})",
                        'snippet': snippet,
                        'link': f"https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html?stockNo={symbol}",
                        'tier': 6,
                        'type': 'stock_tw',
                        'source': exchange.lower()
                    }

        except aiohttp.ClientError as e:
            logger.debug(f"{exchange} API client error: {e}")
            return None
        except Exception as e:
            logger.debug(f"{exchange} API error: {e}")
            return None

    def _update_cache(self, cache_key: str, result: Dict) -> None:
        """
        Update cache with LRU eviction if needed.

        Args:
            cache_key: Cache key (symbol)
            result: Stock data to cache
        """
        # Evict oldest entry if cache is full
        if len(self._cache) >= self._cache_max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
            logger.debug(f"TWSE cache evicted oldest entry")

        self._cache[cache_key] = (result, datetime.now())
        logger.debug(f"TWSE cache UPDATED: '{cache_key}'")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"TWSE cache cleared ({count} entries)")
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
        Check if TWSE client is available and enabled.

        Returns:
            True if client can be used
        """
        return self._enabled
