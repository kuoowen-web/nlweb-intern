"""
Admin dev-only endpoints.

NOTE: These endpoints are intended for E2E test verification and
developer diagnostics only. Do NOT expose to general user UI.
Authorization is enforced strictly by role == 'admin'.

See docs/in progress/plans/frontend-init-sync-refactor-plan.md Task 2B
for the rationale (replaces UI-count substitute with authoritative PG
row count for cross-user spawn detection tests).
"""

from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("admin_route")


async def _count_sessions_for_user(user_id: str) -> int:
    """COUNT(*) FROM search_sessions WHERE user_id = $1 (non-deleted only).

    Uses the shared AuthDB instance (same handle as SessionService) so the
    answer is consistent across SQLite (local dev) and PostgreSQL (prod).
    """
    from auth.auth_db import AuthDB
    db = AuthDB.get_instance()
    row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM search_sessions "
        "WHERE user_id = ? AND deleted_at IS NULL",
        (user_id,)
    )
    if not row:
        return 0
    # AuthDB returns dict-like rows; column key is 'c'
    return int(row['c'])


async def admin_session_count_handler(request: web.Request) -> web.Response:
    """GET /api/admin/session-count?user_id=<uuid> - admin dev-only."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)
    if user_info.get('role') != 'admin':
        return web.json_response({'error': 'Admin only'}, status=403)

    target_user_id = request.query.get('user_id')
    if not target_user_id:
        return web.json_response(
            {'error': 'user_id query param required'}, status=400
        )

    try:
        count = await _count_sessions_for_user(target_user_id)
    except Exception as e:
        logger.error(f"admin_session_count error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)

    return web.json_response({
        'success': True,
        'user_id': target_user_id,
        'count': count,
    })


def setup_admin_routes(app: web.Application) -> None:
    """Register admin dev-only routes. Idempotent."""
    app.router.add_get('/api/admin/session-count', admin_session_count_handler)
