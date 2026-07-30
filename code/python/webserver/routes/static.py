"""Static file serving routes for aiohttp server"""

from aiohttp import web
import logging
import os
from pathlib import Path

from webserver.middleware.auth import _BLOG_SLUG_RE

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
    app.router.add_get('/', landing_handler)     # landing page（2026-07 起）
    app.router.add_get('/app', index_handler)    # 產品頁（原 /）
    app.router.add_get('/faq', faq_handler)      # FAQ 公開頁
    app.router.add_get('/blog', blog_list_handler)          # Blog 列表頁
    app.router.add_get('/blog/{slug}', blog_post_handler)   # Blog 單篇（slug 白名單驗證）

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


async def landing_handler(request: web.Request) -> web.Response:
    """Serve landing page for root path; fallback to app if landing missing."""

    static_path = request.app.get('static_path')
    if not static_path:
        return web.Response(text="Static files not configured", status=500)

    landing_file = static_path / 'landing' / 'index.html'
    if not landing_file.exists():
        # Defense-in-depth：landing 缺檔時 root 退回產品頁，不開天窗
        logger.error(f"landing/index.html not found at {landing_file}, falling back to app")
        return await index_handler(request)

    return web.FileResponse(
        landing_file,
        headers={
            'Cache-Control': 'no-cache',
            'Content-Type': 'text/html; charset=utf-8'
        }
    )


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


async def faq_handler(request: web.Request) -> web.Response:
    """Serve /faq 公開 FAQ 頁。"""
    static_path = request.app.get('static_path')
    if not static_path:
        return web.Response(text="Static files not configured", status=500)

    faq_file = static_path / 'landing' / 'faq.html'
    if not faq_file.exists():
        logger.error(f"faq.html not found at {faq_file}")
        return web.Response(text="faq.html not found", status=404)

    return web.FileResponse(
        faq_file,
        headers={
            'Cache-Control': 'no-cache',
            'Content-Type': 'text/html; charset=utf-8'
        }
    )


async def blog_list_handler(request: web.Request) -> web.Response:
    """Serve /blog 部落格列表頁。"""
    static_path = request.app.get('static_path')
    if not static_path:
        return web.Response(text="Static files not configured", status=500)

    blog_file = static_path / 'landing' / 'blog.html'
    if not blog_file.exists():
        logger.error(f"blog.html not found at {blog_file}")
        return web.Response(text="blog.html not found", status=404)

    return web.FileResponse(
        blog_file,
        headers={
            'Cache-Control': 'no-cache',
            'Content-Type': 'text/html; charset=utf-8'
        }
    )


async def blog_post_handler(request: web.Request) -> web.Response:
    """Serve /blog/<slug> 單篇。slug 過白名單 regex 才組路徑（防 path traversal）。"""
    static_path = request.app.get('static_path')
    if not static_path:
        return web.Response(text="Static files not configured", status=500)

    slug = request.match_info.get('slug', '')
    # 未過白名單（含 . / \ 空字串）→ 404，不組路徑
    if not _BLOG_SLUG_RE.match(slug):
        return web.Response(text="Not found", status=404)

    post_file = static_path / 'landing' / 'blog' / f'{slug}.html'
    if not post_file.exists():
        return web.Response(text="Not found", status=404)

    return web.FileResponse(
        post_file,
        headers={
            'Cache-Control': 'no-cache',
            'Content-Type': 'text/html; charset=utf-8'
        }
    )
