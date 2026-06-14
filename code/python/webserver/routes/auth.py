"""
Auth API routes: register, login, token refresh, password reset, org management.

All handlers directly await async AuthService methods (no asyncio.to_thread).
"""

import json
from html import escape as _html_escape

from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger
from core.audit_service import log_action, fire_and_forget
from webserver.middleware.ip_utils import get_client_ip as _get_client_ip

logger = get_configured_logger("auth_routes")


def _get_service():
    """Lazy-init AuthService to avoid import-time DB hits."""
    from auth.auth_service import AuthService
    if not hasattr(_get_service, '_instance'):
        _get_service._instance = AuthService()
    return _get_service._instance


# ── Auth Routes ───────────────────────────────────────────────────

async def register_handler(request: web.Request) -> web.Response:
    """POST /api/auth/register — Bootstrap admin via bootstrap token (B2B)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '')
    password = body.get('password', '')
    name = body.get('name', '')
    org_name = body.get('org_name', '')
    bootstrap_token = body.get('bootstrap_token', '')

    if not email or not password or not name:
        return web.json_response({'error': 'email, password, and name are required'}, status=400)

    if not bootstrap_token:
        return web.json_response({'error': 'bootstrap_token is required'}, status=400)

    try:
        user = await _get_service().register_user(email, password, name, org_name, bootstrap_token)
        fire_and_forget(log_action(
            'auth.bootstrap',
            user_id=user.get('id'),
            ip=_get_client_ip(request),
            details={'email': email, 'name': name, 'org_name': org_name},
        ))

        # Task 6 Backend Variant: auto-issue cookies so inline /setup JS can
        # redirect straight into the app (eliminates the "前往登入" window where
        # stale admin session state could leak through). Mirrors login_handler
        # cookie config below — same flags, same paths, same max_age.
        access_token = user.pop('access_token')
        refresh_token = user.pop('refresh_token')
        response = web.json_response({'success': True, 'user': user}, status=201)
        response.set_cookie(
            'access_token', access_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=15 * 60,  # 15 minutes (match JWT expiry)
            path='/'
        )
        response.set_cookie(
            'refresh_token', refresh_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=7 * 24 * 3600,
            path='/api/auth'
        )
        return response
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Register error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def verify_email_handler(request: web.Request) -> web.Response:
    """GET /api/auth/verify-email?token=xxx"""
    token = request.query.get('token', '')
    if not token:
        return web.json_response({'error': 'Token is required'}, status=400)

    try:
        user = await _get_service().verify_email(token)
        return web.json_response({'success': True, 'message': 'Email verified successfully', 'user': user})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Verify email error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def login_handler(request: web.Request) -> web.Response:
    """POST /api/auth/login"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '')
    password = body.get('password', '')

    if not email or not password:
        return web.json_response({'error': 'email and password are required'}, status=400)

    ip = _get_client_ip(request)

    try:
        result = await _get_service().login(email, password, ip)

        fire_and_forget(log_action(
            'auth.login',
            user_id=result.get('user', {}).get('id'),
            org_id=result.get('user', {}).get('org_id'),
            ip=ip,
            details={'email': email},
        ))

        # BP-1: Set access token as httpOnly cookie (replaces localStorage)
        access_token = result.pop('access_token')
        refresh_token = result.pop('refresh_token')
        response = web.json_response({'success': True, **result})
        # Always Secure: production is HTTPS (nginx terminates SSL, request.secure is always False behind proxy)
        response.set_cookie(
            'access_token', access_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=15 * 60,  # 15 minutes (match JWT expiry)
            path='/'
        )
        response.set_cookie(
            'refresh_token', refresh_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=7 * 24 * 3600,
            path='/api/auth'
        )
        return response
    except ValueError as e:
        fire_and_forget(log_action(
            'auth.login_failed',
            ip=ip,
            details={'email': email, 'reason': str(e)},
        ))
        return web.json_response({'error': str(e)}, status=401)
    except RuntimeError as e:
        logger.error(f"Login config error: {e}")
        return web.json_response({'error': 'Authentication not configured'}, status=500)
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def refresh_handler(request: web.Request) -> web.Response:
    """POST /api/auth/refresh"""
    refresh_token_value = request.cookies.get('refresh_token', '')

    if not refresh_token_value:
        try:
            body = await request.json()
            refresh_token_value = body.get('refresh_token', '')
        except Exception:
            pass

    if not refresh_token_value:
        return web.json_response({'error': 'Refresh token is required'}, status=401)

    try:
        result = await _get_service().refresh_token(refresh_token_value)

        # BP-1: Set new access token cookie
        access_token = result.pop('access_token')
        # BP-2: Set rotated refresh token cookie
        new_refresh_token = result.pop('refresh_token')

        response = web.json_response({'success': True, **result})
        # Always Secure: production is HTTPS (nginx terminates SSL, request.secure is always False behind proxy)
        response.set_cookie(
            'access_token', access_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=15 * 60,
            path='/'
        )
        response.set_cookie(
            'refresh_token', new_refresh_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=7 * 24 * 3600,
            path='/api/auth'
        )
        return response
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=401)
    except Exception as e:
        logger.error(f"Refresh error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def logout_handler(request: web.Request) -> web.Response:
    """POST /api/auth/logout"""
    refresh_token_value = request.cookies.get('refresh_token', '')

    if not refresh_token_value:
        try:
            body = await request.json()
            refresh_token_value = body.get('refresh_token', '')
        except Exception:
            pass

    if refresh_token_value:
        try:
            await _get_service().logout(refresh_token_value)
        except Exception as e:
            logger.warning(f"Logout error: {e}")

    response = web.json_response({'success': True, 'message': 'Logged out'})
    response.del_cookie('access_token', path='/')
    response.del_cookie('refresh_token', path='/api/auth')
    return response


async def change_password_handler(request: web.Request) -> web.Response:
    """POST /api/auth/change-password — Authenticated user changes their own password."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    current_password = body.get('current_password', '')
    new_password = body.get('new_password', '')

    if not current_password or not new_password:
        return web.json_response({'error': 'current_password and new_password are required'}, status=400)

    try:
        await _get_service().change_password(user_info['id'], current_password, new_password)
        fire_and_forget(log_action(
            'auth.change_password',
            user_id=user_info['id'],
            org_id=user_info.get('org_id'),
            ip=_get_client_ip(request),
        ))
        return web.json_response({'success': True, 'message': 'Password changed. Please log in again.'})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Change password error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def logout_all_handler(request: web.Request) -> web.Response:
    """POST /api/auth/logout-all — Authenticated user logs out all their devices."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        await _get_service().revoke_all_user_tokens(user_info['id'])
        fire_and_forget(log_action(
            'auth.logout_all',
            user_id=user_info['id'],
            org_id=user_info.get('org_id'),
            ip=_get_client_ip(request),
        ))
        response = web.json_response({'success': True, 'message': 'Logged out from all devices'})
        response.del_cookie('access_token', path='/')
        response.del_cookie('refresh_token', path='/api/auth')
        return response
    except Exception as e:
        logger.error(f"Logout all error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def me_handler(request: web.Request) -> web.Response:
    """GET /api/auth/me — Get current user info (requires auth)."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        user = await _get_service().get_user_by_id(user_info['id'])
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)

        user['org_id'] = user_info.get('org_id')
        user['role'] = user_info.get('role')

        return web.json_response({'success': True, 'user': user})
    except Exception as e:
        logger.error(f"Me handler error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def forgot_password_handler(request: web.Request) -> web.Response:
    """POST /api/auth/forgot-password"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '')
    if not email:
        return web.json_response({'error': 'email is required'}, status=400)

    try:
        await _get_service().forgot_password(email)
        return web.json_response({'success': True, 'message': 'If the email exists, a reset link has been sent.'})
    except Exception as e:
        logger.error(f"Forgot password error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def reset_password_handler(request: web.Request) -> web.Response:
    """POST /api/auth/reset-password"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    token = body.get('token', '')
    new_password = body.get('new_password', '')

    if not token or not new_password:
        return web.json_response({'error': 'token and new_password are required'}, status=400)

    try:
        await _get_service().reset_password(token, new_password)
        fire_and_forget(log_action(
            'auth.password_reset',
            ip=_get_client_ip(request),
        ))
        return web.json_response({'success': True, 'message': 'Password reset successfully'})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Reset password error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Admin Routes (B2B) ────────────────────────────────────────────

async def admin_create_user_handler(request: web.Request) -> web.Response:
    """POST /api/admin/create-user — Admin creates employee account."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '')
    name = body.get('name', '')
    role = body.get('role', 'member')

    if not email or not name:
        return web.json_response({'error': 'email and name are required'}, status=400)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        result = await _get_service().admin_create_user(email, name, role, org_id, user_info['id'])
        fire_and_forget(log_action(
            'admin.create_user',
            user_id=user_info['id'],
            org_id=org_id,
            ip=_get_client_ip(request),
            details={'email': email, 'name': name, 'role': role},
        ))
        return web.json_response({'success': True, 'user': result}, status=201)
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Admin create user error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def admin_logout_user_handler(request: web.Request) -> web.Response:
    """POST /api/admin/logout-user/{user_id} — Admin force-logs-out a member."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    target_user_id = request.match_info.get('user_id')
    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    # Verify requester is admin of same org
    from auth.auth_db import AuthDB
    db = AuthDB.get_instance()
    membership = await db.fetchone(
        "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
        (user_info['id'], org_id)
    )
    if not membership or membership['role'] != 'admin':
        return web.json_response({'error': 'Only admins can force logout members'}, status=403)

    # Verify target is in same org
    target_membership = await db.fetchone(
        "SELECT id FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
        (target_user_id, org_id)
    )
    if not target_membership:
        return web.json_response({'error': 'User not found in organization'}, status=404)

    try:
        await _get_service().revoke_all_user_tokens(target_user_id)
        fire_and_forget(log_action(
            'admin.logout_user',
            user_id=user_info['id'],
            org_id=org_id,
            ip=_get_client_ip(request),
            details={'target_user_id': target_user_id},
        ))
        return web.json_response({'success': True, 'message': 'User logged out from all devices'})
    except Exception as e:
        logger.error(f"Admin logout user error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def admin_set_user_active_handler(request: web.Request) -> web.Response:
    """PATCH /api/admin/user/{user_id}/active — Admin activates or deactivates a member."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    target_user_id = request.match_info.get('user_id')
    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    if 'is_active' not in body:
        return web.json_response({'error': 'is_active is required'}, status=400)

    is_active = bool(body['is_active'])

    try:
        await _get_service().set_user_active(target_user_id, is_active, user_info['id'], org_id)
        fire_and_forget(log_action(
            'admin.set_user_active',
            user_id=user_info['id'],
            org_id=org_id,
            ip=_get_client_ip(request),
            details={'target_user_id': target_user_id, 'is_active': is_active},
        ))
        action = 'activated' if is_active else 'deactivated'
        return web.json_response({'success': True, 'message': f'User {action}'})
    except PermissionError as e:
        return web.json_response({'error': str(e)}, status=403)
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Admin set user active error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def admin_delete_user_handler(request: web.Request) -> web.Response:
    """DELETE /api/admin/user/{user_id} — Admin soft-deletes a member."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    target_user_id = request.match_info.get('user_id')
    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        await _get_service().delete_user(target_user_id, user_info['id'], org_id)
        fire_and_forget(log_action(
            'admin.delete_user',
            user_id=user_info['id'],
            org_id=org_id,
            ip=_get_client_ip(request),
            details={'target_user_id': target_user_id},
        ))
        return web.json_response({'success': True, 'message': 'User deleted'})
    except PermissionError as e:
        return web.json_response({'error': str(e)}, status=403)
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Admin delete user error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def admin_resend_activation_handler(request: web.Request) -> web.Response:
    """POST /api/admin/resend-activation — Admin resends activation email to unactivated member."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    target_user_id = body.get('user_id', '')
    if not target_user_id:
        return web.json_response({'error': 'user_id is required'}, status=400)

    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        result = await _get_service().admin_resend_activation(
            target_user_id, user_info['id'], org_id
        )
        fire_and_forget(log_action(
            'auth.admin_resend_activation',
            user_id=user_info['id'],
            org_id=org_id,
            ip=_get_client_ip(request),
            details={'target_user_id': target_user_id},
        ))
        return web.json_response({'success': True, 'message': '啟用信已重新寄出'})
    except PermissionError as e:
        return web.json_response({'error': str(e)}, status=403)
    except LookupError as e:
        return web.json_response({'error': str(e)}, status=404)
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Admin resend activation error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def admin_change_role_handler(request: web.Request) -> web.Response:
    """PATCH /api/admin/user/{user_id}/role — Admin changes a member's role."""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    target_user_id = request.match_info.get('user_id')
    org_id = user_info.get('org_id')
    if not org_id:
        return web.json_response({'error': 'No organization context'}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    role = body.get('role', '')
    if not role:
        return web.json_response({'error': 'role is required'}, status=400)

    try:
        await _get_service().change_member_role(org_id, target_user_id, role, user_info['id'])
        fire_and_forget(log_action(
            'admin.change_role',
            user_id=user_info['id'],
            org_id=org_id,
            ip=_get_client_ip(request),
            details={'target_user_id': target_user_id, 'new_role': role},
        ))
        return web.json_response({'success': True, 'message': f'Role updated to {role}'})
    except PermissionError as e:
        return web.json_response({'error': str(e)}, status=403)
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Admin change role error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def activate_page_handler(request: web.Request) -> web.Response:
    """GET /api/auth/activate?token=xxx — Show password setup form (or friendly status page)."""
    token = request.query.get('token', '')
    if not token:
        return web.Response(text='Missing activation token.', content_type='text/html', status=400)

    # C-1: 先查 DB 判斷 token 狀態，給出友善 UI
    import time as _time
    from auth.auth_db import AuthDB
    db = AuthDB.get_instance()
    token_row = await db.fetchone(
        "SELECT id, password_hash, is_active, email_verification_expires "
        "FROM users WHERE email_verification_token = ?",
        (token,)
    )

    if token_row is None:
        # token 不存在：已啟用（消費後被清 NULL）或完全無效 — 友善合併
        # CEO 拍板（2026-05-07）：合併訊息避免 user enumeration，文案給方向感
        return web.Response(
            text=_render_activation_info_page(
                title='連結已失效',
                message='此啟用連結已失效。如果您之前已設定過密碼，請從首頁登入；若忘記密碼，請聯絡管理員。',
                show_login_btn=True,
            ),
            content_type='text/html',
            status=200,
        )

    # is_active 正規化
    is_active = bool(token_row['is_active'])
    if not is_active:
        return web.Response(
            text=_render_activation_info_page(
                title='帳號已停用',
                message='您的帳號已被停用，無法完成啟用。請聯絡管理員。',
                show_login_btn=False,
            ),
            content_type='text/html',
            status=200,
        )

    # 過期判斷（NULL expires = 無過期限制，視為有效）
    expires = token_row.get('email_verification_expires')
    if expires and float(expires) < _time.time():
        return web.Response(
            text=_render_activation_info_page(
                title='啟用連結已過期',
                message='此啟用連結已超過有效期限（48 小時）。請聯絡管理員重新寄送啟用信。',
                show_login_btn=False,
            ),
            content_type='text/html',
            status=200,
        )

    # 理論上不該發生：token 存在但 password_hash 已設
    if token_row['password_hash'] is not None:
        logger.warning(
            f"activate_page_handler: token exists but password_hash is set (user_id={token_row['id']})"
        )
        return web.Response(
            text=_render_activation_info_page(
                title='帳號已啟用',
                message='您的帳號已啟用，請從首頁登入。',
                show_login_btn=True,
            ),
            content_type='text/html',
            status=200,
        )

    # 正常情境：渲染密碼設定表單（後續程式碼不變）
    nonce = request.get('csp_nonce', '')

    # C-1: Escape token for safe use in HTML attribute (html context)
    # and produce a JS string literal via json.dumps (js context) — no f-string interpolation
    safe_token_attr = _html_escape(token, quote=True)
    safe_token_js = json.dumps(token)  # produces "\"...\""  with all special chars escaped

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>啟用帳號 - 讀豹</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #FBF5E6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  .activate-card {{
    background: #FFFFFF;
    border-radius: 10px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    padding: 40px;
    width: 100%;
    max-width: 440px;
  }}
  .activate-logo {{
    text-align: center;
    margin-bottom: 24px;
  }}
  .activate-logo img {{
    height: 64px;
    margin-bottom: 8px;
  }}
  .activate-logo h1 {{
    font-size: 1.5rem;
    color: #2D3436;
    font-weight: 700;
  }}
  .activate-logo p {{
    color: #B2BEC3;
    font-size: 0.9rem;
    margin-top: 4px;
  }}
  .form-group {{
    margin-bottom: 16px;
  }}
  .form-group label {{
    display: block;
    font-size: 0.875rem;
    font-weight: 600;
    color: #2D3436;
    margin-bottom: 6px;
  }}
  .form-group input {{
    width: 100%;
    padding: 10px 14px;
    border: 1px solid #B2BEC3;
    border-radius: 8px;
    font-size: 0.95rem;
    transition: border-color 0.2s;
  }}
  .form-group input:focus {{
    outline: none;
    border-color: #FDCB6E;
    box-shadow: 0 0 0 3px rgba(253,203,110,0.15);
  }}
  .form-hint {{
    font-size: 0.8rem;
    color: #B2BEC3;
    margin-top: 4px;
  }}
  .activate-btn {{
    width: 100%;
    padding: 12px;
    background: #FDCB6E;
    color: #2D3436;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
    transition: opacity 0.2s;
  }}
  .activate-btn:hover {{ background: #d4a84b; }}
  .activate-btn:disabled {{ opacity: 0.5; cursor: not-allowed; background: #FDCB6E; }}
  .error-msg {{
    color: #dc2626;
    font-size: 0.875rem;
    margin-top: 8px;
    display: none;
  }}
  .success-msg {{
    color: #059669;
    font-size: 1rem;
    margin-top: 16px;
    padding: 20px;
    display: none;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="activate-card">
  <div class="activate-logo">
    <img src="/static/images/Leopard.png" alt="讀豹" id="logoImg">
    <h1>啟用帳號</h1>
    <p>設定您的登入密碼</p>
  </div>
  <form id="f">
    <input type="hidden" id="tokenField" name="token" value="{safe_token_attr}">
    <div class="form-group">
      <label for="pw">密碼</label>
      <input type="password" id="pw" required minlength="8" placeholder="至少 8 字元，含大寫與數字" autocomplete="new-password">
      <div class="form-hint">至少 8 字元，需包含大寫字母與數字</div>
    </div>
    <div class="form-group">
      <label for="pw2">確認密碼</label>
      <input type="password" id="pw2" required placeholder="再次輸入密碼" autocomplete="new-password">
    </div>
    <div class="error-msg" id="err"></div>
    <div class="success-msg" id="successEl"></div>
    <button type="submit" class="activate-btn" id="activateBtn">啟用帳號</button>
  </form>
</div>
<script nonce="{nonce}">
document.getElementById('logoImg').addEventListener('error', function() {{ this.style.display = 'none'; }});
document.getElementById('f').addEventListener('submit', function(e) {{
  e.preventDefault();
  go();
}});
async function go(){{
  const errEl = document.getElementById('err');
  const successEl = document.getElementById('successEl');
  const btn = document.getElementById('activateBtn');
  errEl.style.display = 'none';
  successEl.style.display = 'none';
  const pw=document.getElementById('pw').value, pw2=document.getElementById('pw2').value;
  if(pw!==pw2){{errEl.textContent='密碼不一致';errEl.style.display='block';return}}
  btn.disabled = true;
  btn.textContent = '啟用中...';
  const token=document.getElementById('tokenField').value;
  try {{
    const r=await fetch('/api/auth/activate',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{token:token,password:pw}})}});
    const d=await r.json();
    if(d.success){{
      // Task 6 Backend Variant: cookies were just set by the server. Redirect
      // immediately — no "前往登入" window where stale state could surface.
      document.getElementById('f').style.display='none';
      successEl.innerHTML='<h2 style="margin-bottom:12px;color:#2D3436;">帳號已啟用!</h2>'
        +'<p style="color:#2D3436;">即將進入您的工作區...</p>';
      successEl.style.display='block';
      window.location.replace('/');
      return;
    }} else {{
      errEl.textContent=d.error||'啟用失敗';
      errEl.style.display='block';
      btn.disabled=false;
      btn.textContent='啟用帳號';
    }}
  }} catch(e) {{
    errEl.textContent='網路錯誤，請稍後再試';
    errEl.style.display='block';
    btn.disabled=false;
    btn.textContent='啟用帳號';
  }}
}}
</script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')


async def activate_account_handler(request: web.Request) -> web.Response:
    """POST /api/auth/activate — Employee sets password to activate account."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    token = body.get('token', '')
    password = body.get('password', '')

    if not token or not password:
        return web.json_response({'error': 'token and password are required'}, status=400)

    try:
        result = await _get_service().activate_account(token, password)
        fire_and_forget(log_action(
            'auth.activate',
            user_id=result.get('id'),
            ip=_get_client_ip(request),
            details={'email': result.get('email')},
        ))

        # Task 6 Backend Variant: auto-issue cookies so inline /api/auth/activate
        # JS can redirect straight into the app. Mirrors login_handler config.
        access_token = result.pop('access_token')
        refresh_token = result.pop('refresh_token')
        response = web.json_response({'success': True, 'user': result})
        response.set_cookie(
            'access_token', access_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=15 * 60,
            path='/'
        )
        response.set_cookie(
            'refresh_token', refresh_token,
            httponly=True,
            secure=True,
            samesite='Lax',
            max_age=7 * 24 * 3600,
            path='/api/auth'
        )
        return response
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Activate account error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Reset Password Page ───────────────────────────────────────────


async def reset_password_page_handler(request: web.Request) -> web.Response:
    """GET /api/auth/reset-password?token=xxx — Show new password form."""
    token = request.query.get('token', '')
    if not token:
        return web.Response(text='Missing reset token.', content_type='text/html', status=400)

    nonce = request.get('csp_nonce', '')
    safe_token_attr = _html_escape(token, quote=True)
    safe_token_js = json.dumps(token)

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>重設密碼 - 讀豹</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #FBF5E6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  .reset-card {{
    background: #FFFFFF;
    border-radius: 10px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    padding: 40px;
    width: 100%;
    max-width: 440px;
  }}
  .reset-logo {{
    text-align: center;
    margin-bottom: 24px;
  }}
  .reset-logo img {{
    height: 64px;
    margin-bottom: 8px;
  }}
  .reset-logo h1 {{
    font-size: 1.5rem;
    color: #2D3436;
    font-weight: 700;
  }}
  .reset-logo p {{
    color: #B2BEC3;
    font-size: 0.9rem;
    margin-top: 4px;
  }}
  .form-group {{
    margin-bottom: 16px;
  }}
  .form-group label {{
    display: block;
    font-size: 0.875rem;
    font-weight: 600;
    color: #2D3436;
    margin-bottom: 6px;
  }}
  .form-group input {{
    width: 100%;
    padding: 10px 14px;
    border: 1px solid #B2BEC3;
    border-radius: 8px;
    font-size: 0.95rem;
    transition: border-color 0.2s;
  }}
  .form-group input:focus {{
    outline: none;
    border-color: #FDCB6E;
    box-shadow: 0 0 0 3px rgba(253,203,110,0.15);
  }}
  .form-hint {{
    font-size: 0.8rem;
    color: #B2BEC3;
    margin-top: 4px;
  }}
  .reset-btn {{
    width: 100%;
    padding: 12px;
    background: #FDCB6E;
    color: #2D3436;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
    transition: opacity 0.2s;
  }}
  .reset-btn:hover {{ background: #d4a84b; }}
  .reset-btn:disabled {{ opacity: 0.5; cursor: not-allowed; background: #FDCB6E; }}
  .error-msg {{
    color: #dc2626;
    font-size: 0.875rem;
    margin-top: 8px;
    display: none;
  }}
  .success-msg {{
    color: #059669;
    font-size: 1rem;
    margin-top: 16px;
    padding: 20px;
    display: none;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="reset-card">
  <div class="reset-logo">
    <img src="/static/images/Leopard.png" alt="讀豹" id="logoImg">
    <h1>重設密碼</h1>
    <p>請輸入您的新密碼</p>
  </div>
  <form id="resetForm">
    <input type="hidden" id="tokenField" name="token" value="{safe_token_attr}">
    <div class="form-group">
      <label for="newPassword">新密碼</label>
      <input type="password" id="newPassword" required minlength="8" placeholder="至少 8 字元，含大寫與數字" autocomplete="new-password">
      <div class="form-hint">至少 8 字元，需包含大寫字母與數字</div>
    </div>
    <div class="form-group">
      <label for="confirmPassword">確認密碼</label>
      <input type="password" id="confirmPassword" required placeholder="再次輸入新密碼" autocomplete="new-password">
    </div>
    <div class="error-msg" id="resetError"></div>
    <div class="success-msg" id="resetSuccess"></div>
    <button type="submit" class="reset-btn" id="resetBtn">重設密碼</button>
  </form>
</div>
<script nonce="{nonce}">
document.getElementById('logoImg').addEventListener('error', function() {{ this.style.display = 'none'; }});
document.getElementById('resetForm').addEventListener('submit', function(e) {{
  e.preventDefault();
  handleReset();
}});
async function handleReset() {{
  const errEl = document.getElementById('resetError');
  const successEl = document.getElementById('resetSuccess');
  const btn = document.getElementById('resetBtn');
  errEl.style.display = 'none';
  successEl.style.display = 'none';

  const pw = document.getElementById('newPassword').value;
  const pw2 = document.getElementById('confirmPassword').value;
  if (pw !== pw2) {{
    errEl.textContent = '密碼不一致';
    errEl.style.display = 'block';
    return;
  }}

  btn.disabled = true;
  btn.textContent = '重設中...';

  try {{
    const token = {safe_token_js};
    const res = await fetch('/api/auth/reset-password', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ token: token, new_password: pw }})
    }});
    const data = await res.json();
    if (data.success) {{
      document.getElementById('resetForm').style.display = 'none';
      successEl.innerHTML = '<h2 style="margin-bottom:12px;color:#2D3436;">密碼已重設!</h2>'
        + '<p style="color:#2D3436;">您的密碼已成功更新。即將跳轉至首頁...</p>'
        + '<p style="margin-top:16px;"><a href="/" style="color:#2D3436;background:#FDCB6E;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block;">前往登入</a></p>';
      successEl.style.display = 'block';
      setTimeout(() => {{ window.location.href = '/'; }}, 2000);
    }} else {{
      errEl.textContent = data.error || '重設失敗';
      errEl.style.display = 'block';
      btn.disabled = false;
      btn.textContent = '重設密碼';
    }}
  }} catch (e) {{
    errEl.textContent = '網路錯誤，請稍後再試';
    errEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = '重設密碼';
  }}
}}
</script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')


# ── Setup Page (Bootstrap Onboarding) ────────────────────────────


async def setup_page_handler(request: web.Request) -> web.Response:
    """GET /setup?token=xxx — Bootstrap onboarding page for new customer admin."""
    token = request.query.get('token', '')
    if not token:
        return web.Response(
            text=_setup_error_page('缺少設定 Token'),
            content_type='text/html', status=400,
        )

    try:
        token_row = await _get_service().validate_bootstrap_token(token)
    except ValueError as e:
        return web.Response(
            text=_setup_error_page(str(e)),
            content_type='text/html', status=400,
        )

    nonce = request.get('csp_nonce', '')
    org_name_hint = _html_escape(token_row.get('org_name_hint', ''), quote=True)
    safe_token_attr = _html_escape(token, quote=True)

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>組織設定 - 讀豹</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #FBF5E6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  .setup-card {{
    background: #FFFFFF;
    border-radius: 10px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    padding: 40px;
    width: 100%;
    max-width: 440px;
  }}
  .setup-logo {{
    text-align: center;
    margin-bottom: 24px;
  }}
  .setup-logo img {{
    height: 64px;
    margin-bottom: 8px;
  }}
  .setup-logo h1 {{
    font-size: 1.5rem;
    color: #2D3436;
    font-weight: 700;
  }}
  .setup-logo p {{
    color: #B2BEC3;
    font-size: 0.9rem;
    margin-top: 4px;
  }}
  .form-group {{
    margin-bottom: 16px;
  }}
  .form-group label {{
    display: block;
    font-size: 0.875rem;
    font-weight: 600;
    color: #2D3436;
    margin-bottom: 6px;
  }}
  .form-group input {{
    width: 100%;
    padding: 10px 14px;
    border: 1px solid #B2BEC3;
    border-radius: 8px;
    font-size: 0.95rem;
    transition: border-color 0.2s;
  }}
  .form-group input:focus {{
    outline: none;
    border-color: #FDCB6E;
    box-shadow: 0 0 0 3px rgba(253,203,110,0.15);
  }}
  .form-hint {{
    font-size: 0.8rem;
    color: #B2BEC3;
    margin-top: 4px;
  }}
  .setup-btn {{
    width: 100%;
    padding: 12px;
    background: #FDCB6E;
    color: #2D3436;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
    transition: opacity 0.2s;
  }}
  .setup-btn:hover {{ background: #d4a84b; }}
  .setup-btn:disabled {{ opacity: 0.5; cursor: not-allowed; background: #FDCB6E; }}
  .error-msg {{
    color: #dc2626;
    font-size: 0.875rem;
    margin-top: 8px;
    display: none;
  }}
  .success-msg {{
    color: #059669;
    font-size: 1rem;
    margin-top: 16px;
    padding: 20px;
    display: none;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="setup-card">
  <div class="setup-logo">
    <img src="/static/images/Leopard.png" alt="讀豹" id="logoImg">
    <h1>組織設定</h1>
    <p>建立您的組織與管理員帳號</p>
  </div>
  <form id="setupForm">
    <input type="hidden" name="token" value="{safe_token_attr}">
    <div class="form-group">
      <label for="orgName">組織名稱</label>
      <input type="text" id="orgName" required placeholder="例：讀豹科技" value="{org_name_hint}">
    </div>
    <div class="form-group">
      <label for="adminName">管理員名稱</label>
      <input type="text" id="adminName" required placeholder="您的姓名">
    </div>
    <div class="form-group">
      <label for="adminEmail">Email</label>
      <input type="email" id="adminEmail" required placeholder="you@company.com" autocomplete="email">
    </div>
    <div class="form-group">
      <label for="adminPassword">密碼</label>
      <input type="password" id="adminPassword" required minlength="8" placeholder="至少 8 字元，含大寫與數字" autocomplete="new-password">
      <div class="form-hint">至少 8 字元，需包含大寫字母與數字</div>
    </div>
    <div class="form-group">
      <label for="adminPassword2">確認密碼</label>
      <input type="password" id="adminPassword2" required placeholder="再次輸入密碼" autocomplete="new-password">
    </div>
    <div class="error-msg" id="setupError"></div>
    <div class="success-msg" id="setupSuccess"></div>
    <button type="submit" class="setup-btn" id="setupBtn">建立組織</button>
  </form>
</div>
<script nonce="{nonce}">
document.getElementById('logoImg').addEventListener('error', function() {{ this.style.display = 'none'; }});
document.getElementById('setupForm').addEventListener('submit', function(e) {{
  e.preventDefault();
  handleSetup();
}});
async function handleSetup() {{
  const errEl = document.getElementById('setupError');
  const successEl = document.getElementById('setupSuccess');
  const btn = document.getElementById('setupBtn');
  errEl.style.display = 'none';
  successEl.style.display = 'none';

  const pw = document.getElementById('adminPassword').value;
  const pw2 = document.getElementById('adminPassword2').value;
  if (pw !== pw2) {{
    errEl.textContent = '密碼不一致';
    errEl.style.display = 'block';
    return;
  }}

  btn.disabled = true;
  btn.textContent = '建立中...';

  try {{
    const token = document.querySelector('input[name="token"]').value;
    const res = await fetch('/api/auth/register', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        bootstrap_token: token,
        org_name: document.getElementById('orgName').value.trim(),
        name: document.getElementById('adminName').value.trim(),
        email: document.getElementById('adminEmail').value.trim(),
        password: pw
      }})
    }});
    const data = await res.json();
    if (data.success) {{
      // Task 6 Backend Variant: cookies were just set by the server. Redirect
      // immediately so init-sync (Trigger A on cold load) runs against the new
      // identity — no "前往登入" window where stale state could surface.
      document.getElementById('setupForm').style.display = 'none';
      successEl.innerHTML = '<h2 style="margin-bottom:12px;color:#059669;">組織建立成功!</h2>'
        + '<p style="color:#2D3436;">即將進入您的工作區...</p>';
      successEl.style.display = 'block';
      window.location.replace('/');
      return;
    }} else {{
      errEl.textContent = data.error || '建立失敗';
      errEl.style.display = 'block';
    }}
  }} catch (e) {{
    errEl.textContent = '網路錯誤，請稍後再試';
    errEl.style.display = 'block';
  }}
  btn.disabled = false;
  btn.textContent = '建立組織';
}}
</script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')


def _setup_error_page(message: str) -> str:
    """Return a styled error page for invalid bootstrap tokens."""
    safe_msg = _html_escape(message, quote=True)
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>連結無效 - 讀豹</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #FBF5E6;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  .error-card {{
    background: #FFFFFF;
    border-radius: 10px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    padding: 40px;
    width: 100%;
    max-width: 440px;
    text-align: center;
  }}
  .error-card img {{
    height: 64px;
    margin-bottom: 16px;
  }}
  .error-card h1 {{
    font-size: 1.3rem;
    color: #dc2626;
    margin-bottom: 12px;
  }}
  .error-card p {{
    color: #2D3436;
    font-size: 0.95rem;
    line-height: 1.6;
  }}
</style>
</head>
<body>
<div class="error-card">
  <img src="/static/images/Leopard.png" alt="讀豹" onerror="this.style.display='none'">
  <h1>此連結無效或已過期</h1>
  <p>{safe_msg}</p>
  <p style="margin-top:16px;color:#B2BEC3;font-size:0.85rem;">如有疑問，請聯繫平台管理員。</p>
</div>
</body>
</html>"""


def _render_activation_info_page(title: str, message: str, show_login_btn: bool = False) -> str:
    """Render a friendly info page for non-error activation states (already activated, expired, etc)."""
    safe_title = _html_escape(title)
    safe_message = _html_escape(message)
    login_btn = (
        '<p style="margin-top:20px;">'
        '<a href="/" style="color:#2D3436;background:#FDCB6E;padding:10px 24px;'
        'border-radius:8px;text-decoration:none;font-weight:600;display:inline-block;">'
        '前往登入</a></p>'
    ) if show_login_btn else ''
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} - 讀豹</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #FBF5E6; min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 20px;
  }}
  .info-card {{
    background: #FFFFFF; border-radius: 10px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    padding: 40px; width: 100%; max-width: 440px; text-align: center;
  }}
  .info-card img {{ height: 64px; margin-bottom: 16px; }}
  .info-card h1 {{ font-size: 1.3rem; color: #2D3436; margin-bottom: 12px; font-weight: 700; }}
  .info-card p {{ color: #636e72; font-size: 0.95rem; line-height: 1.6; }}
</style>
</head>
<body>
<div class="info-card">
  <img src="/static/images/Leopard.png" alt="讀豹" onerror="this.style.display='none'">
  <h1>{safe_title}</h1>
  <p>{safe_message}</p>
  {login_btn}
</div>
</body>
</html>"""


# ── Organization Routes ───────────────────────────────────────────

async def create_org_handler(request: web.Request) -> web.Response:
    """POST /api/org"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    name = body.get('name', '')
    if not name:
        return web.json_response({'error': 'name is required'}, status=400)

    try:
        org = await _get_service().create_organization(name, user_info['id'])
        return web.json_response({'success': True, 'organization': org})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Create org error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def list_orgs_handler(request: web.Request) -> web.Response:
    """GET /api/org"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        orgs = await _get_service().list_user_orgs(user_info['id'])
        return web.json_response({'success': True, 'organizations': orgs})
    except Exception as e:
        logger.error(f"List orgs error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def invite_member_handler(request: web.Request) -> web.Response:
    """POST /api/org/{id}/invite"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = request.match_info.get('id')

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '')
    role = body.get('role', 'member')

    if not email:
        return web.json_response({'error': 'email is required'}, status=400)

    try:
        result = await _get_service().invite_member(org_id, email, role, user_info['id'])
        fire_and_forget(log_action('member.invite', user_id=user_info['id'], org_id=org_id, target_type='org', target_id=org_id, details={'email': email, 'role': role}))
        return web.json_response({'success': True, 'invitation': result})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Invite member error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def list_members_handler(request: web.Request) -> web.Response:
    """GET /api/org/{id}/members"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = request.match_info.get('id')

    try:
        members = await _get_service().list_org_members(org_id, user_info['id'])
        return web.json_response({'success': True, 'members': members})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=403)
    except Exception as e:
        logger.error(f"List members error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def remove_member_handler(request: web.Request) -> web.Response:
    """DELETE /api/org/{id}/members/{user_id}"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    org_id = request.match_info.get('id')
    target_user_id = request.match_info.get('user_id')

    try:
        await _get_service().remove_member(org_id, target_user_id, user_info['id'])
        fire_and_forget(log_action('member.remove', user_id=user_info['id'], org_id=org_id, target_type='org', target_id=org_id, details={'removed_user_id': target_user_id}))
        return web.json_response({'success': True, 'message': 'Member removed'})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=403)
    except Exception as e:
        logger.error(f"Remove member error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def accept_invite_handler(request: web.Request) -> web.Response:
    """POST /api/org/accept-invite"""
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    token = body.get('token', '')
    if not token:
        return web.json_response({'error': 'token is required'}, status=400)

    try:
        result = await _get_service().accept_invitation(token, user_info['id'])
        return web.json_response({'success': True, **result})
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Accept invite error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ── Route Setup ───────────────────────────────────────────────────

def setup_auth_routes(app: web.Application):
    """Register all auth routes."""
    # Auth routes
    app.router.add_get('/setup', setup_page_handler)
    app.router.add_post('/api/auth/register', register_handler)
    app.router.add_get('/api/auth/verify-email', verify_email_handler)
    app.router.add_post('/api/auth/login', login_handler)
    app.router.add_post('/api/auth/refresh', refresh_handler)
    app.router.add_post('/api/auth/logout', logout_handler)
    app.router.add_get('/api/auth/me', me_handler)
    app.router.add_post('/api/auth/forgot-password', forgot_password_handler)
    app.router.add_get('/api/auth/reset-password', reset_password_page_handler)
    app.router.add_post('/api/auth/reset-password', reset_password_handler)
    app.router.add_get('/api/auth/activate', activate_page_handler)
    app.router.add_post('/api/auth/activate', activate_account_handler)
    app.router.add_post('/api/auth/change-password', change_password_handler)
    app.router.add_post('/api/auth/logout-all', logout_all_handler)

    # Admin routes (B2B)
    app.router.add_post('/api/admin/create-user', admin_create_user_handler)
    app.router.add_post('/api/admin/logout-user/{user_id}', admin_logout_user_handler)
    app.router.add_post('/api/admin/resend-activation', admin_resend_activation_handler)
    app.router.add_patch('/api/admin/user/{user_id}/active', admin_set_user_active_handler)
    app.router.add_patch('/api/admin/user/{user_id}/role', admin_change_role_handler)
    app.router.add_delete('/api/admin/user/{user_id}', admin_delete_user_handler)

    # Organization routes — literal routes before {id} wildcard
    app.router.add_post('/api/org', create_org_handler)
    app.router.add_get('/api/org', list_orgs_handler)
    app.router.add_post('/api/org/accept-invite', accept_invite_handler)  # before {id}
    app.router.add_post('/api/org/{id}/invite', invite_member_handler)
    app.router.add_get('/api/org/{id}/members', list_members_handler)
    app.router.add_delete('/api/org/{id}/members/{user_id}', remove_member_handler)

    logger.info("Auth routes registered")
