"""Content-Security-Policy middleware for aiohttp server."""

import secrets

from aiohttp import web

_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}' https://*.clarity.ms https://static.cloudflareinsights.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://*.clarity.ms https://c.bing.com; "
    "connect-src 'self' https://*.clarity.ms https://cloudflareinsights.com; "
    "font-src 'self'; "
    "frame-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'"
)

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"


def _build_csp(nonce: str) -> str:
    """Build CSP header string with the given nonce."""
    return _CSP_TEMPLATE.format(nonce=nonce)


def _apply_security_headers(headers, csp_policy: str) -> None:
    """Apply all security headers to a response/exception headers dict."""
    headers['Content-Security-Policy'] = csp_policy
    headers['X-Frame-Options'] = 'DENY'
    headers['X-Content-Type-Options'] = 'nosniff'
    headers['Server'] = '讀豹'
    headers['Permissions-Policy'] = PERMISSIONS_POLICY


@web.middleware
async def csp_middleware(request: web.Request, handler):
    """Add Content-Security-Policy and other security headers to all responses."""
    nonce = secrets.token_urlsafe(16)
    request['csp_nonce'] = nonce
    csp_policy = _build_csp(nonce)

    try:
        response = await handler(request)
    except web.HTTPException as ex:
        _apply_security_headers(ex.headers, csp_policy)
        raise

    _apply_security_headers(response.headers, csp_policy)
    return response
