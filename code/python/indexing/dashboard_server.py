#!/usr/bin/env python3
"""
dashboard_server.py - Indexing Dashboard standalone server

A separate aiohttp server for the Indexing Dashboard, running on port 8001.
This keeps the dashboard isolated from the main NLWeb server.

Usage:
    python -m indexing.dashboard_server
    # or
    python code/python/indexing/dashboard_server.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from aiohttp import web

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# Suppress noisy aiohttp access logs (GET /api/... every 5 seconds)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

# Add paths for imports
CODE_DIR = Path(__file__).parent.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

# Also add nlweb root for static files
NLWEB_ROOT = CODE_DIR.parent.parent
STATIC_DIR = NLWEB_ROOT / "static"


async def create_app() -> web.Application:
    """Create and configure the dashboard application"""
    app = web.Application()

    # Setup CORS middleware
    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            response = web.Response()
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

        # Skip CORS headers for WebSocket responses (already upgraded)
        if not isinstance(response, web.WebSocketResponse):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"

        return response

    app.middlewares.append(cors_middleware)

    # Setup API routes
    from indexing.dashboard_api import setup_routes
    setup_routes(app)

    # Serve static files
    app.router.add_static("/static", STATIC_DIR)

    # Redirect root to dashboard
    async def index_handler(request: web.Request) -> web.Response:
        raise web.HTTPFound("/static/indexing-dashboard.html")

    app.router.add_get("/", index_handler)

    # Health check
    async def health_handler(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "indexing-dashboard"})

    app.router.add_get("/health", health_handler)

    return app


async def main():
    """Main entry point"""
    port = int(os.environ.get("DASHBOARD_PORT", 8001))
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")

    app = await create_app()

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(runner, host, port)

    logger.info(f"Starting Indexing Dashboard server at http://{host}:{port}")
    logger.info(f"Dashboard URL: http://localhost:{port}")

    await site.start()

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
