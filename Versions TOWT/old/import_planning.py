"""
Import planning CSV into the database.
Usage: python import_planning.py planning_import.csv
"""
import asyncio
import csv
import sys
from datetime import datetime

from sqlalchemy import select, text
from app.database import engine, async_session, Base
from app.models.vessel import Vessel
from app.models.port import Port
from app.models.leg import Leg


# ── Port coordinates lookup ──────────────────────────────────
PORT_COORDS = {
    "FRFEC": ("Fecamp", 49.7578, 0.3650, "FR"),
    "FRLEH": ("Le Havre", 49.4944, 0.1079, "FR"),
    "FRCOC": ("Concarneau", 47.8706, -3.9214, "FR"),
    "USNYC": ("New York", 40.6892, -74.0445, "US"),
    "BRSSO": ("Sao Sebastiao", -23.8159, -45.4097, "BR"),
    "COSMR": ("Santa Marta", 11.2472, -74.1992, "CO"),
    "COSTM": ("Santa Marta", 11.2472, -74.1992, "CO"),
    "HNPCR": ("Puerto Cortes", 15.8383, -87.9550, "HN"),
    "CAQUE": ("Quebec", 46.8139, -71.2080, "CA"),
    "CAMAT": ("Matane", 48.8500, -67.5333, "CA"),
    "GPPTP": ("Pointe-a-Pitre", 16.2411, -61.5310, "GP"),
    "GTSTC": ("Puerto Santo Tomas de Castilla", 15.7000, -88.6167, "GT"),
    "PTOPO": ("Porto", 41.1496, -8.6109, "PT"),
    "CUHAV": ("La Habana", 23.1136, -82.3666, "CU"),
    "VNSGN": ("Ho Chi Minh City", 10.7626, 106.7533, "VN"),
    "VNDAD": ("Da Nang", 16.0678, 108.2208, "VN"),
    "REPDG": ("Port de Pointe des Galets", -20.9342, 55.2917, "RE"),
    "RELPT": ("Le Port", -20.9342, 55.2917, "RE"),
}

# ── Vessel mapping ──────────────────────────────────────────
VESSEL_MAP = {
    "Anemos": 1,
    "Artemis": 2,
    "Atlantis": 3,
    "Atlas": 4,
}


def parse_dt(s: str):
    """Parse DD/MM/YYYY HH:MM or DD/MM/YYYY HH:MM:SS"""
    if not s or not s.strip():
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_float(s: str):
    if not s or not s.strip():
        return None
    try:
        return float(s.strip().replace(",", "."))
    except ValueError:
        return None


def extract_year_from_code(leg_code: str) -> int:
    """Extract year from the last digit of the leg code."""
    last_char = leg_code.rstrip("-ABCDEFGHIJKLMNOPQRSTUVWXYZ")[-1:]
    if last_char.isdigit():
        d = int(last_char)
        if d == 4:
            return 2024
        elif d == 5:
            return 2025
        elif d == 6:
            return 2026
    return 2026


def extract_sequence_from_code(leg_code: str) -> int:
    """
    Extract sequence from leg_code.
    Format: {vessel_code}{sequence_letter}{dep_country}{arr_country}{year_digit}
    E.g., 1AFRUS6 -> A -> 1, 1BUSCO6 -> B -> 2
    """
    # Remove vessel code prefix (1 digit)
    rest = leg_code[1:]
    if not rest:
        return 1
    letter = rest[0].upper()
    if letter.isalpha():
        return ord(letter) - ord('A') + 1
    return 1


