"""
CLI tool to generate bootstrap tokens for B2B customer onboarding.

Usage:
    cd code/python
    python -m auth.bootstrap_cli --org "Company Name" --expires 72
"""

import argparse
import asyncio
import sys
import os
import platform

# Windows needs SelectorEventLoop for psycopg async
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from auth.auth_db import AuthDB
from auth.auth_service import AuthService


async def main(org_name_hint: str, expires_hours: int):
    db = AuthDB.get_instance()
    await db.initialize()

    service = AuthService()
    result = await service.create_bootstrap_token(
        org_name_hint=org_name_hint,
        expires_hours=expires_hours,
    )

    print()
    print("=" * 60)
    print("  Bootstrap Token Created")
    print("=" * 60)
    print(f"  Org hint : {org_name_hint or '(none)'}")
    print(f"  Expires  : {expires_hours} hours")
    print(f"  Token    : {result['token']}")
    print()
    print(f"  URL      : {result['url']}")
    print("=" * 60)
    print()
    print("Send this URL to the customer admin.")
    print()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate bootstrap token for B2B onboarding')
    parser.add_argument('--org', type=str, default='', help='Organization name hint (pre-fills the form)')
    parser.add_argument('--expires', type=int, default=72, help='Token expiry in hours (default: 72)')
    args = parser.parse_args()

    asyncio.run(main(args.org, args.expires))
