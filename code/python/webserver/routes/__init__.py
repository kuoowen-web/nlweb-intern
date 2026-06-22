"""Routes package for aiohttp server"""

from .static import setup_static_routes
from .api import setup_api_routes
from .health import setup_health_routes
from .mcp import setup_mcp_routes
from .a2a import setup_a2a_routes
from .user_data import setup_user_data_routes
from .auth import setup_auth_routes
from .sessions import setup_session_routes
from .audit import setup_audit_routes
from .help import setup_help_routes
from .user_init import setup_user_init_routes
from .admin import setup_admin_routes

# Analytics routes (from parent webserver directory)
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analytics_handler import register_analytics_routes


def setup_routes(app):
    """Setup all routes for the application"""
    setup_static_routes(app)
    setup_api_routes(app)
    setup_health_routes(app)
    setup_mcp_routes(app)
    setup_a2a_routes(app)
    setup_user_data_routes(app)
    setup_auth_routes(app)
    setup_session_routes(app)
    setup_audit_routes(app)
    setup_help_routes(app)
    setup_user_init_routes(app)
    setup_admin_routes(app)

    # Register analytics routes with correct database path
    # Use environment variable if available (for Render persistent disk), otherwise use default
    import os
    analytics_db_path = os.environ.get('ANALYTICS_DB_PATH', 'data/analytics/query_logs.db')
    try:
        register_analytics_routes(app, db_path=analytics_db_path)
        
        # Register ranking analytics routes
        from ranking_analytics_handler import register_ranking_analytics_routes
        register_ranking_analytics_routes(app, db_path=analytics_db_path)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to register analytics routes: {e}", exc_info=True)


__all__ = ['setup_routes']