async def main(csv_path: str):
    print(f"Reading {csv_path}...")

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            rows.append(row)

    print(f"Found {len(rows)} legs to import.")

    async with async_session() as db:
        # ── 1. Ensure vessels exist ──────────────────────
        print("\n── Vessels ──")
        for name, code in VESSEL_MAP.items():
            result = await db.execute(select(Vessel).where(Vessel.code == code))
            vessel = result.scalar_one_or_none()
            if not vessel:
                vessel = Vessel(code=code, name=name, default_speed=8.0, default_elongation=1.25)
                db.add(vessel)
                print(f"  Created vessel: {name} (code={code})")
            else:
                print(f"  Exists: {vessel.name} (code={vessel.code})")
        await db.flush()

        # Build vessel_id lookup
        result = await db.execute(select(Vessel))
        vessels = {v.code: v.id for v in result.scalars().all()}

        # ── 2. Ensure ports exist with coordinates ───────
        print("\n── Ports ──")
        needed_locodes = set()
        for row in rows:
            needed_locodes.add(row["Départ LOCODE"].strip())
            needed_locodes.add(row["Arrivée LOCODE"].strip())

        for locode in sorted(needed_locodes):
            result = await db.execute(select(Port).where(Port.locode == locode))
            port = result.scalar_one_or_none()
            if port:
                # Update coordinates if missing
                if not port.latitude and locode in PORT_COORDS:
                    _, lat, lon, _ = PORT_COORDS[locode]
                    port.latitude = lat
                    port.longitude = lon
                    print(f"  Updated coords: {locode} ({port.name})")
                else:
                    print(f"  Exists: {locode} ({port.name})")
            else:
                if locode in PORT_COORDS:
                    name, lat, lon, cc = PORT_COORDS[locode]
                else:
                    # Derive from CSV
                    name = locode
                    lat, lon = None, None
                    cc = locode[:2]
                    for row in rows:
                        if row["Départ LOCODE"].strip() == locode:
                            name = row["Port Départ"].strip()
                            cc = locode[:2]
                            break
                        elif row["Arrivée LOCODE"].strip() == locode:
                            name = row["Port Arrivée"].strip()
                            cc = locode[:2]
                            break

                port = Port(
                    locode=locode,
                    name=name,
                    latitude=lat,
                    longitude=lon,
                    country_code=cc,
                )
                db.add(port)
                print(f"  Created port: {locode} ({name}){' [NO COORDS]' if not lat else ''}")
        await db.flush()

        # ── 3. Import legs ───────────────────────────────
        print("\n── Legs ──")
        created = 0
        updated = 0
        skipped = 0

        for row in rows:
            leg_code = row["Leg Code"].strip()
            vessel_name = row["Navire"].strip()
            dep_locode = row["Départ LOCODE"].strip()
            arr_locode = row["Arrivée LOCODE"].strip()
            etd = parse_dt(row.get("ETD", ""))
            eta = parse_dt(row.get("ETA", ""))
            atd = parse_dt(row.get("ATD (réel)", ""))
            ata = parse_dt(row.get("ATA (réel)", ""))
            distance_ortho = parse_float(row.get("Distance Ortho (NM)", ""))
            distance_reel = parse_float(row.get("Distance Réelle (NM)", ""))
            speed = parse_float(row.get("Vitesse (nds)", ""))
            duration = parse_float(row.get("Durée Est. (h)", ""))
            status = row.get("Statut", "planned").strip()

            vessel_code = VESSEL_MAP.get(vessel_name)
            if not vessel_code or vessel_code not in vessels:
                print(f"  SKIP {leg_code}: unknown vessel {vessel_name}")
                skipped += 1
                continue

            vessel_id = vessels[vessel_code]
            year = extract_year_from_code(leg_code)
            sequence = extract_sequence_from_code(leg_code)

            # Compute elongation from distances if available
            elongation = None
            if distance_ortho and distance_reel and distance_ortho > 0:
                elongation = round(distance_reel / distance_ortho, 2)

            # Check existing
            result = await db.execute(select(Leg).where(Leg.leg_code == leg_code))
            leg = result.scalar_one_or_none()

            if leg:
                # Update existing leg
                leg.vessel_id = vessel_id
                leg.year = year
                leg.sequence = sequence
                leg.departure_port_locode = dep_locode
                leg.arrival_port_locode = arr_locode
                leg.etd = etd
                leg.eta = eta
                leg.atd = atd
                leg.ata = ata
                leg.distance_nm = distance_ortho
                leg.computed_distance = distance_reel
                leg.speed_knots = speed
                leg.estimated_duration_hours = duration
                leg.elongation_coeff = elongation or leg.elongation_coeff
                leg.status = status
                updated += 1
                print(f"  Updated: {leg_code}")
            else:
                leg = Leg(
                    leg_code=leg_code,
                    vessel_id=vessel_id,
                    year=year,
                    sequence=sequence,
                    departure_port_locode=dep_locode,
                    arrival_port_locode=arr_locode,
                    etd=etd,
                    eta=eta,
                    atd=atd,
                    ata=ata,
                    distance_nm=distance_ortho,
                    computed_distance=distance_reel,
                    speed_knots=speed,
                    elongation_coeff=elongation or 1.25,
                    estimated_duration_hours=duration,
                    status=status,
                )
                db.add(leg)
                created += 1
                print(f"  Created: {leg_code}")

        await db.flush()
        await db.commit()

        print(f"\n── Done ──")
        print(f"  Created: {created}")
        print(f"  Updated: {updated}")
        print(f"  Skipped: {skipped}")
        print(f"  Total:   {len(rows)}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "planning_import.csv"
    asyncio.run(main(path))
