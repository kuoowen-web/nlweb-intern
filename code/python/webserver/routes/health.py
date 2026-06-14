"""Health check routes for aiohttp server"""

import asyncio
import json
import os
import logging
import time
from datetime import datetime, timezone

from aiohttp import web


def _json_health_response(data: dict, status: int = 200) -> web.Response:
    """Return a JSON response compatible with HTTP/1.0 (no chunked encoding).

    Cloudflare health checks use HTTP/1.0 which forbids chunked transfer
    encoding.  aiohttp's json_response() uses chunked by default, causing
    ``RuntimeError: Using chunked encoding is forbidden for HTTP/1.0``
    deep inside response serialization — uncatchable by handler try/except.
    """
    body = json.dumps(data).encode('utf-8')
    return web.Response(
        body=body,
        status=status,
        content_type='application/json',
    )

logger = logging.getLogger(__name__)

# Server start time
SERVER_START_TIME = time.time()


def setup_health_routes(app: web.Application):
    """Setup health check routes"""
    app.router.add_get('/health', health_check)
    app.router.add_get('/ready', readiness_check)


async def _check_database() -> str | None:
    """Run SELECT 1 against the configured PostgreSQL database.

    Returns None on success, or an error string on failure.
    """
    database_url = (
        os.environ.get('POSTGRES_CONNECTION_STRING')
        or os.environ.get('DATABASE_URL')
        or os.environ.get('ANALYTICS_DATABASE_URL')
    )

    if not database_url:
        # No PG configured — not an error (dev mode uses SQLite)
        return None

    try:
        import psycopg

        async def _ping():
            async with await psycopg.AsyncConnection.connect(
                database_url, connect_timeout=3
            ) as conn:
                await conn.execute("SELECT 1")

        await asyncio.wait_for(_ping(), timeout=3.0)
        return None
    except asyncio.TimeoutError:
        return "timeout after 3s"
    except Exception as e:
        # Return a safe, non-sensitive message
        return f"connection failed: {type(e).__name__}"


async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint with database connectivity verification.

    Safety net: this handler must NEVER raise — nginx interprets an
    unfinished response as 502 Bad Gateway.
    """
    try:
        checks: dict = {}

        db_error = await _check_database()
        if db_error is not None:
            checks['database'] = db_error

        timestamp = datetime.now(timezone.utc).isoformat()

        if not checks:
            return _json_health_response(
                {'status': 'healthy', 'timestamp': timestamp},
                status=200,
            )

        return _json_health_response(
            {'status': 'unhealthy', 'checks': checks, 'timestamp': timestamp},
            status=503,
        )
    except Exception as e:
        logger.error(f"Health check handler crashed: {e}", exc_info=True)
        return _json_health_response(
            {
                'status': 'error',
                'error': f'{type(e).__name__}: {e}',
                'timestamp': datetime.now(timezone.utc).isoformat(),
            },
            status=503,
        )


async def readiness_check(request: web.Request) -> web.Response:
    """Readiness check - verifies all dependencies are available"""

    checks = {}
    all_ready = True

    # Check static files
    static_path = request.app.get('static_path')
    if static_path and static_path.exists():
        checks['static_files'] = True
    else:
        checks['static_files'] = False
        all_ready = False

    # Check client session
    if request.app.get('client_session'):
        checks['http_client'] = True
    else:
        checks['http_client'] = False
        all_ready = False

    status_code = 200 if all_ready else 503

    return _json_health_response({
        'status': 'ready' if all_ready else 'not_ready',
        'checks': checks,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }, status=status_code)
