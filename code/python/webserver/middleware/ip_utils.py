"""Shared IP-address utilities for middleware and route handlers.

Centralises trusted-proxy logic so that rate limiting, auth routes, and any
future consumer all apply the same X-Forwarded-For policy (BP-5).
"""

import os
from aiohttp import web

# BP-5: Only trust X-Forwarded-For when the direct connection comes from a
# known proxy (Cloudflare, local loopback, etc.).  Configurable via the
# TRUSTED_PROXIES environment variable (comma-separated).
_TRUSTED_PROXIES: set[str] = set(
    p.strip()
    for p in os.environ.get('TRUSTED_PROXIES', '127.0.0.1').split(',')
    if p.strip()
)


def get_client_ip(request: web.Request) -> str:
    """Extract the real client IP, only trusting XFF from known proxies (BP-5).

    If the direct peer is a trusted proxy (e.g. Cloudflare, loopback), the
    first address in X-Forwarded-For is returned.  Otherwise, the direct
    peer address is used — preventing spoofing by untrusted clients.
    """
    peername = request.transport.get_extra_info('peername')
    direct_ip = peername[0] if peername else '0.0.0.0'

    if direct_ip in _TRUSTED_PROXIES:
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            return forwarded.split(',')[0].strip()

    return direct_ip
