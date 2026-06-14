#!/usr/bin/env python3
"""
B2B Data Migration Script — Phase 4A

Maps pre-auth legacy data to real users and organisations.

What it migrates:
  A. user_data.db  : user_sources rows with legacy user_id → real user_id + org_id
  B. analytics DB  : adds org_id column; tags historical rows with a sentinel org tag
  C. Qdrant nlweb_user_data    : updates payload user_id + org_id
  D. Qdrant nlweb_conversations: updates payload user_id

All steps are idempotent — running the script twice is safe.

Usage examples:
  # Dry-run: see what would change
  python scripts/migrate_to_b2b.py --target-email you@example.com --dry-run

  # Actually migrate
  python scripts/migrate_to_b2b.py --target-email you@example.com

  # List registered users
  python scripts/migrate_to_b2b.py --list-users

  # Custom legacy ids
  python scripts/migrate_to_b2b.py --target-email you@example.com \\
      --legacy-user-id old_user --legacy-analytics-user anon
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

# ── Path setup ───────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CODE_PYTHON = PROJECT_ROOT / "code" / "python"

sys.path.insert(0, str(CODE_PYTHON))

# Default DB paths
DEFAULT_AUTH_DB     = PROJECT_ROOT / "data" / "auth" / "auth.db"
DEFAULT_USERDATA_DB = PROJECT_ROOT / "data" / "user_data" / "user_data.db"
DEFAULT_ANALYTICS_DB = PROJECT_ROOT / "data" / "analytics" / "query_logs.db"

LEGACY_ORG_TAG = "legacy"          # sentinel stored in org_id for pre-B2B analytics rows
LEGACY_USER_ID_DEFAULT = "demo_user_001"
LEGACY_ANALYTICS_USER_DEFAULT = "anonymous"


# ── Helpers ──────────────────────────────────────────────────────

def _sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_user_and_org(auth_db: Path, email: str):
    """Look up (user_id, org_id) for the given email."""
    conn = _sqlite(auth_db)
    try:
        c = conn.cursor()
        c.execute("SELECT id, email, name FROM users WHERE email = ?", (email.lower(),))
        user = c.fetchone()
        if not user:
            raise SystemExit(f"ERROR: No user with email '{email}' in auth.db")

        c.execute(
            "SELECT org_id, role FROM org_memberships WHERE user_id = ? AND status = 'active' LIMIT 1",
            (user['id'],)
        )
        mem = c.fetchone()
        if not mem:
            raise SystemExit(
                f"ERROR: User '{email}' has no active org membership.\n"
                "       Create or join an org first, then re-run migration."
            )
        return user['id'], mem['org_id'], user['name']
    finally:
        conn.close()


def _list_users(auth_db: Path):
    conn = _sqlite(auth_db)
    try:
        c = conn.cursor()
        c.execute("""
            SELECT u.id, u.email, u.name,
                   m.org_id, o.name as org_name, m.role
            FROM users u
            LEFT JOIN org_memberships m ON m.user_id = u.id AND m.status = 'active'
            LEFT JOIN organizations o ON o.id = m.org_id
            ORDER BY u.created_at
        """)
        rows = c.fetchall()
        print(f"{'Email':<35} {'Name':<15} {'Role':<10} {'Org'}")
        print("-" * 75)
        for row in rows:
            print(f"{row['email']:<35} {row['name'] or '':<15} {row['role'] or '':<10} {row['org_name'] or '(no org)'}")
    finally:
        conn.close()


# ── Step A: user_data.db migration ───────────────────────────────

def _migrate_user_sources(
    db_path: Path,
    legacy_user_id: str,
    real_user_id: str,
    org_id: str,
    dry_run: bool,
) -> int:
    """Update user_sources rows where user_id == legacy_user_id and org_id IS NULL."""
    if not db_path.exists():
        print(f"  [SKIP] {db_path} not found")
        return 0

    conn = _sqlite(db_path)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM user_sources WHERE user_id = ? AND (org_id IS NULL OR org_id = '')",
            (legacy_user_id,)
        )
        count = c.fetchone()[0]
        if count == 0:
            print(f"  [OK]   user_sources: no rows to migrate (already done or no legacy data)")
            return 0

        print(f"  {'[DRY]' if dry_run else '[MIGRATE]'} user_sources: {count} rows "
              f"'{legacy_user_id}' → '{real_user_id}' (org_id={org_id})")

        if not dry_run:
            c.execute(
                "UPDATE user_sources SET user_id = ?, org_id = ?, updated_at = ? "
                "WHERE user_id = ? AND (org_id IS NULL OR org_id = '')",
                (real_user_id, org_id, time.time(), legacy_user_id)
            )
            conn.commit()
            print(f"         → {c.rowcount} rows updated")
        return count
    finally:
        conn.close()


# ── Step B: Analytics DB migration ───────────────────────────────

def _migrate_analytics(
    db_path: Path,
    legacy_analytics_user: str,
    org_id: str,
    dry_run: bool,
) -> int:
    """
    1. Add org_id column to queries table if missing.
    2. Set org_id = LEGACY_ORG_TAG for rows where user_id == legacy_analytics_user
       and org_id IS NULL.
    """
    if not db_path.exists():
        print(f"  [SKIP] {db_path} not found")
        return 0

    conn = _sqlite(db_path)
    try:
        c = conn.cursor()

        # 1. Add org_id column if missing
        c.execute("PRAGMA table_info(queries)")
        existing_cols = {row[1] for row in c.fetchall()}
        if 'org_id' not in existing_cols:
            if not dry_run:
                c.execute("ALTER TABLE queries ADD COLUMN org_id TEXT")
                conn.commit()
                print("  [MIGRATE] analytics/queries: added org_id column")
            else:
                print("  [DRY]    analytics/queries: would add org_id column")
        else:
            print("  [OK]   analytics/queries: org_id column already exists")

        # 2. Tag legacy rows
        # If column was just added (or dry-run skipped adding it), it may not exist yet.
        # Fall back to counting all rows with the legacy user_id.
        col_exists_now = 'org_id' in existing_cols or not dry_run
        if col_exists_now:
            c.execute(
                "SELECT COUNT(*) FROM queries WHERE user_id = ? AND (org_id IS NULL OR org_id = '')",
                (legacy_analytics_user,)
            )
        else:
            # Column doesn't exist yet (dry-run): count all legacy-user rows
            c.execute(
                "SELECT COUNT(*) FROM queries WHERE user_id = ?",
                (legacy_analytics_user,)
            )
        count = c.fetchone()[0]
        if count == 0:
            print("  [OK]   analytics/queries: no legacy rows to tag")
            return 0

        tag = LEGACY_ORG_TAG
        print(f"  {'[DRY]' if dry_run else '[MIGRATE]'} analytics/queries: {count} rows "
              f"user_id='{legacy_analytics_user}' → org_id='{tag}'")

        if not dry_run:
            c.execute(
                "UPDATE queries SET org_id = ? "
                "WHERE user_id = ? AND (org_id IS NULL OR org_id = '')",
                (tag, legacy_analytics_user)
            )
            conn.commit()
            print(f"         → {c.rowcount} rows tagged")
        return count
    finally:
        conn.close()


# ── Step C+D: Qdrant migration ────────────────────────────────────

async def _qdrant_update_payload(
    client,
    collection_name: str,
    filter_field: str,
    filter_value: str,
    new_payload: dict,
    dry_run: bool,
    batch_size: int = 100,
) -> int:
    """Scroll through points matching filter and overwrite payload fields."""
    from qdrant_client.http import models

    updated = 0
    next_offset = None

    while True:
        results, next_offset = await client.scroll(
            collection_name=collection_name,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(
                    key=filter_field,
                    match=models.MatchValue(value=filter_value),
                )]
            ),
            limit=batch_size,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )

        if not results:
            break

        ids = [p.id for p in results]
        if not dry_run:
            await client.set_payload(
                collection_name=collection_name,
                payload=new_payload,
                points=ids,
            )
        updated += len(ids)

        if next_offset is None:
            break

    return updated


async def _migrate_qdrant(
    legacy_user_id: str,
    real_user_id: str,
    org_id: str,
    dry_run: bool,
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    user_data_collection: str = "nlweb_user_data",
    conv_collection: str = "nlweb_conversations",
    user_data_db_path: Optional[str] = None,
    conv_db_path: Optional[str] = None,
):
    try:
        from qdrant_client import AsyncQdrantClient
    except ImportError:
        print("  [SKIP] qdrant-client not installed — skipping Qdrant migration")
        return

    async def _try_collection(client, collection_name: str, label: str, new_payload: dict):
        try:
            collections = await client.get_collections()
            names = [c.name for c in collections.collections]
            if collection_name not in names:
                print(f"  [SKIP] Qdrant collection '{collection_name}' not found")
                return

            count = await _qdrant_update_payload(
                client, collection_name,
                filter_field="user_id",
                filter_value=legacy_user_id,
                new_payload=new_payload,
                dry_run=dry_run,
            )
            verb = "[DRY]" if dry_run else "[MIGRATE]"
            print(f"  {verb} Qdrant/{collection_name} ({label}): {count} points updated")
        except Exception as e:
            print(f"  [WARN] Qdrant/{collection_name}: {e}")

    # Helper to open a client and run migrations
    async def _run_with_client(client):
        await _try_collection(
            client, user_data_collection, "user_data",
            new_payload={"user_id": real_user_id, "org_id": org_id},
        )
        await _try_collection(
            client, conv_collection, "conversations",
            new_payload={"user_id": real_user_id},
        )
        await client.close()

    if qdrant_url:
        print(f"  Connecting to Qdrant at {qdrant_url} …")
        client = AsyncQdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        await _run_with_client(client)
    else:
        # Try local paths
        paths_tried = []
        for path in [user_data_db_path, conv_db_path,
                     str(PROJECT_ROOT / "data" / "qdrant"),
                     str(PROJECT_ROOT / "data" / "db")]:
            if not path or path in paths_tried:
                continue
            paths_tried.append(path)
            try:
                client = AsyncQdrantClient(path=path)
                # Quick sanity check
                await client.get_collections()
                print(f"  Using local Qdrant at: {path}")
                await _run_with_client(client)
                return
            except Exception:
                pass

        print("  [SKIP] No reachable Qdrant instance found "
              "(set QDRANT_URL env var to connect to remote)")


# ── Main ─────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Migrate pre-auth legacy data to B2B user/org model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--target-email", metavar="EMAIL",
                        help="Email of the real user to receive legacy data")
    parser.add_argument("--org-id", metavar="ORG_ID",
                        help="Override org_id (default: user's first active org)")
    parser.add_argument("--legacy-user-id", default=LEGACY_USER_ID_DEFAULT,
                        help=f"Legacy user_id in user_data.db (default: {LEGACY_USER_ID_DEFAULT})")
    parser.add_argument("--legacy-analytics-user", default=LEGACY_ANALYTICS_USER_DEFAULT,
                        help=f"Legacy user_id in analytics (default: {LEGACY_ANALYTICS_USER_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without applying")
    parser.add_argument("--list-users", action="store_true",
                        help="List registered users and exit")

    # Path overrides
    parser.add_argument("--auth-db", default=str(DEFAULT_AUTH_DB),
                        help="Path to auth.db")
    parser.add_argument("--userdata-db", default=str(DEFAULT_USERDATA_DB),
                        help="Path to user_data.db")
    parser.add_argument("--analytics-db", default=str(DEFAULT_ANALYTICS_DB),
                        help="Path to query_logs.db")

    # Qdrant
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"),
                        help="Qdrant server URL (default: $QDRANT_URL)")
    parser.add_argument("--qdrant-api-key", default=os.environ.get("QDRANT_API_KEY"),
                        help="Qdrant API key (default: $QDRANT_API_KEY)")
    parser.add_argument("--user-data-collection", default="nlweb_user_data")
    parser.add_argument("--conv-collection", default="nlweb_conversations")

    args = parser.parse_args()

    auth_db      = Path(args.auth_db)
    userdata_db  = Path(args.userdata_db)
    analytics_db = Path(args.analytics_db)

    if args.list_users:
        _list_users(auth_db)
        return

    if not args.target_email:
        parser.error("--target-email is required (use --list-users to see registered users)")

    # ── Resolve target user ──────────────────────────────────────
    real_user_id, inferred_org_id, user_name = _get_user_and_org(auth_db, args.target_email)
    org_id = args.org_id or inferred_org_id

    print()
    print("═" * 60)
    if args.dry_run:
        print("  DRY-RUN MODE — no changes will be written")
    print(f"  Target user  : {args.target_email}  ({user_name})")
    print(f"  User ID      : {real_user_id}")
    print(f"  Org ID       : {org_id}")
    print(f"  Legacy user  : {args.legacy_user_id}")
    print(f"  Legacy anlyt : {args.legacy_analytics_user}")
    print("═" * 60)
    print()

    total_changed = 0

    # ── Step A: user_data.db ─────────────────────────────────────
    print("Step A — user_sources (user_data.db)")
    total_changed += _migrate_user_sources(
        userdata_db,
        legacy_user_id=args.legacy_user_id,
        real_user_id=real_user_id,
        org_id=org_id,
        dry_run=args.dry_run,
    )
    print()

    # ── Step B: Analytics ────────────────────────────────────────
    print("Step B — analytics queries (query_logs.db)")
    total_changed += _migrate_analytics(
        analytics_db,
        legacy_analytics_user=args.legacy_analytics_user,
        org_id=org_id,
        dry_run=args.dry_run,
    )
    print()

    # ── Step C+D: Qdrant ─────────────────────────────────────────
    print("Step C+D — Qdrant collections")
    await _migrate_qdrant(
        legacy_user_id=args.legacy_user_id,
        real_user_id=real_user_id,
        org_id=org_id,
        dry_run=args.dry_run,
        qdrant_url=args.qdrant_url,
        qdrant_api_key=args.qdrant_api_key,
        user_data_collection=args.user_data_collection,
        conv_collection=args.conv_collection,
    )
    print()

    # ── Summary ──────────────────────────────────────────────────
    print("═" * 60)
    if args.dry_run:
        print(f"  DRY-RUN complete. ~{total_changed} rows would be affected.")
        print("  Re-run without --dry-run to apply.")
    else:
        print(f"  Migration complete. {total_changed} rows updated.")
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(main())
