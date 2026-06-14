"""Slot-based in-memory concurrency limiter for /ask and Deep Research endpoints.

Each active request occupies a slot recorded as {request_id: start_timestamp}.
Zombie slots (older than ZOMBIE_TTL_SECONDS) are cleaned on every check.

Thread-safety: Not needed — aiohttp runs on a single event loop. try_acquire()
is synchronous with no await between the check and the set, so it is atomic.

Integration: Step 4 (P1-1b) will call try_acquire() in api.py with try/finally
to guarantee release() on all code paths including exceptions.
"""

import time
import logging

logger = logging.getLogger(__name__)

# ── Default limits from spec (P1-1) ───────────────────────────────────────────
DR_USER_LIMIT = 1           # Deep Research: 1 concurrent per user
DR_IP_LIMIT = 3             # Deep Research: 3 concurrent per IP (unauthenticated only)
SEARCH_SESSION_LIMIT = 5    # General search: 5 concurrent per session
SEARCH_IP_LIMIT = 10        # General search: 10 concurrent per IP (unauthenticated only)
ZOMBIE_TTL_SECONDS = 300    # 5 minutes TTL — longest reasonable request duration


class ConcurrencyLimitExceeded(Exception):
    """Raised when concurrency limit is exceeded."""

    def __init__(self, key: str, limit: int):
        self.key = key
        self.limit = limit
        super().__init__(f"Concurrency limit {limit} exceeded for key '{key}'")


class ConcurrencyLimiter:
    """
    In-memory slot-based concurrency limiter.

    Tracks active requests per key. Each slot records a monotonic start
    timestamp. Zombie slots (older than ZOMBIE_TTL_SECONDS) are cleaned on
    every try_acquire() and active_count() call.

    Usage (Step 4 will wire this into api.py):
        limiter = ConcurrencyLimiter.get_instance()
        acquired = limiter.try_acquire(key, request_id, limit)
        if not acquired:
            return web.json_response({...}, status=429)
        try:
            ...
        finally:
            limiter.release(key, request_id)
    """

    _instance = None

    @classmethod
    def get_instance(cls) -> 'ConcurrencyLimiter':
        """Return the process-wide singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # key -> {request_id: start_timestamp (monotonic)}
        self._slots: dict[str, dict[str, float]] = {}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _cleanup_zombies(self, key: str) -> None:
        """Remove slots older than ZOMBIE_TTL_SECONDS for the given key.

        Protects against ghost locks caused by coroutine hangs or hard crashes
        where the finally block never ran and release() was never called.
        """
        slots = self._slots.get(key)
        if not slots:
            return
        now = time.monotonic()
        cleaned = {
            req_id: ts
            for req_id, ts in slots.items()
            if now - ts <= ZOMBIE_TTL_SECONDS
        }
        removed = len(slots) - len(cleaned)
        if removed:
            logger.warning(
                "ConcurrencyLimiter: removed %d zombie slot(s) for key '%s'",
                removed, key,
            )
        self._slots[key] = cleaned

    # ── Public API ─────────────────────────────────────────────────────────────

    def try_acquire(self, key: str, request_id: str, limit: int) -> bool:
        """Try to acquire a concurrency slot.

        Returns True if the slot was acquired (request may proceed).
        Returns False if the limit is already reached (caller should return 429).
        Cleans zombie slots before checking the current count.
        """
        # 1. Initialise bucket if first request for this key
        if key not in self._slots:
            self._slots[key] = {}

        # 2. Clean up zombies before counting
        self._cleanup_zombies(key)

        slots = self._slots[key]

        # 3. Check limit
        if len(slots) >= limit:
            logger.warning(
                "ConcurrencyLimiter: limit %d reached for key '%s' (active=%d)",
                limit, key, len(slots),
            )
            return False

        # 4. Acquire slot — record start timestamp
        slots[request_id] = time.monotonic()
        logger.debug(
            "ConcurrencyLimiter: acquired slot for key '%s' request_id='%s' (active=%d/%d)",
            key, request_id, len(slots), limit,
        )
        return True

    def release(self, key: str, request_id: str) -> None:
        """Release a slot.

        Idempotent — safe to call multiple times or when the slot does not
        exist (e.g., was already cleaned as a zombie).
        """
        self._slots.get(key, {}).pop(request_id, None)
        logger.debug(
            "ConcurrencyLimiter: released slot for key '%s' request_id='%s'",
            key, request_id,
        )

    def active_count(self, key: str) -> int:
        """Return current active slot count for the key after zombie cleanup."""
        self._cleanup_zombies(key)
        return len(self._slots.get(key, {}))
