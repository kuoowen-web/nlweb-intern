"""CORS middleware for aiohttp server.

Production: reads ALLOWED_ORIGINS env var (comma-separated list of allowed origins).
Development: when ALLOWED_ORIGINS is not set, allows localhost/127.0.0.1 origins.

Note: 'Access-Control-Allow-Origin: *' is INCOMPATIBLE with credentials.
We must reflect a specific origin (if allowed) when credentials are involved.
"""

import os
from typing import Optional, Set

from aiohttp import web
import logging

logger = logging.getLogger(__name__)

# Parse allowed origins from environment variable
_ALLOWED_ORIGINS_ENV = os.environ.get('ALLOWED_ORIGINS', '')
ALLOWED_ORIGINS: Set[str] = (
    {o.strip() for o in _ALLOWED_ORIGINS_ENV.split(',') if o.strip()}
    if _ALLOWED_ORIGINS_ENV
    else set()
)

if ALLOWED_ORIGINS:
    logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")
else:
    logger.info("CORS: no ALLOWED_ORIGINS set, allowing localhost in dev mode")


def _resolve_origin(origin: str) -> Optional[str]:
    """Return the origin to echo back, or None if not allowed."""
    if not origin:
        return None
    if ALLOWED_ORIGINS:
        return origin if origin in ALLOWED_ORIGINS else None
    # Dev mode: allow localhost and 127.0.0.1
    if origin.startswith('http://localhost') or origin.startswith('http://127.0.0.1'):
        return origin
    return None


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Handle CORS headers for all requests."""
    config = request.app.get('config', {})
    cors_enabled = config.get('server', {}).get('enable_cors', True)

    if not cors_enabled:
        return await handler(request)

    origin = request.headers.get('Origin', '')
    allowed_origin = _resolve_origin(origin)

    cors_headers = {}
    if allowed_origin:
        cors_headers = {
            'Access-Control-Allow-Origin': allowed_origin,
            'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With',
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Max-Age': '3600',
            'Vary': 'Origin',
        }
    elif origin:
        # Origin present but not allowed — still handle OPTIONS so browser gets proper rejection
        logger.debug(f"CORS: rejected origin {origin!r}")

    # Handle preflight OPTIONS requests
    if request.method == 'OPTIONS':
        return web.Response(status=200, headers=cors_headers)

    # Process request
    try:
        response = await handler(request)
    except web.HTTPException as ex:
        if cors_headers:
            ex.headers.update(cors_headers)
        raise

    if cors_headers:
        response.headers.update(cors_headers)

    return response
