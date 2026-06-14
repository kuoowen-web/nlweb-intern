"""Add live_research_state column to search_sessions table.

Usage:
    cd code/python && python tools/migrate_live_research.py

Supports:
    - SQLite: adds TEXT column (serialized JSON string)
    - PostgreSQL: adds JSONB column (native JSON)

Note: Run this migration once per environment before using the Live Research feature.
The column stores LiveResearchStageState as JSON, keyed by session_id.
"""

import asyncio
import sys
sys.path.insert(0, '.')

# Windows requires SelectorEventLoop for asyncio + psycopg compatibility
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from auth.auth_db import AuthDB
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("migrate_live_research")


async def migrate():
    db = AuthDB.get_instance()
    await db.initialize()

    if db.db_type == 'sqlite':
        # SQLite: TEXT column (JSON stored as string)
        try:
            await db.execute(
                "ALTER TABLE search_sessions ADD COLUMN live_research_state TEXT DEFAULT '{}'"
            )
            logger.info("Migration complete: added live_research_state TEXT column (SQLite)")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                logger.info("Column live_research_state already exists — skipping (SQLite)")
            else:
                raise
    else:
        # PostgreSQL: JSONB column (native JSON with indexing support)
        try:
            await db.execute(
                "ALTER TABLE search_sessions ADD COLUMN IF NOT EXISTS live_research_state JSONB DEFAULT '{}'"
            )
            logger.info("Migration complete: added live_research_state JSONB column (PostgreSQL)")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info("Column live_research_state already exists — skipping (PostgreSQL)")
            else:
                raise


if __name__ == "__main__":
    asyncio.run(migrate())
