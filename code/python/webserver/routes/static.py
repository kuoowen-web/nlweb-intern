"""Static file serving routes for aiohttp server"""

from aiohttp import web
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_static_routes(app: web.Application):
    """Setup static file serving routes"""

    config = app.get('config', {})
    static_dir = config.get('static_directory', '../static')

    # Use NLWEB_STATIC_DIR environment variable if available
    env_static_dir = os.environ.get('NLWEB_STATIC_DIR')
    if env_static_dir:
        static_path = Path(env_static_dir)
    elif static_dir.startswith('/'):
        # Absolute path in config
        static_path = Path(static_dir)
    else:
        # Convert relative path to absolute
        base_path = Path(__file__).parent.parent.parent.parent.parent
        static_path = base_path / static_dir.lstrip('../')
    
    if not static_path.exists():
        logger.warning(f"Static directory not found at {static_path}")
        # Try alternate path
        static_path = Path(__file__).parent.parent / 'static'
        if not static_path.exists():
            logger.error("Could not find static directory")
            return
    
    logger.info(f"Serving static files from: {static_path}")
    
    # Serve index.html for root path
    app.router.add_get('/', index_handler)

    # Serve favicon.ico from favicon.png
    app.router.add_get('/favicon.ico', favicon_handler)

    # Serve static files
    app.router.add_static(
        '/static/', 
        path=static_path,
        name='static',
        show_index=False,
        follow_symlinks=True
    )
    
    # Serve HTML files
    html_path = static_path / 'html'
    if html_path.exists():
        app.router.add_static(
            '/html/', 
            path=html_path,
            name='html',
            show_index=False,
            follow_symlinks=True
        )
    
    # Serve .well-known/ directory (dot-prefix dirs are not served by add_static)
    well_known_path = static_path / '.well-known'
    if well_known_path.exists():
        app.router.add_static(
            '/.well-known/',
            path=well_known_path,
            name='well_known',
            show_index=False,
            follow_symlinks=False
        )
        logger.info(f"Serving .well-known/ from: {well_known_path}")

    # Store static path in app for use in handlers
    app['static_path'] = static_path


async def index_handler(request: web.Request) -> web.Response:
    """Serve index.html for root path"""

    static_path = request.app.get('static_path')
    if not static_path:
        return web.Response(text="Static files not configured", status=500)

    index_file = static_path / 'news-search-prototype.html'

    if not index_file.exists():
        logger.error(f"news-search-prototype.html not found at {index_file}")
        return web.Response(text="news-search-prototype.html not found", status=404)

    return web.FileResponse(
        index_file,
        headers={
            'Cache-Control': 'no-cache',
            'Content-Type': 'text/html; charset=utf-8'
        }
    )


async def favicon_handler(request: web.Request) -> web.Response:
    """Serve favicon.png as favicon.ico"""

    static_path = request.app.get('static_path')
    if not static_path:
        return web.Response(status=404)

    favicon_file = static_path / 'favicon.png'
    if not favicon_file.exists():
        return web.Response(status=404)

    return web.FileResponse(
        favicon_file,
        headers={'Cache-Control': 'public, max-age=86400', 'Content-Type': 'image/png'}
    )