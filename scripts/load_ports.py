"""Bulk-load ports into the directory.

Usage:
  python -m scripts.load_ports               # all sources, gracefully skip on error
  python -m scripts.load_ports --skip-datagouv
  python -m scripts.load_ports --skip-unlocode
  python -m scripts.load_ports --datagouv-url <override>

Sources:
- data.gouv.fr — French ports dataset
  Dataset 6900a8a0460b3d95b01ff77d, resource ac2c8109-8db3-40ff-af88-9e68ddafe66d
  Default URL: https://www.data.gouv.fr/fr/datasets/r/ac2c8109-8db3-40ff-af88-9e68ddafe66d
- UN/LOCODE — World ports dataset (community mirror)
  Default URL: https://raw.githubusercontent.com/datasets/un-locode/master/data/code-list.csv
  (≈ 110 000 entries; we keep only rows with a Function code containing
  "1" — sea-port — and valid lat/lon.)

This script is *idempotent*: it upserts on locode, never overwrites
manual entries with automatic data.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from app.database import SessionLocal
from app.services.ports import (
    PortRow,
    _filter_unlocode_seaports,
    parse_csv,
    upsert_ports,
)

DATAGOUV_DEFAULT_URL = (
    "https://www.data.gouv.fr/fr/datasets/r/ac2c8109-8db3-40ff-af88-9e68ddafe66d"
)
UNLOCODE_DEFAULT_URL = (
    "https://raw.githubusercontent.com/datasets/un-locode/master/data/code-list.csv"
)

logger = logging.getLogger("load_ports")


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except httpx.HTTPError as e:
        logger.warning("Download failed for %s: %s", url, e)
        return None


async def load(*, skip_datagouv: bool, skip_unlocode: bool,
               datagouv_url: str, unlocode_url: str) -> None:
    async with SessionLocal() as db:
        # ─── data.gouv FR ─────────────────────────────────────────────
        if not skip_datagouv:
            logger.info("Fetching data.gouv FR ports from %s", datagouv_url)
            payload = await _download(datagouv_url)
            if payload:
                rows = parse_csv(payload, source="datagouv")
                rows = [r for r in rows if r.country == "FR"]  # safety net
                ins, upd = await upsert_ports(db, rows)
                await db.commit()
                logger.info("data.gouv FR: %d inserted, %d updated", ins, upd)
            else:
                logger.warning("Skipping data.gouv (download failed)")

        # ─── UN/LOCODE (world) ────────────────────────────────────────
        if not skip_unlocode:
            logger.info("Fetching UN/LOCODE from %s", unlocode_url)
            payload = await _download(unlocode_url)
            if payload:
                rows = parse_csv(payload, source="unlocode")
                rows = _filter_unlocode_seaports(rows)
                # Don't overwrite French ports already loaded from data.gouv
                rows = [r for r in rows if r.country != "FR"]
                ins, upd = await upsert_ports(db, rows)
                await db.commit()
                logger.info("UN/LOCODE: %d inserted, %d updated", ins, upd)
            else:
                logger.warning("Skipping UN/LOCODE (download failed)")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Load ports into the directory")
    parser.add_argument("--skip-datagouv", action="store_true")
    parser.add_argument("--skip-unlocode", action="store_true")
    parser.add_argument("--datagouv-url", default=DATAGOUV_DEFAULT_URL)
    parser.add_argument("--unlocode-url", default=UNLOCODE_DEFAULT_URL)
    args = parser.parse_args()

    asyncio.run(load(
        skip_datagouv=args.skip_datagouv,
        skip_unlocode=args.skip_unlocode,
        datagouv_url=args.datagouv_url,
        unlocode_url=args.unlocode_url,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
