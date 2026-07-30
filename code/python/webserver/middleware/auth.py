"""Authentication middleware for aiohttp server"""

from aiohttp import web
import logging
import os
import jwt
import time
import re
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Public endpoints that don't require authentication (all HTTP methods)
PUBLIC_ENDPOINTS: Set[str] = {
    '/',
    '/app',              # 產品頁（登入牆是前端 modal，同 '/' 舊行為）
    '/health',
    '/ready',
    '/who',
    '/sites',
    '/sites_config',
    # Static files
    '/static',
    '/html',
    # Bootstrap setup page (public — token validated inside handler)
    '/setup',
    # Auth endpoints (must be public for login/register flow)
    '/api/auth/register',
    '/api/auth/login',
    '/api/auth/verify-email',
    '/api/auth/forgot-password',
    '/api/auth/reset-password',
    '/api/auth/refresh',
    '/api/auth/logout',
    '/api/auth/activate',
    # Analytics events — sent by frontend tracker (may be unauthenticated users)
    '/api/analytics/event',
    '/api/analytics/event/batch',
    # Help Center — feedback is public
    '/api/help/feedback',
    '/api/early-bird',   # landing 早鳥表單（public POST）
}

# Endpoints that are public for GET requests only; other methods require auth
PUBLIC_GET_ENDPOINTS: Set[str] = {
    # FAQ list is public (read-only); POST/PUT/DELETE require admin auth
    '/api/faq',
    # Landing 公開 subpage（免登入、只 GET；單篇 /blog/<slug> 走 _BLOG_PUBLIC_PATH_RE 放行）
    '/faq',              # FAQ 公開頁
    '/blog',             # Blog 列表頁
    # tier6 error aggregate（僅 error_type 計數，無 query 內容）— daily-patrol
    # 無憑證巡檢要能打；handler 層同一裁決見 analytics_handler.get_tier6_errors
    '/api/analytics/tier6_errors',
}


# ── Blog slug 規則：單一權威點 ────────────────────────────────────
# slug 字元類定義於此（middleware），route 層（static.py）import _BLOG_SLUG_RE 衍生
# 自己的 slug 驗證 → 兩處同構、零漂移。middleware 用 path regex 放行、只 GET、
# 與 route 白名單同構（不寬放所有 /blog/...）。
# 🔧 R2（nit-1）：錨用 \Z 不用 $——Python `$` 匹配「尾端換行前」，`hello\n`（來自
# /blog/hello%0A decode）會過 `^...$` 白名單（實測 match True）；`\Z` 只匹配字串真結尾，
# 尾隨換行不過。無實害但語義乾淨、封死 %0A 縫。
_BLOG_SLUG_CHARS = r'[a-z0-9-]+'
_BLOG_SLUG_RE = re.compile(rf'^{_BLOG_SLUG_CHARS}\Z')          # static.py import 此個驗 slug
_BLOG_PUBLIC_PATH_RE = re.compile(rf'^/blog/{_BLOG_SLUG_CHARS}\Z')  # middleware 放行 /blog/<slug>


