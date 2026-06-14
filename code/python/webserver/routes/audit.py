"""
Audit log API routes.

GET /api/audit/logs   — admin: org-wide audit log
GET /api/audit/trail  — authenticated user: personal research trail
"""

from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("audit_routes")


def _get_user_info(request: web.Request):
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return None
    return user_info


async def audit_logs_handler(request: web.Request) -> web.Response:
    """GET /api/audit/logs — admin only, returns org audit log."""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    if user_info.get('role') != 'admin':
        return web.json_response({'error': 'Admin access required'}, status=403)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    action = request.query.get('action')
    user_id = request.query.get('user_id')
    since = request.query.get('since')
    until = request.query.get('until')
    try:
        limit = int(request.query.get('limit', '100'))
        offset = int(request.query.get('offset', '0'))
    except ValueError:
        return web.json_response({'error': 'limit/offset must be integers'}, status=400)

    # Clamp limit to prevent abuse
    limit = min(limit, 500)

    try:
        since_f = float(since) if since else None
        until_f = float(until) if until else None
    except ValueError:
        return web.json_response({'error': 'since/until must be epoch seconds'}, status=400)

    try:
        from core.audit_service import get_audit_logs
        logs = await get_audit_logs(
            org_id,
            action=action,
            user_id=user_id,
            since=since_f,
            until=until_f,
            limit=limit,
            offset=offset,
        )
        return web.json_response({'success': True, 'logs': logs, 'count': len(logs)})
    except Exception as e:
        logger.error(f"Audit logs error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def research_trail_handler(request: web.Request) -> web.Response:
    """GET /api/audit/trail — personal research trail for current user."""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    since = request.query.get('since')
    until = request.query.get('until')
    try:
        limit = int(request.query.get('limit', '200'))
    except ValueError:
        return web.json_response({'error': 'limit must be an integer'}, status=400)
    limit = min(limit, 500)

    try:
        since_f = float(since) if since else None
        until_f = float(until) if until else None
    except ValueError:
        return web.json_response({'error': 'since/until must be epoch seconds'}, status=400)

    try:
        from core.audit_service import get_my_research_trail
        trail = await get_my_research_trail(
            user_info['id'],
            since=since_f,
            until=until_f,
            limit=limit,
        )
        return web.json_response({'success': True, 'trail': trail, 'count': len(trail)})
    except Exception as e:
        logger.error(f"Research trail error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


def setup_audit_routes(app: web.Application):
    """Register audit routes."""
    app.router.add_get('/api/audit/logs', audit_logs_handler)
    app.router.add_get('/api/audit/trail', research_trail_handler)
    logger.info("Audit routes registered")
