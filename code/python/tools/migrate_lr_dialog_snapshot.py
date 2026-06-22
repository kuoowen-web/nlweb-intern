"""Add lr_dialog_snapshot column to search_sessions table.

DEPRECATED: 已由 alembic migration 9863ee09ce82 取代（接在 head 7c2f4ae6b1d3 後）。
此欄位現在隨 `alembic upgrade head` 自動上 prod，不需再手動跑本腳本。
保留本腳本僅供歷史參考 / 緊急 out-of-band 修補。

Usage:
    cd code/python && python tools/migrate_lr_dialog_snapshot.py

Supports:
    - SQLite: adds TEXT column (serialized JSON string)
    - PostgreSQL: adds JSONB column (native JSON)

Note: Run this migration once per environment before using LR dialog
persistence (plan v3). The column stores the frontend DOM snapshot of the
#lrChat conversation as a JSON array, isolated from live_research_state
so the backend _save_state (which only writes live_research_state) never
overwrites it.
"""

import asyncio
import sys
sys.path.insert(0, '.')

# Windows requires SelectorEventLoop for asyncio + psycopg compatibility
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from auth.auth_db import AuthDB
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("migrate_lr_dialog_snapshot")


async def migrate():
    db = AuthDB.get_instance()
    await db.initialize()

    if db.db_type == 'sqlite':
        # SQLite: TEXT column (JSON array stored as string)
        try:
            await db.execute(
                "ALTER TABLE search_sessions ADD COLUMN lr_dialog_snapshot TEXT DEFAULT '[]'"
            )
            logger.info("Migration complete: added lr_dialog_snapshot TEXT column (SQLite)")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                logger.info("Column lr_dialog_snapshot already exists — skipping (SQLite)")
            else:
                raise
    else:
        # PostgreSQL: JSONB column (native JSON)
        try:
            await db.execute(
                "ALTER TABLE search_sessions ADD COLUMN IF NOT EXISTS lr_dialog_snapshot JSONB DEFAULT '[]'"
            )
            logger.info("Migration complete: added lr_dialog_snapshot JSONB column (PostgreSQL)")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info("Column lr_dialog_snapshot already exists — skipping (PostgreSQL)")
            else:
                raise


if __name__ == "__main__":
    asyncio.run(migrate())
