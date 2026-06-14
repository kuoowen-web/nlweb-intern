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

    def store(self, conversation_id: str, results: List[Any], query: str) -> None:
        """
        Store ranked results for a conversation.

        Args:
            conversation_id: Unique conversation identifier
            results: List of ranked answer objects (final_ranked_answers)
            query: The search query
        """
        with self._lock:
            self._cache[conversation_id] = {
                'final_ranked_answers': results,
                'query': query,
                'timestamp': time.time()
            }
            logger.info(f"Cached {len(results)} results for conversation {conversation_id}")
            self._cleanup_expired()

    def retrieve(self, conversation_id: str) -> Optional[List[Any]]:
        """
        Retrieve cached results for a conversation.

        Args:
            conversation_id: Unique conversation identifier

        Returns:
            List of ranked results if found and not expired, None otherwise
        """
        with self._lock:
            if conversation_id not in self._cache:
                logger.debug(f"No cached results for conversation {conversation_id}")
                return None

            entry = self._cache[conversation_id]

            # Check if expired
            age = time.time() - entry['timestamp']
            if age > self.ttl_seconds:
                logger.info(f"Cached results for {conversation_id} expired (age={age:.1f}s)")
                del self._cache[conversation_id]
                return None

            logger.info(f"Retrieved {len(entry['final_ranked_answers'])} cached results for conversation {conversation_id}")
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
            logger.debug(f"Removed expired cache entry for conversation {cid}")

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
