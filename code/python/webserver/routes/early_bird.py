"""
Landing page 早鳥名單 API。

POST /api/early-bird — public，存 early_bird_signups + email 通知
（awaited + 5s timeout：DB commit 後同步等通知、逾時/失敗只 log 不影響 201）
防護：honeypot 欄位（website）+ rate limit（rate_limit.py 5/hr per IP）
"""

import re
import time
import asyncio

from aiohttp import web
from misc.logger.logging_config_helper import get_configured_logger
from auth.email_service import send_early_bird_notification
from webserver.middleware.ip_utils import get_client_ip

logger = get_configured_logger("early_bird_routes")

# 通知 email 最長等待秒數——抽成模組常數讓測試可 monkeypatch（timeout 分支專測，R2-S2）
NOTIFY_TIMEOUT_SECONDS = 5.0

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# (欄位, 必填, 最大長度) — 與 alembic migration / landing 表單 maxlength 一致
_FIELDS = [
    ('name', True, 100),
    ('email', True, 255),
    ('company', False, 200),
    ('job_title', False, 100),
    ('purpose', False, 500),
]


def _get_db():
    from auth.auth_db import AuthDB
    return AuthDB.get_instance()


async def post_early_bird_handler(request: web.Request) -> web.Response:
    """POST /api/early-bird — 早鳥名單報名（public endpoint）。"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    # JSON 合法但非 object（array/str/number/null）→ 後續 body.get() 會 AttributeError；
    # public endpoint 不可 5xx，統一回 400。
    if not isinstance(body, dict):
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    # Honeypot：bot 填了隱藏欄 → 裝成功、不落庫（不讓 bot 學到分辨訊號）
    if body.get('website'):
        logger.info("Early-bird honeypot triggered, dropping submission")
        return web.json_response({'success': True, 'id': 0}, status=201)

    values = {}
    for field, required, max_len in _FIELDS:
        raw = body.get(field, '')
        if not isinstance(raw, str):
            return web.json_response({'error': f'{field} must be a string'}, status=400)
        val = raw.strip()
        if required and not val:
            return web.json_response({'error': f'{field} is required'}, status=400)
        if len(val) > max_len:
            return web.json_response(
                {'error': f'{field} must be {max_len} characters or less'}, status=400)
        if any(c in val for c in '\r\n') and field != 'purpose':
            return web.json_response({'error': f'{field} must be single-line'}, status=400)
        values[field] = val or None

    if not _EMAIL_RE.match(values['email'] or ''):
        return web.json_response({'error': 'email format is invalid'}, status=400)

    db = _get_db()
    row = await db.execute_returning(
        """INSERT INTO early_bird_signups
           (name, email, company, job_title, purpose, client_ip, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           RETURNING id""",
        (values['name'], values['email'], values['company'],
         values['job_title'], values['purpose'], get_client_ip(request), time.time())
    )
    signup_id = row['id'] if row else None
    logger.info(f"Early-bird signup stored: id={signup_id} email={values['email']}")

    # 通知 CEO — awaited + 短 timeout：Resend 卡住最多拖 5s，不會無限吊住使用者提交；
    # 逾時/失敗只 log，不影響 201（不可 silent fail：必留 error log）。
    # timeout 時 wait_for 取消 future、executor thread 自行跑完收尾，回應照常返回。
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None, send_early_bird_notification,
                values['name'], values['email'], values['company'],
                values['job_title'], values['purpose']),
            timeout=NOTIFY_TIMEOUT_SECONDS)
    except Exception as e:
        logger.error(f"Early-bird notification email failed (signup id={signup_id}): {e}",
                     exc_info=True)

    return web.json_response({'success': True, 'id': signup_id}, status=201)


def setup_early_bird_routes(app: web.Application):
    """Register early-bird routes."""
    app.router.add_post('/api/early-bird', post_early_bird_handler)
    logger.info("Early-bird routes registered")
