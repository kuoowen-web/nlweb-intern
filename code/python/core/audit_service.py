"""
Audit logging service (Phase 3B).

Records user/system actions for compliance and research trail.
All writes are fire-and-forget (asyncio.create_task) — never blocks request path.

Actions catalogue:
  auth.login, auth.login_failed, auth.register, auth.password_reset
  session.create, session.delete, session.export, session.share
  session.restore, session.feedback
  member.invite, member.remove
  file.upload, file.delete
  search.query, search.deep_research
  org.settings.update
"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from auth.auth_db import AuthDB
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("audit_service")


def _get_db() -> AuthDB:
    return AuthDB.get_instance()


# ── Public API ────────────────────────────────────────────────────

async def log_action(
    action: str,
    *,
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip: Optional[str] = None,
) -> None:
    """Write one audit record.  Call via fire_and_forget() from route handlers."""
    try:
        db = _get_db()
        entry_id = str(uuid.uuid4())
        details_str = json.dumps(details) if details else None
        await db.execute(
            "INSERT INTO audit_logs "
            "(id, user_id, org_id, action, target_type, target_id, details, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, user_id, org_id, action, target_type, target_id,
             details_str, ip, time.time())
        )
    except Exception as e:
        # Audit failure must never crash the request
        logger.warning(f"Audit write failed [{action}]: {e}")


def fire_and_forget(coro) -> None:
    """Schedule an audit coroutine without awaiting it.

    Usage:
        from core.audit_service import log_action, fire_and_forget
        fire_and_forget(log_action('auth.login', user_id=uid, ip=ip))
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(coro)
        else:
            loop.run_until_complete(coro)
    except Exception as e:
        logger.warning(f"fire_and_forget failed: {e}")


# ── Query API ─────────────────────────────────────────────────────

async def get_audit_logs(
    org_id: str,
    *,
    action: Optional[str] = None,
    user_id: Optional[str] = None,
    since: Optional[float] = None,     # epoch seconds
    until: Optional[float] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict]:
    """Return audit logs for an org (admin use).  Filters are ANDed."""
    db = _get_db()

    clauses = ["org_id = ?"]
    params: list = [org_id]

    if action:
        clauses.append("action = ?")
        params.append(action)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("created_at <= ?")
        params.append(until)

    where = " AND ".join(clauses)
    params.extend([limit, offset])

    rows = await db.fetchall(
        f"SELECT id, user_id, org_id, action, target_type, target_id, "
        f"details, ip_address, created_at "
        f"FROM audit_logs WHERE {where} "
        f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params)
    )

    result = []
    for row in rows:
        r = dict(row)
        if r.get('details'):
            try:
                r['details'] = json.loads(r['details'])
            except Exception:
                pass
        result.append(r)
    return result


async def get_my_research_trail(
    user_id: str,
    *,
    since: Optional[float] = None,
    until: Optional[float] = None,
    limit: int = 200,
) -> List[Dict]:
    """Return a user's own search/research audit trail."""
    db = _get_db()

    clauses = ["user_id = ?", "action IN ('search.query','search.deep_research','session.create','session.export')"]
    params: list = [user_id]

    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("created_at <= ?")
        params.append(until)

    params.append(limit)

    rows = await db.fetchall(
        f"SELECT action, target_type, target_id, details, created_at "
        f"FROM audit_logs WHERE {' AND '.join(clauses)} "
        f"ORDER BY created_at DESC LIMIT ?",
        tuple(params)
    )

    result = []
    for row in rows:
        r = dict(row)
        if r.get('details'):
            try:
                r['details'] = json.loads(r['details'])
            except Exception:
                pass
        result.append(r)
    return result
