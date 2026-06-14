"""
Session API routes: CRUD, migration, export, preferences.

All handlers require authentication (user info injected by auth middleware).
"""

from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger
from core.audit_service import log_action, fire_and_forget

logger = get_configured_logger("session_routes")


def _get_service():
    """Lazy-init SessionService."""
    from core.session_service import SessionService
    if not hasattr(_get_service, '_instance'):
        _get_service._instance = SessionService()
    return _get_service._instance


def _get_user_info(request: web.Request):
    """Extract authenticated user info. Raises 401 if not authenticated."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return None
    return user_info


# ── Session CRUD ─────────────────────────────────────────────────

async def list_sessions_handler(request: web.Request) -> web.Response:
    """GET /api/sessions"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        limit = int(request.query.get('limit', '50'))
        offset = int(request.query.get('offset', '0'))
    except ValueError:
        return web.json_response({'error': 'limit/offset must be integers'}, status=400)
    include_archived = request.query.get('include_archived', 'false').lower() == 'true'

    try:
        sessions = await _get_service().list_sessions(
            user_info['id'], org_id, limit=limit, offset=offset,
            include_archived=include_archived
        )
        return web.json_response({'success': True, 'sessions': sessions})
    except Exception as e:
        logger.error(f"List sessions error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def create_session_handler(request: web.Request) -> web.Response:
    """POST /api/sessions"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    try:
        session = await _get_service().create_session(
            user_id=user_info['id'],
            org_id=org_id,
            title=body.get('title'),
            conversation_history=body.get('conversation_history'),
            session_history=body.get('session_history'),
            chat_history=body.get('chat_history'),
            accumulated_articles=body.get('accumulated_articles'),
            research_report=body.get('research_report'),
        )
        fire_and_forget(log_action('session.create', user_id=user_info['id'], org_id=org_id, target_type='session', target_id=session['id']))
        return web.json_response({'success': True, 'session': session}, status=201)
    except Exception as e:
        logger.error(f"Create session error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def get_session_handler(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        # Try own session first; fall back to shared access (team/org visibility)
        session = await _get_service().get_session_shared(session_id, user_info['id'], org_id)
        if not session:
            logger.warning(f"Session not found: id={session_id!r} user={user_info['id']!r} org={org_id!r}")
            return web.json_response({'error': 'Session not found'}, status=404)
        return web.json_response({'success': True, 'session': session})
    except Exception as e:
        logger.error(f"Get session error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def update_session_handler(request: web.Request) -> web.Response:
    """PUT /api/sessions/{id}"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    try:
        updated = await _get_service().update_session(session_id, user_info['id'], org_id, body)
        if not updated:
            return web.json_response({'error': 'No valid fields to update'}, status=400)
        return web.json_response({'success': True})
    except Exception as e:
        logger.error(f"Update session error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def delete_session_handler(request: web.Request) -> web.Response:
    """DELETE /api/sessions/{id}"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        await _get_service().delete_session(session_id, user_info['id'], org_id)
        fire_and_forget(log_action(
            'session.delete',
            user_id=user_info['id'],
            org_id=org_id,
            target_type='session',
            target_id=session_id,
        ))
        return web.json_response({'success': True, 'message': 'Session deleted'})
    except Exception as e:
        logger.error(f"Delete session error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def restore_session_handler(request: web.Request) -> web.Response:
    """POST /api/sessions/{id}/restore"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        await _get_service().restore_session(session_id, user_info['id'], org_id)
        fire_and_forget(log_action('session.restore', user_id=user_info['id'], org_id=org_id, target_type='session', target_id=session_id))
        return web.json_response({'success': True, 'message': 'Session restored'})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Restore session error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Feedback & Notes ─────────────────────────────────────────────

async def feedback_handler(request: web.Request) -> web.Response:
    """PATCH /api/sessions/{id}/feedback"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    feedback = body.get('feedback')
    if feedback not in ('thumbs_up', 'thumbs_down', None):
        return web.json_response({'error': 'Invalid feedback value'}, status=400)

    try:
        await _get_service().update_session(
            session_id, user_info['id'], org_id, {'user_feedback': feedback}
        )
        fire_and_forget(log_action('session.feedback', user_id=user_info['id'], org_id=org_id, target_type='session', target_id=session_id, details={'feedback': feedback}))
        return web.json_response({'success': True})
    except Exception as e:
        logger.error(f"Feedback error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def note_handler(request: web.Request) -> web.Response:
    """PATCH /api/sessions/{id}/note (admin only)"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    if user_info.get('role') != 'admin':
        return web.json_response({'error': 'Admin access required'}, status=403)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    try:
        await _get_service().update_session(
            session_id, user_info['id'], org_id, {'admin_note': body.get('note', '')}
        )
        return web.json_response({'success': True})
    except Exception as e:
        logger.error(f"Note error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Article Annotation ───────────────────────────────────────────

async def annotate_article_handler(request: web.Request) -> web.Response:
    """PATCH /api/sessions/{id}/articles/{url}/annotate"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')
    # URL is passed in body since URLs in path are problematic
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    article_url = body.get('url', '')
    annotation = body.get('annotation', {})

    if not article_url:
        return web.json_response({'error': 'url is required'}, status=400)

    try:
        await _get_service().update_article_annotation(
            session_id, user_info['id'], org_id, article_url, annotation
        )
        return web.json_response({'success': True})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=404)
    except Exception as e:
        logger.error(f"Annotate article error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Session Sharing ──────────────────────────────────────────────

async def set_visibility_handler(request: web.Request) -> web.Response:
    """PATCH /api/sessions/{id}/visibility"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    visibility = body.get('visibility', '')
    if not visibility:
        return web.json_response({'error': 'visibility is required'}, status=400)

    try:
        await _get_service().set_visibility(session_id, user_info['id'], org_id, visibility)
        if visibility in ('team', 'org'):
            fire_and_forget(log_action(
                'session.share',
                user_id=user_info['id'],
                org_id=org_id,
                target_type='session',
                target_id=session_id,
                details={'visibility': visibility},
            ))
        return web.json_response({'success': True, 'visibility': visibility})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Set visibility error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def shared_sessions_handler(request: web.Request) -> web.Response:
    """GET /api/sessions/shared — sessions from org shared to the current user."""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        limit = int(request.query.get('limit', '50'))
        offset = int(request.query.get('offset', '0'))
    except ValueError:
        return web.json_response({'error': 'limit/offset must be integers'}, status=400)

    try:
        sessions = await _get_service().get_shared_sessions(
            user_info['id'], org_id, limit=limit, offset=offset
        )
        return web.json_response({'success': True, 'sessions': sessions})
    except Exception as e:
        logger.error(f"Shared sessions error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Export ────────────────────────────────────────────────────────

async def export_session_handler(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}/export?format=json|csv|citations|ris"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    session_id = request.match_info.get('id')
    export_format = request.query.get('format', 'json')

    try:
        result = await _get_service().export_session(
            session_id, user_info['id'], org_id, format=export_format
        )
        fire_and_forget(log_action(
            'session.export',
            user_id=user_info['id'],
            org_id=org_id,
            target_type='session',
            target_id=session_id,
            details={'format': export_format},
        ))
        if export_format == 'json':
            return web.json_response({'success': True, 'session': result})
        elif export_format == 'csv':
            return web.Response(
                text=result, content_type='text/csv',
                headers={'Content-Disposition': f'attachment; filename=session_{session_id}.csv'}
            )
        elif export_format == 'ris':
            return web.Response(
                text=result, content_type='application/x-research-info-systems',
                headers={'Content-Disposition': f'attachment; filename=session_{session_id}.ris'}
            )
        else:
            return web.json_response({'success': True, 'citations': result})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Migration ────────────────────────────────────────────────────

async def migrate_sessions_handler(request: web.Request) -> web.Response:
    """POST /api/sessions/migrate"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    sessions = body.get('sessions', [])
    if not sessions:
        return web.json_response({'error': 'No sessions to migrate'}, status=400)

    try:
        result = await _get_service().migrate_sessions(user_info['id'], org_id, sessions)
        return web.json_response({'success': True, **result})
    except Exception as e:
        logger.error(f"Migration error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Preferences ──────────────────────────────────────────────────

async def get_preferences_handler(request: web.Request) -> web.Response:
    """GET /api/preferences"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        prefs = await _get_service().get_preferences(user_info['id'], org_id)
        return web.json_response({'success': True, 'preferences': prefs})
    except Exception as e:
        logger.error(f"Get preferences error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def set_preference_handler(request: web.Request) -> web.Response:
    """PUT /api/preferences/{key}"""
    user_info = _get_user_info(request)
    if not user_info:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    key = request.match_info.get('key')

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    value = body.get('value')
    if value is None:
        return web.json_response({'error': 'value is required'}, status=400)

    try:
        await _get_service().set_preference(user_info['id'], org_id, key, value)
        return web.json_response({'success': True})
    except Exception as e:
        logger.error(f"Set preference error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Route Setup ──────────────────────────────────────────────────

def setup_session_routes(app: web.Application):
    """Register all session routes."""
    # Session CRUD — literal routes must be registered before {id} wildcard
    app.router.add_get('/api/sessions', list_sessions_handler)
    app.router.add_post('/api/sessions', create_session_handler)
    app.router.add_get('/api/sessions/shared', shared_sessions_handler)  # before {id}
    app.router.add_post('/api/sessions/migrate', migrate_sessions_handler)  # before {id}
    app.router.add_get('/api/sessions/{id}', get_session_handler)
    app.router.add_put('/api/sessions/{id}', update_session_handler)
    app.router.add_delete('/api/sessions/{id}', delete_session_handler)

    # Session actions
    app.router.add_post('/api/sessions/{id}/restore', restore_session_handler)
    app.router.add_patch('/api/sessions/{id}/feedback', feedback_handler)
    app.router.add_patch('/api/sessions/{id}/note', note_handler)
    app.router.add_patch('/api/sessions/{id}/visibility', set_visibility_handler)
    app.router.add_patch('/api/sessions/{id}/articles/annotate', annotate_article_handler)
    app.router.add_get('/api/sessions/{id}/export', export_session_handler)

    # Preferences
    app.router.add_get('/api/preferences', get_preferences_handler)
    app.router.add_put('/api/preferences/{key}', set_preference_handler)

    logger.info("Session routes registered")
