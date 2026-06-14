"""
One-shot cleanup: remove SSE intermediate envelope entries from
search_sessions.session_history that were persisted before the
client+server fix landed.

Usage (run from code/python/):
    python -m scripts.cleanup_polluted_session_history --dry-run
    python -m scripts.cleanup_polluted_session_history --commit

Idempotent — re-running with --commit on already-clean data is a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from auth.auth_db import AuthDB
from core.session_service import SessionService
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("cleanup_polluted_session_history")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='show what would change without writing')
    parser.add_argument('--commit', action='store_true',
                        help='write the cleanup back to PG/SQLite')
    args = parser.parse_args()

    if not args.dry_run and not args.commit:
        print('error: must pass --dry-run or --commit', file=sys.stderr)
        sys.exit(2)

    db = AuthDB.get_instance()
    if hasattr(db, 'initialize'):
        try:
            await db.initialize()
        except Exception:
            # initialize() may not exist or already be done; tolerate.
            pass

    rows = await db.fetchall(
        "SELECT id, user_id, org_id, session_history FROM search_sessions "
        "WHERE deleted_at IS NULL"
    )

    total = len(rows)
    polluted_count = 0
    dropped_total = 0

    for row in rows:
        sh_raw = row.get('session_history')
        if isinstance(sh_raw, str):
            try:
                sh = json.loads(sh_raw)
            except (json.JSONDecodeError, TypeError):
                continue
        elif isinstance(sh_raw, list):
            sh = sh_raw
        else:
            continue

        clean = SessionService._sanitize_session_history(sh)
        if len(clean) == len(sh):
            continue

        polluted_count += 1
        dropped_total += len(sh) - len(clean)
        logger.info(
            f"session={row['id']} user={row['user_id']} org={row['org_id']}: "
            f"{len(sh)} → {len(clean)} entries (dropped {len(sh) - len(clean)})"
        )

        if args.commit:
            # Use the same `?` placeholder convention as session_service.update_session;
            # AuthDB._adapt_query_pg auto-translates to %s for PG.
            await db.execute(
                "UPDATE search_sessions SET session_history = ? WHERE id = ?",
                (json.dumps(clean), row['id'])
            )

    logger.info(
        f"Done. Total sessions scanned: {total}. "
        f"Polluted: {polluted_count}. Total entries dropped: {dropped_total}. "
        f"Mode: {'COMMIT' if args.commit else 'DRY-RUN'}"
    )


if __name__ == '__main__':
    asyncio.run(main())
