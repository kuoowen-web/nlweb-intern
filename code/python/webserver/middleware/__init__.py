"""Middleware package for aiohttp server"""

from .correlation import correlation_middleware
from .cors import cors_middleware
from .csp import csp_middleware
from .error_handler import error_middleware
from .logging_middleware import logging_middleware
from .rate_limit import rate_limit_middleware
from .auth import auth_middleware
from .upload_rate_limit import upload_rate_limit_middleware
from .streaming import streaming_middleware
from .static_no_cache import static_no_cache_middleware


def setup_middleware(app):
    """Setup all middleware in the correct order"""
    # Note: aiohttp applies middleware in the order it is appended, outermost first.
    # correlation_middleware must be first so that the correlation ID is available
    # to all subsequent middleware and handlers.
    app.middlewares.append(correlation_middleware)
    app.middlewares.append(error_middleware)
    app.middlewares.append(csp_middleware)
    app.middlewares.append(logging_middleware)
    app.middlewares.append(cors_middleware)
    app.middlewares.append(rate_limit_middleware)
    app.middlewares.append(auth_middleware)
    # upload_rate_limit must run AFTER auth_middleware so request['user'] is set
    # (per-user keying, docs/decisions.md:175-178). Only guards POST /api/user/upload;
    # all other requests pass through untouched.
    app.middlewares.append(upload_rate_limit_middleware)
    app.middlewares.append(streaming_middleware)
    # D-14 (Frontend Modular Refactor v3.3 Phase 1): emit Cache-Control: no-cache for
    # /static/js/ + /static/css/ URLs. Appended last so it sees the final response after
    # all upstream middleware have done their work and can safely override any cache
    # header set by aiohttp's static file handler.
    app.middlewares.append(static_no_cache_middleware)


__all__ = [
    'setup_middleware',
    'correlation_middleware',
    'cors_middleware',
    'csp_middleware',
    'error_middleware',
    'logging_middleware',
    'rate_limit_middleware',
    'auth_middleware',
    'upload_rate_limit_middleware',
    'streaming_middleware',
    'static_no_cache_middleware',
]