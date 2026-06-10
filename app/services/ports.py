"""Port directory service.

- Bulk upsert helper (idempotent on locode).
- Haversine distance for nearby queries (no PostGIS dependency).
- CSV parser tolerant to common column names from data.gouv.fr and
  UN/LOCODE distributions.

Why haversine in Python instead of PostGIS? V3 keeps Postgres lean
(no extension required); for our scale (~15k rows) the SQL prefilter
on a lat/lon bounding box + Python distance refinement is plenty fast.
"""
from __future__ import annotations

import csv
import io
import logging
import math
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.port import Port

logger = logging.getLogger(__name__)

EARTH_RADIUS_NM = 3440.065  # nautical miles
EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class PortRow:
    """Lean DTO used by the loader and the nearby API."""

    locode: str
    name: str
    country: str
    latitude: float
    longitude: float
    source: str = "manual"
    function_code: str | None = None
    subdivision: str | None = None
    timezone: str | None = None


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_nm(lat1, lon1, lat2, lon2) * 1.852


# ---------------------------------------------------------------------------
# Nearby queries
# ---------------------------------------------------------------------------


async def nearby_ports(
    db: AsyncSession,
    *,
    lat: float,
    lon: float,
    radius_km: float = 50,
    limit: int = 10,
) -> list[tuple[Port, float]]:
    """Return ports within ``radius_km`` of (lat, lon), sorted ascending."""
    # SQL pre-filter via bounding box (~ very approximate at this scale).
    # 1° lat ≈ 111 km. 1° lon ≈ 111 * cos(lat). We over-estimate by
    # using 111 in both directions — refined in Python.
    deg = max(radius_km / 90.0, 0.5)
    stmt = (
        select(Port)
        .where(Port.latitude.is_not(None))
        .where(Port.longitude.is_not(None))
        .where(Port.latitude.between(lat - deg, lat + deg))
        .where(Port.longitude.between(lon - deg, lon + deg))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    enriched = [
        (p, haversine_km(lat, lon, p.latitude, p.longitude))
        for p in rows
        if p.latitude is not None and p.longitude is not None
    ]
    enriched = [(p, d) for p, d in enriched if d <= radius_km]
    enriched.sort(key=lambda x: x[1])
    return enriched[:limit]


async def closest_port(
    db: AsyncSession, *, lat: float, lon: float, max_km: float = 100
) -> tuple[Port, float] | None:
    results = await nearby_ports(db, lat=lat, lon=lon, radius_km=max_km, limit=1)
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Bulk upsert (idempotent on locode)
# ---------------------------------------------------------------------------


async def upsert_ports(db: AsyncSession, rows: Iterable[PortRow]) -> tuple[int, int]:
    """Insert new ports, update existing ones (matched on locode).

    Robuste aux doublons en batch : on déduplique par locode (premier
    gagnant), et on flushe par paquets de 500 pour matérialiser les
    INSERT avant de retomber sur un éventuel locode déjà présent dans la
    session (cas du UN/LOCODE CSV qui contient des variantes orthographiques
    sur le même locode, ex. BEZUN "Zuen (Zuun)" / "Zuun (Zuen)").

    Returns (inserted_count, updated_count).
    """
    inserted = 0
    updated = 0
    seen_in_batch: set[str] = set()
    BATCH = 500
    pending = 0

    for row in rows:
        if not row.locode or not row.country or row.latitude is None or row.longitude is None:
            continue
        if row.locode in seen_in_batch:
            continue
        seen_in_batch.add(row.locode)

        existing = (
            await db.execute(select(Port).where(Port.locode == row.locode))
        ).scalar_one_or_none()
        if existing is None:
            db.add(Port(
                locode=row.locode,
                name=row.name,
                country=row.country,
                latitude=row.latitude,
                longitude=row.longitude,
                source=row.source,
                function_code=row.function_code,
                subdivision=row.subdivision,
                timezone=row.timezone,
            ))
            inserted += 1
            pending += 1
        else:
            # Don't overwrite manual entries with automatic data unless
            # explicitly re-imported by the same source.
            if existing.source == "manual" and row.source != "manual":
                continue
            existing.name = row.name
            existing.country = row.country
            existing.latitude = row.latitude
            existing.longitude = row.longitude
            existing.source = row.source
            if row.function_code:
                existing.function_code = row.function_code
            if row.subdivision:
                existing.subdivision = row.subdivision
            if row.timezone:
                existing.timezone = row.timezone
            updated += 1
            pending += 1

        if pending >= BATCH:
            await db.flush()
            pending = 0

    if pending:
        await db.flush()
    return inserted, updated


# ---------------------------------------------------------------------------
# CSV parsers — tolerant to common column names
# ---------------------------------------------------------------------------


_LOCODE_COLS = ("locode", "un_locode", "unlocode", "code", "code_locode")
_NAME_COLS = ("name", "nom", "port_name", "ville", "nom_port", "libelle")
_COUNTRY_COLS = ("country", "pays", "country_code", "code_pays", "iso2")
_LAT_COLS = ("latitude", "lat", "y")
_LON_COLS = ("longitude", "lon", "lng", "long", "x")
_FUNC_COLS = ("function", "function_code", "fonction")
_SUBDIV_COLS = ("subdivision", "subdiv", "region_code", "region")
_TZ_COLS = ("timezone", "tz", "fuseau")


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for key in row:
        if key is None:
            # csv.DictReader yields None keys for rows with more fields
            # than headers — ignore them.
            continue
        if key.lower().strip() in candidates:
            v = (row[key] or "").strip()
            if v:
                return v
    return None


def _maybe_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v.replace(",", "."))
    except ValueError:
        return None


def parse_csv(content: bytes | str, *, source: str = "csv") -> list[PortRow]:
    """Parse a CSV blob into PortRow list. Skips invalid lines silently."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    reader = csv.DictReader(io.StringIO(text), delimiter=_detect_delimiter(text))
    rows: list[PortRow] = []
    for raw in reader:
        if not raw:
            continue
        locode = _pick(raw, _LOCODE_COLS)
        name = _pick(raw, _NAME_COLS)
        country = _pick(raw, _COUNTRY_COLS)
        lat = _maybe_float(_pick(raw, _LAT_COLS))
        lon = _maybe_float(_pick(raw, _LON_COLS))
        if not (locode and name and country and lat is not None and lon is not None):
            continue
        rows.append(PortRow(
            locode=locode.replace(" ", "").upper()[:5],
            name=name[:100],
            country=country.upper()[:2],
            latitude=lat,
            longitude=lon,
            source=source,
            function_code=_pick(raw, _FUNC_COLS),
            subdivision=_pick(raw, _SUBDIV_COLS),
            timezone=_pick(raw, _TZ_COLS),
        ))
    return rows


def _detect_delimiter(text: str) -> str:
    head = text[:2048]
    counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get)


def _filter_unlocode_seaports(rows: list[PortRow]) -> list[PortRow]:
    """UN/LOCODE rows where the Function code position 0 is "1" (sea port).

    Function format example: "1-3----" (7 chars). Position meanings:
    1=sea port, 2=rail, 3=road, 4=airport, 5=postal, 6=multimodal,
    7=fixed transport, B=border crossing.
    """
    out: list[PortRow] = []
    for r in rows:
        if not r.function_code or len(r.function_code) < 1:
            continue
        if r.function_code[0] == "1":
            out.append(r)
    return out
