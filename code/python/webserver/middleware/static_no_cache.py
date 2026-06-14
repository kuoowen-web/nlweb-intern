"""Static asset cache-control middleware (D-14 anchor).

Frontend Modular Refactor v3.3 Phase 1 (2026-05-21) lands this middleware as part of
the D-14 "Cache identity is part of module identity" infrastructure.

Rationale (D-14):
    Splitting the frontend into ~22 JS + ~18 CSS files makes cache-bust per-file
    impractical to enforce by subagent discipline. Instead, this middleware emits
    `Cache-Control: no-cache, must-revalidate` for every URL under /static/js/ and
    /static/css/. `no-cache` does NOT mean "always re-download" — browsers still
    send conditional requests (If-Modified-Since / ETag) and accept 304 responses.
    But after every deploy, first visit guarantees latest version.

    Verified by: tools/frontend_ownership_check.py --check cache-headers
"""

from aiohttp import web


_STATIC_PREFIX = '/static/'
_MODULE_ASSET_SUFFIXES = ('.js', '.css')


def _is_module_asset(path: str) -> bool:
    """True for any .js / .css under /static/ (subfolders OR root-level barrel entries).

    Includes /static/js/, /static/css/ subfolder modules AND legacy barrel entries
    that still live directly under /static/ during the incremental refactor
    (news-search.css, news-search.js, phase-gate-probe.js, etc.). Excludes fonts /
    images / other static assets — those are large + content-addressable by hash.
    """
    return path.startswith(_STATIC_PREFIX) and path.endswith(_MODULE_ASSET_SUFFIXES)


@web.middleware
async def static_no_cache_middleware(request: web.Request, handler):
    """Emit Cache-Control: no-cache, must-revalidate for every .js / .css under /static/.

    Applied AFTER the handler runs so we override any default cache header set by
    aiohttp's static file handler.
    """
    response = await handler(request)
    if _is_module_asset(request.path):
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response
