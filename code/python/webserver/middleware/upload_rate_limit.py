"""Per-user rate limiting for private-document uploads.

Guards POST /api/user/upload with an in-memory sliding-window counter keyed by
authenticated user_id (NOT IP). The limit is read from
config/user_data.yaml -> security.max_uploads_per_hour (default 20).

Why a separate middleware (not RATE_LIMIT_RULES in rate_limit.py):
rate_limit_middleware runs BEFORE auth_middleware, so it has no request['user']
and can only key by IP. Per docs/decisions.md:175-178 authenticated users must be
keyed by user_id (precise; unaffected by shared IPs). This middleware is therefore
registered AFTER auth_middleware (see webserver/middleware/__init__.py), at which
point request['user'] is populated.

Counters are in-memory (mirrors rate_limit.py). On restart they reset, and in a
multi-worker deployment each worker keeps its own window — so the effective limit
scales with worker count. Acceptable for the current single-instance deployment;
revisit (shared store, e.g. Redis) if scaled out.
"""

import time
import logging
import os
from pathlib import Path
from collections import defaultdict, deque

import yaml
from aiohttp import web

logger = logging.getLogger(__name__)

# Only this endpoint is guarded.
UPLOAD_PATH = '/api/user/upload'
UPLOAD_METHOD = 'POST'

_DEFAULT_MAX_UPLOADS_PER_HOUR = 20
_WINDOW_SECONDS = 3600

# Sliding-window store: key -> deque of timestamps.
_windows: dict = defaultdict(deque)

# Cache the config-derived limit so we don't re-read the YAML on every request.
_cached_limit = None


def _resolve_config_path() -> Path:
    """Locate config/user_data.yaml, mirroring UserDataManager._load_config."""
    config_dir = os.environ.get('NLWEB_CONFIG_DIR')
    if config_dir:
        return Path(config_dir) / "user_data.yaml"
    # webserver/middleware/upload_rate_limit.py -> repo root is 4 parents up.
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    return project_root / "config" / "user_data.yaml"


def _get_upload_limit() -> tuple:
    """Return (max_uploads, window_seconds), reading max from user_data.yaml.

    Falls back to the default on any read/parse error, logging a warning so the
    degradation is visible (no silent fail).
    """
    global _cached_limit
    if _cached_limit is not None:
        return _cached_limit

    max_uploads = _DEFAULT_MAX_UPLOADS_PER_HOUR
    try:
        config_path = _resolve_config_path()
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        value = config.get('security', {}).get('max_uploads_per_hour')
        if isinstance(value, int) and value > 0:
            max_uploads = value
        else:
            logger.warning(
                "max_uploads_per_hour missing/invalid in %s (got %r); "
                "falling back to default %d",
                config_path, value, _DEFAULT_MAX_UPLOADS_PER_HOUR,
            )
    except Exception as e:
        logger.warning(
            "Failed to read max_uploads_per_hour from config (%s); "
            "falling back to default %d", e, _DEFAULT_MAX_UPLOADS_PER_HOUR,
        )

    _cached_limit = (max_uploads, _WINDOW_SECONDS)
    return _cached_limit


def _check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Return True if allowed, False if rate limit exceeded."""
    now = time.monotonic()
    cutoff = now - window_seconds
    q = _windows[key]

    # Evict expired entries
    while q and q[0] < cutoff:
        q.popleft()

    if len(q) >= max_requests:
        return False

    q.append(now)
    return True


@web.middleware
async def upload_rate_limit_middleware(request: web.Request, handler):
    """Per-user sliding-window limit on POST /api/user/upload."""
    if request.path != UPLOAD_PATH or request.method != UPLOAD_METHOD:
        return await handler(request)

    user = request.get('user')
    user_id = user.get('id') if user else None
    if not user_id:
        # auth_middleware should have rejected this already; if somehow not,
        # don't apply a per-user limit (nothing to key on). Let the handler's
        # own auth check return 401.
        return await handler(request)

    max_uploads, window_seconds = _get_upload_limit()
    key = f"{user_id}:upload"

    if not _check_rate_limit(key, max_uploads, window_seconds):
        logger.warning(f"Upload rate limit exceeded for user {user_id}")
        return web.json_response(
            {
                'error': f'Too many uploads. Limit: {max_uploads} per hour.',
                'type': 'rate_limit_exceeded',
            },
            status=429,
            headers={'Retry-After': str(window_seconds)},
        )

    return await handler(request)
