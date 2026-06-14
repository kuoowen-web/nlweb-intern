"""
GET /api/user/init - Composite endpoint for frontend init sync.

Returns user / org / role / sessions / shared_sessions / preferences in a
single round-trip. Replaces the pattern of frontend hitting 5+ endpoints
on every login / user-switch / page-reload.

Lazy fields (NOT here, fetched on-demand by frontend):
- Admin member list (admin modal only)
- Source/file folder content (device-scoped UI)
- Full session conversation history (GET /api/sessions/{id} when clicked)

See docs/in progress/plans/frontend-init-sync-refactor-plan.md (D-1)
for the necessary-tier / lazy-tier rationale.
"""

import asyncio
from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_init_route")

# Caps to keep init payload bounded
_SESSIONS_LIMIT = 50
_SHARED_SESSIONS_LIMIT = 50


def _get_session_service():
    """Lazy-init SessionService singleton (avoids import-time DB connection)."""
    from core.session_service import SessionService
    if not hasattr(_get_session_service, '_instance'):
        _get_session_service._instance = SessionService()
    return _get_session_service._instance


def _get_auth_service():
    """Lazy-init AuthService singleton."""
    from auth.auth_service import AuthService
    if not hasattr(_get_auth_service, '_instance'):
        _get_auth_service._instance = AuthService()
    return _get_auth_service._instance


async def user_init_handler(request: web.Request) -> web.Response:
    """GET /api/user/init"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    user_id = user_info['id']
    org_id = user_info.get('org_id')
    role = user_info.get('role')

    if not org_id:
        # B2B context required; orphan users must be handled by onboarding.
        logger.warning(f"user_init: user {user_id} has no org_id; refusing to init.")
        return web.json_response({'error': 'No organization context'}, status=400)

    svc = _get_session_service()
    auth_svc = _get_auth_service()

    # Parallel fetch - latency dominated by slowest leg
    try:
        user_t = asyncio.create_task(auth_svc.get_user_by_id(user_id))
        org_t = asyncio.create_task(auth_svc.get_org_by_id(org_id))
        sessions_t = asyncio.create_task(
            svc.list_sessions(user_id, org_id,
                              limit=_SESSIONS_LIMIT, offset=0,
                              include_archived=False)
        )
        shared_t = asyncio.create_task(
            svc.get_shared_sessions(user_id, org_id,
                                    limit=_SHARED_SESSIONS_LIMIT, offset=0)
        )
        prefs_t = asyncio.create_task(svc.get_preferences(user_id, org_id))

        user, org, sessions, shared_sessions, preferences = await asyncio.gather(
            user_t, org_t, sessions_t, shared_t, prefs_t
        )
    except AttributeError as e:
        # Defensive: if a service method is renamed/removed, surface clearly
        # rather than returning an opaque 500. (Silent-fail prohibition.)
        logger.error(f"user_init: missing service method: {e}", exc_info=True)
        return web.json_response(
            {'error': f'Server missing service method: {e}'}, status=500
        )
    except Exception as e:
        logger.error(f"user_init handler error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)

    if not user:
        return web.json_response({'error': 'User not found'}, status=404)

    # Contract: user payload must match the shape returned by /api/auth/login
    # and /api/auth/me — both inject org_id + role into the user dict. Without
    # this, frontend init-sync overwrites _user with a payload missing org_id,
    # which breaks isOnline() / org admin guards (e.g. share-to-org button,
    # org invite section).
    user['org_id'] = org_id
    user['role'] = role

    return web.json_response({
        'success': True,
        'user': user,
        'org': org,
        'role': role,
        'sessions': sessions or [],
        'shared_sessions': shared_sessions or [],
        'preferences': preferences or {},
    })


def setup_user_init_routes(app: web.Application) -> None:
    """Register /api/user/init route. Idempotent."""
    app.router.add_get('/api/user/init', user_init_handler)