def _try_soft_auth(request: web.Request) -> None:
    """Try to decode JWT on public endpoints. Sets request['user'] if valid, does nothing if not."""
    auth_header = request.headers.get('Authorization', '')
    token = auth_header[7:] if auth_header.startswith('Bearer ') else None
    if not token:
        token = request.cookies.get('access_token')
    if not token:
        return

    jwt_secret = os.environ.get('JWT_SECRET')
    if not jwt_secret:
        return

    try:
        payload = jwt.decode(token, jwt_secret, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if user_id:
            request['user'] = {
                'id': user_id,
                'name': payload.get('name', 'User'),
                'email': payload.get('email'),
                'org_id': payload.get('org_id'),
                'role': payload.get('role'),
                'authenticated': True,
                'token': token,
            }
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        pass  # Soft auth — silently ignore invalid tokens on public endpoints


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Handle authentication for protected endpoints"""

    # Check if path is public
    path = request.path

    # /blog/ 前綴但 slug 非法（含 traversal / 大寫 / 底線 / 尾隨換行）→ middleware 層 404。
    # 公開頁的非法 URL 語義是「找不到」，不是「請登入」——擋在 auth 檢查前，避免回 401。
    # 🔧 land-diff R1（Codex SF-1）：不綁 method——任何 method 打非法 /blog/ path 都
    # 該是「找不到」（POST /blog/Foo_Bar → 404，非 401）。合法 /blog/<slug> 續走下方
    # is_public 的 _BLOG_PUBLIC_PATH_RE 分支放行。
    if path.startswith('/blog/') and not _BLOG_PUBLIC_PATH_RE.match(path):
        return web.Response(text="Not found", status=404)

    # Check exact matches and path prefixes
    # 🔧 land-diff R1（Codex SF-1 + agy nit）：public read 放行 GET 也放行 HEAD——
    # aiohttp add_get 預設一併註冊 HEAD route，公開頁對 crawler / link checker /
    # uptime probe 的 HEAD 探測不該回 401。合法 path 的其他非 GET/HEAD method（如
    # POST /blog/<slug>）仍落 auth 401（行為不變）。
    _public_read = request.method in ('GET', 'HEAD')
    is_public = (
        path in PUBLIC_ENDPOINTS or
        (_public_read and path in PUBLIC_GET_ENDPOINTS) or
        (_public_read and bool(_BLOG_PUBLIC_PATH_RE.match(path))) or
        path.startswith('/static/') or
        path.startswith('/html/') or
        path == '/favicon.ico'
    )

    if is_public:
        # Public endpoint — still try to extract user info if token present (soft auth)
        _try_soft_auth(request)
        return await handler(request)

    # Check for authentication token
    auth_token = None

    # Check Authorization header
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        auth_token = auth_header[7:]

    # BP-1: Check httpOnly cookie (primary auth for web UI)
    if not auth_token:
        auth_cookie = request.cookies.get('access_token')
        if auth_cookie:
            auth_token = auth_cookie

    # Check query parameter (for SSE connections that can't set cookies/headers)
    if not auth_token and request.method == 'GET':
        auth_token = request.query.get('auth_token')

    # Dev auth bypass DELETED (2026-05-19 spec v0.大 §5.4).
    # Reason: bypass was a hack that broke production-path fidelity in v9-v15 E2E.
    # 'dev_user' string id clashed with PG users.id UUID type (v15 P0-1 Server 500).
    # Replacement: E2E uses real admin login (admin@twdubao.com / test1234!, see spec §5.5).
    # If you find yourself wanting to re-add bypass, fix the real login flow instead.

    if not auth_token:
        logger.warning(f"No auth token provided for protected endpoint: {path}")
        return web.json_response(
            {'error': 'Authentication required', 'type': 'auth_required'},
            status=401,
            headers={'WWW-Authenticate': 'Bearer'}
        )

    # Validate token
    request['auth_token'] = auth_token

    # Validate as JWT token
    user_id = None
    user_name = 'User'
    user_email = None

    jwt_secret = os.environ.get('JWT_SECRET')
    if jwt_secret:
        try:
            payload = jwt.decode(auth_token, jwt_secret, algorithms=['HS256'])

            # Extract user info from JWT
            user_id = payload.get('user_id')
            if not user_id:
                logger.warning("JWT payload missing user_id")
                return web.json_response(
                    {'error': 'Invalid token payload', 'type': 'invalid_token'},
                    status=401
                )
            user_name = payload.get('name', 'User')
            user_email = payload.get('email')

        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return web.json_response(
                {'error': 'Token expired', 'type': 'token_expired'},
                status=401
            )
        except jwt.InvalidTokenError as e:
            logger.warning(f"JWT validation failed: {e}")
            return web.json_response(
                {'error': 'Invalid token', 'type': 'invalid_token'},
                status=401
            )
    else:
        # No JWT_SECRET configured — cannot validate tokens
        logger.warning("JWT_SECRET not configured, cannot validate auth tokens")
        return web.json_response(
            {'error': 'Authentication not configured', 'type': 'auth_not_configured'},
            status=500
        )

    request['user'] = {
        'id': user_id,
        'name': user_name,
        'email': user_email,
        'org_id': payload.get('org_id'),
        'role': payload.get('role'),
        'authenticated': True,
        'token': auth_token
    }

    # Continue to handler
    return await handler(request)
