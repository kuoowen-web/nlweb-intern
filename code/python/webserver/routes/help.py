"""
Help Center API routes: feedback submission only.

POST /api/feedback — public, store user feedback
"""

import os
import time
import base64
import uuid
from pathlib import Path

from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("help_routes")

VALID_CATEGORIES = {'bug', 'feature', 'content', 'other'}

# Build an absolute path so it resolves correctly regardless of cwd.
# help.py lives at: code/python/webserver/routes/help.py
# parents: routes/ -> webserver/ -> python/ -> code/ -> project_root/
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent.parent.parent
SCREENSHOT_DIR = _PROJECT_ROOT / 'static' / 'uploads' / 'feedback'

MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5 MB after base64 decode

# Magic bytes for allowed image types
_JPEG_MAGIC = b'\xff\xd8\xff'
_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _get_db():
    from auth.auth_db import AuthDB
    return AuthDB.get_instance()


# ── Feedback ──────────────────────────────────────────────────────

async def post_feedback_handler(request: web.Request) -> web.Response:
    """POST /api/feedback — store user feedback (public endpoint)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    category = body.get('category', '')
    rating = body.get('rating')
    content = body.get('content', '')
    email = body.get('email', '')
    screenshot_b64 = body.get('screenshot', '')
    session_id = body.get('session_id', '')

    # Validation
    if category not in VALID_CATEGORIES:
        return web.json_response(
            {'error': f'category must be one of: {", ".join(VALID_CATEGORIES)}'}, status=400
        )
    if not isinstance(rating, int) or not (1 <= rating <= 5):
        return web.json_response({'error': 'rating must be integer 1-5'}, status=400)
    if not content or len(content) < 10:
        return web.json_response({'error': 'content must be at least 10 characters'}, status=400)
    if len(content) > 500:
        return web.json_response({'error': 'content must be 500 characters or less'}, status=400)

    # Auto-fill email from JWT if available
    user = request.get('user')
    user_id = None
    if user and user.get('authenticated'):
        user_id = user.get('id')
        if not email:
            email = user.get('email', '')

    # Handle screenshot upload
    screenshot_path = None
    if screenshot_b64:
        try:
            raw = base64.b64decode(screenshot_b64)
            if len(raw) > MAX_SCREENSHOT_BYTES:
                return web.json_response({'error': 'Screenshot exceeds 5MB limit'}, status=400)
            # Validate image magic bytes (must be JPEG or PNG)
            if not (raw.startswith(_JPEG_MAGIC) or raw.startswith(_PNG_MAGIC)):
                return web.json_response({'error': 'Screenshot must be a JPEG or PNG image'}, status=400)
            ext = 'png' if raw.startswith(_PNG_MAGIC) else 'jpg'
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            fname = f"{uuid.uuid4().hex}.{ext}"
            fpath = SCREENSHOT_DIR / fname
            fpath.write_bytes(raw)
            screenshot_path = f"uploads/feedback/{fname}"
        except web.HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Screenshot save failed: {e}")
            screenshot_path = None

    db = _get_db()
    now = time.time()
    row = await db.execute_returning(
        """INSERT INTO feedbacks
           (user_id, email, category, rating, content, screenshot_path, session_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           RETURNING id""",
        (user_id, email or None, category, rating, content, screenshot_path, session_id or None, now)
    )
    feedback_id = row['id'] if row else None

    logger.info(f"Feedback submitted: id={feedback_id} category={category} rating={rating}")
    return web.json_response({'success': True, 'id': feedback_id}, status=201)


# ── Route Setup ───────────────────────────────────────────────────

def setup_help_routes(app: web.Application):
    """Register help routes."""
    app.router.add_post('/api/help/feedback', post_feedback_handler)
    logger.info("Help routes registered")
