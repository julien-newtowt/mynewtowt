"""
Import cargo tonnage per leg from CSV into leg_kpis table.

Usage (inside Docker):
    docker exec towt-app-v2 python3 /app/scripts/import_tonnage.py

Or locally:
    python3 scripts/import_tonnage.py
"""
import asyncio
import csv
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.database import engine, async_session
from app.models.leg import Leg
from app.models.kpi import LegKPI


CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "import_tonnage.csv")


async def main():
    async with async_session() as db:
        # Read CSV
        with open(CSV_PATH, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            rows = list(reader)

        print(f"Read {len(rows)} rows from CSV")

        # Load all legs indexed by leg_code
        result = await db.execute(select(Leg))
        legs_by_code = {leg.leg_code: leg for leg in result.scalars().all()}

        # Load existing KPIs indexed by leg_id
        result = await db.execute(select(LegKPI))
        kpis_by_leg = {kpi.leg_id: kpi for kpi in result.scalars().all()}

        updated = 0
        created = 0
        not_found = []

        for row in rows:
            code = row["leg_code"].strip()
            tons = float(row["cargo_tons"].strip() or 0)

            leg = legs_by_code.get(code)
            if not leg:
                not_found.append(code)
                continue

            kpi = kpis_by_leg.get(leg.id)
            if kpi:
                kpi.cargo_tons = tons
                updated += 1
            else:
                kpi = LegKPI(leg_id=leg.id, cargo_tons=tons)
                db.add(kpi)
                created += 1

        await db.commit()

        print(f"Updated: {updated}")
        print(f"Created: {created}")
        if not_found:
            print(f"Leg codes not found ({len(not_found)}): {', '.join(not_found)}")
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
