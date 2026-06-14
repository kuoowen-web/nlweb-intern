# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
yFinance Client - Interface for global stock price data via yfinance library.
Provides real-time stock quotes for US, HK, and other global markets.

Features:
- In-memory caching with configurable TTL (default 15 minutes)
- Timeout protection with graceful fallback
- Fundamental data (P/E ratio, market cap) when available
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from core.config import CONFIG
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("yfinance_client")

# Try to import yfinance library
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance library not available. Install with: pip install yfinance")


class YfinanceClient:
    """
    Client for yFinance API operations.

    Provides global stock price data as Tier 6 enrichment source.
    Supports US stocks (AAPL, NVDA), HK stocks (0700.HK), and more.

    Features:
    - Caching: Reduces API calls, improves latency (15 min TTL for stock data)
    - Timeout: Prevents slow API calls from blocking pipeline
    - Fundamentals: Optional P/E ratio, market cap, etc.
    """

    def __init__(self):
        """Initialize yFinance client."""
        if not YFINANCE_AVAILABLE:
            logger.error("yfinance library not installed. YfinanceClient will return empty results.")
            self._enabled = False
            return

        # Get configuration from config_reasoning.yaml
        tier_6_config = CONFIG.reasoning_params.get("tier_6", {})
        yf_config = tier_6_config.get("yfinance", {})

        self._enabled = yf_config.get("enabled", False)
        self._timeout = yf_config.get("timeout", 5.0)
        self._include_fundamentals = yf_config.get("include_fundamentals", True)

        # Cache configuration
        cache_config = yf_config.get("cache", {})
        self._cache: Dict[str, Tuple[Dict, datetime]] = {}
        self._cache_enabled = cache_config.get("enabled", True)
        self._cache_ttl = timedelta(hours=cache_config.get("ttl_hours", 0.25))  # 15 min default
        self._cache_max_size = cache_config.get("max_size", 100)

        logger.info(
            f"Initialized YfinanceClient (enabled={self._enabled}, "
            f"cache={self._cache_enabled}, ttl={self._cache_ttl})"
        )

    async def search(
        self,
        symbol: str,
        timeout: float = None,
        query_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get stock price data for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "NVDA", "AAPL", "0700.HK")
            timeout: Timeout in seconds (defaults to config value)
            query_id: Optional query ID for analytics logging

        Returns:
            List of dicts with keys: title, snippet, link, tier, type, source
        """
        if not YFINANCE_AVAILABLE or not self._enabled:
            logger.debug("yFinance client disabled or library not available")
            return []

        timeout = timeout or self._timeout

        # Normalize symbol
        symbol = symbol.upper().strip()

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
                    logger.info(f"yFinance cache HIT for symbol: '{symbol}'")
                    results = [cached_result]
                else:
                    del self._cache[cache_key]
                    logger.debug(f"yFinance cache EXPIRED for symbol: '{symbol}'")

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
                    logger.warning(f"yFinance TIMEOUT after {timeout}s for symbol: '{symbol}'")

                    # Try to return stale cache as fallback
                    if cache_key in self._cache:
                        stale_result, _ = self._cache[cache_key]
                        logger.info(f"Returning stale yFinance cache for '{symbol}'")
                        results = [stale_result]

            return results

        except Exception as e:
            logger.error(f"Error during yFinance fetch: {e}", exc_info=True)
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
                        source_type="yfinance",
                        cache_hit=cache_hit,
                        latency_ms=latency_ms,
                        timeout_occurred=timeout_occurred,
                        result_count=len(results),
                        metadata={"symbol": symbol}
                    )
                except Exception as e:
                    logger.debug(f"Failed to log yFinance analytics: {e}")

    async def _fetch_stock_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch stock data from yFinance.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Dict with stock data or None if not found
        """
        loop = asyncio.get_event_loop()

        # Run synchronous yfinance call in thread pool
        ticker = await loop.run_in_executor(
            None,
            lambda: yf.Ticker(symbol)
        )

        # Get fast info (uses optimized API)
        try:
            info = await loop.run_in_executor(
                None,
                lambda: ticker.fast_info
            )
        except Exception as e:
            logger.warning(f"yFinance fast_info failed for '{symbol}': {e}")
            return None

        # Check if we got valid data
        try:
            last_price = info.last_price
            if last_price is None:
                logger.warning(f"yFinance: No price data for symbol '{symbol}'")
                return None
        except (AttributeError, KeyError):
            logger.warning(f"yFinance: Invalid symbol '{symbol}'")
            return None

        # Build result
        change = getattr(info, 'last_price', 0) - getattr(info, 'previous_close', 0)
        change_pct = (change / info.previous_close * 100) if info.previous_close else 0
        change_sign = "+" if change >= 0 else ""

        # Format snippet
        snippet = f"最新價: ${last_price:,.2f} | 漲跌: {change_sign}{change:,.2f} ({change_sign}{change_pct:.2f}%)"

        # Add fundamentals if enabled
        if self._include_fundamentals:
            try:
                full_info = await loop.run_in_executor(
                    None,
                    lambda: ticker.info
                )
                pe_ratio = full_info.get('trailingPE')
                market_cap = full_info.get('marketCap')
                company_name = full_info.get('shortName', symbol)

                if pe_ratio:
                    snippet += f" | 本益比: {pe_ratio:.1f}"
                if market_cap:
                    if market_cap >= 1e12:
                        snippet += f" | 市值: ${market_cap/1e12:.2f}T"
                    elif market_cap >= 1e9:
                        snippet += f" | 市值: ${market_cap/1e9:.2f}B"
            except Exception:
                company_name = symbol

        else:
            company_name = symbol

        return {
            'title': f"[全球股市] {company_name} ({symbol})",
            'snippet': snippet,
            'link': f"https://finance.yahoo.com/quote/{symbol}",
            'tier': 6,
            'type': 'stock_global',
            'source': 'yfinance'
        }

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
            logger.debug(f"yFinance cache evicted oldest entry")

        self._cache[cache_key] = (result, datetime.now())
        logger.debug(f"yFinance cache UPDATED: '{cache_key}'")

    def clear_cache(self) -> int:
        """
        Clear all cached results.

        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"yFinance cache cleared ({count} entries)")
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
        Check if yFinance client is available and enabled.

        Returns:
            True if client can be used
        """
        return YFINANCE_AVAILABLE and self._enabled
