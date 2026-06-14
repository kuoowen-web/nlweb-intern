"""Correlation ID middleware — injects a per-request ID into every log record."""

import uuid
from aiohttp import web
from misc.logger.logger import correlation_id_var


@web.middleware
async def correlation_middleware(request: web.Request, handler) -> web.Response:
    """Attach a correlation ID to every request and echo it in the response header."""
    corr_id = request.headers.get('X-Correlation-ID') or f"req_{uuid.uuid4().hex[:12]}"
    correlation_id_var.set(corr_id)
    response = await handler(request)
    response.headers['X-Correlation-ID'] = corr_id
    return response
