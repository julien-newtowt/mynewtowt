"""Wipe and recreate the my_TOWT database, then seed minimal bootstrap data.

This script is destructive: it drops the entire `public` schema and recreates
all tables from `Base.metadata`. Used post-TOWT liquidation to start from a
clean state before any V2 work.

Safety guards:
- Requires environment variable ALLOW_DB_RESET=yes
- Requires interactive confirmation (type RESET when prompted)
- Logs full action summary at the end

Run via:

    docker exec -e ALLOW_DB_RESET=yes -it towt-app-v2 python3 scripts/reset_database.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make `app` importable when the script is invoked directly (python3 scripts/reset_database.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text

# Importing app.models forces every model to register on Base.metadata.
import app.models  # noqa: F401  (side-effect: full model registration)
from app.database import Base, engine
from scripts.bootstrap_minimal import bootstrap_minimal


CONFIRMATION_TOKEN = "RESET"


def _check_guards() -> None:
    if os.environ.get("ALLOW_DB_RESET") != "yes":
        print("ERROR: ALLOW_DB_RESET=yes is required to run this script.", file=sys.stderr)
        print("Re-run with:", file=sys.stderr)
        print("  docker exec -e ALLOW_DB_RESET=yes -it towt-app-v2 python3 scripts/reset_database.py", file=sys.stderr)
        sys.exit(2)

    print("⚠  This will DROP the entire `public` schema and recreate every table.")
    print("⚠  All production data will be permanently destroyed.")
    print()
    answer = input(f'Type "{CONFIRMATION_TOKEN}" (uppercase) to confirm: ').strip()
    if answer != CONFIRMATION_TOKEN:
        print("Aborted — confirmation token did not match.")
        sys.exit(1)


async def _drop_and_recreate_schema() -> int:
    """Drop and recreate the public schema, then create all tables. Returns table count."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        # Restore default privileges granted by Postgres on a fresh schema
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        return int(result.scalar() or 0)


async def main_async() -> int:
    table_count = await _drop_and_recreate_schema()
    summary = await bootstrap_minimal()

    print()
    print("=" * 60)
    print("Database reset completed")
    print("=" * 60)
    print(f"  Tables created         : {table_count}")
    print(f"  Vessels seeded         : {summary['vessels_created']}")
    print(f"  Ports seeded           : {summary['ports_created']}")
    print(f"  Port configs seeded    : {summary['port_configs_created']}")
    print(f"  OPEX parameters seeded : {summary['opex_params_created']}")
    print(f"  Emission params seeded : {summary['emission_params_created']}")

    if summary["admin_password"]:
        print()
        print("┌" + "─" * 58 + "┐")
        print("│  ADMIN TEMPORARY PASSWORD (shown ONCE — store it now)    │")
        print("│" + " " * 58 + "│")
        print(f"│  username : admin{' ' * 40}│")
        print(f"│  password : {summary['admin_password']:<46}│")
        print("│" + " " * 58 + "│")
        print("│  must_change_password = True (forced rotation on login)  │")
        print("└" + "─" * 58 + "┘")
    return 0


def main() -> int:
    _check_guards()
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
