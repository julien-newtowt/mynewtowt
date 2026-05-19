#!/usr/bin/env python3
"""RGPD: Purge portal access logs older than 12 months."""
import asyncio
from app.database import engine
from sqlalchemy import text


async def purge():
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM portal_access_logs WHERE accessed_at < NOW() - INTERVAL '12 months'")
        )
        print(f"Purge OK: {result.rowcount} entrées supprimées")


if __name__ == "__main__":
    asyncio.run(purge())
