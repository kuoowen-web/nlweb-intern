"""
Results cache for sharing retrieval results between list and generate modes.
Allows generate mode to reuse the exact same ranked results from list mode.
"""

import threading
import time
from typing import Dict, Optional, List, Any
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("results_cache")


class ResultsCache:
    """
    Thread-safe cache for storing ranked search results by conversation_id.
    Allows generate mode to reuse results from list mode instead of doing separate retrieval.
    """

    def __init__(self, ttl_seconds: int = 300):  # 5 minutes default TTL
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.ttl_seconds = ttl_seconds
        logger.info(f"ResultsCache initialized with TTL={ttl_seconds}s")

    def _make_key(self, conversation_id: str, user_id: Optional[str]) -> Optional[str]:
        """Build the per-user cache key, or None when caching must be skipped.

        CORE-1 (D-2026-07-20 規則 1)：cache key **必須併入 trusted user_id**，
        否則不同 user 撞相同 conversation_id 會互撈到彼此含私有文件的排序結果。

        弱 fallback key 一律不 cache（return None）：
          - conversation_id 為空 → 舊行為 fallback 到 query+site，碰撞率極高，
            直接不 cache（不用可碰撞 key）。
          - user_id 為 None/空 → 無可信身分可歸屬，不能安全共享，不 cache。

        key 格式 f"{user_id}:{conversation_id}"。
        """
        if not conversation_id:
            return None
        if not user_id:
            return None
        return f"{user_id}:{conversation_id}"

    def store(self, conversation_id: str, results: List[Any], query: str,
              user_id: Optional[str] = None) -> None:
        """
        Store ranked results for a conversation, isolated per user.

        Args:
            conversation_id: Unique conversation identifier
            results: List of ranked answer objects (final_ranked_answers)
            query: The search query
            user_id: Trusted user id (from _resolve_trusted_identity). Required
                     for caching — a missing user_id or empty conversation_id
                     skips caching entirely (CORE-1).
        """
        key = self._make_key(conversation_id, user_id)
        if key is None:
            # 弱 fallback key（空 conversation_id 或無 user_id）→ 不 cache。
            logger.debug(
                "Skip caching results (weak key): "
                f"conversation_id={'<empty>' if not conversation_id else conversation_id}, "
                f"user_id={'<missing>' if not user_id else user_id}"
            )
            return
        with self._lock:
            self._cache[key] = {
                'final_ranked_answers': results,
                'query': query,
                'timestamp': time.time()
            }
            logger.info(f"Cached {len(results)} results for key {key}")
            self._cleanup_expired()

    def retrieve(self, conversation_id: str,
                 user_id: Optional[str] = None) -> Optional[List[Any]]:
        """
        Retrieve cached results for a conversation, isolated per user.

        Args:
            conversation_id: Unique conversation identifier
            user_id: Trusted user id (from _resolve_trusted_identity). A missing
                     user_id or empty conversation_id always misses (CORE-1).

        Returns:
            List of ranked results if found and not expired, None otherwise
        """
        key = self._make_key(conversation_id, user_id)
        if key is None:
            # 弱 fallback key 從不 cache，故必 miss。
            return None
        with self._lock:
            if key not in self._cache:
                logger.debug(f"No cached results for key {key}")
                return None

            entry = self._cache[key]

            # Check if expired
            age = time.time() - entry['timestamp']
            if age > self.ttl_seconds:
                logger.info(f"Cached results for {key} expired (age={age:.1f}s)")
                del self._cache[key]
                return None

            logger.info(f"Retrieved {len(entry['final_ranked_answers'])} cached results for key {key}")
            return entry['final_ranked_answers']

    def _cleanup_expired(self) -> None:
        """Remove expired entries from cache."""
        current_time = time.time()
        expired = [
            cid for cid, entry in self._cache.items()
            if current_time - entry['timestamp'] > self.ttl_seconds
        ]
        for cid in expired:
            del self._cache[cid]
            logger.debug(f"Removed expired cache entry for key {cid}")

    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics for monitoring."""
        with self._lock:
            return {
                'total_entries': len(self._cache),
                'total_results': sum(len(e['final_ranked_answers']) for e in self._cache.values())
            }


# Global singleton instance
_results_cache = ResultsCache()


def get_results_cache() -> ResultsCache:
    """Get the global results cache instance."""
    return _results_cache
