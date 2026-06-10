"""Reset a staff user's password directly against the database.

Usage (from project root):
  docker compose exec app python -m scripts.reset_password admin
  docker compose exec app python -m scripts.reset_password admin --password 'NewPass123!'
  python -m scripts.reset_password admin          # prompts for new password

If --password is omitted the script prompts interactively (hidden input).
Exits 1 if the username is not found or is inactive.
"""
from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy import select

from app.auth import hash_password
from app.database import SessionLocal, init_db
from app.models.user import User


async def reset(username: str, new_password: str) -> None:
    await init_db()
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            print(f"ERROR: user '{username}' not found.", file=sys.stderr)
            sys.exit(1)
        user.hashed_password = hash_password(new_password)
        user.is_active = True
        user.must_change_password = False
        await db.commit()
        print(f"OK — password reset for '{username}' (role: {user.role}).")
        print("You can now log in at /login.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Reset a staff user's password.")
    parser.add_argument("username", help="Username to reset")
    parser.add_argument("--password", default=None, help="New password (prompted if omitted)")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass(f"New password for '{args.username}': ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("ERROR: passwords do not match.", file=sys.stderr)
            sys.exit(1)

    if len(password) < 8:
        print("ERROR: password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(reset(args.username, password))


if __name__ == "__main__":
    main()
