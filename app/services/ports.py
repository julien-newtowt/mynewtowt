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
from collections.abc import Iterable
from dataclasses import dataclass

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


# Priorité des sources — qui peut écraser quoi lors d'un upsert.
#
# Sans cette hiérarchie, un rafraîchissement UN/LOCODE **dégradait** les
# données curées : le catalogue embarqué donne Fécamp à 49,7594 / 0,3742, là où
# UN/LOCODE l'arrondit à la minute (49,75 / 0,38333, soit ~1 km d'écart). Le
# docstring du chargeur promettait « ne remplace jamais une entrée manuelle »,
# promesse creuse en pratique : aucune source n'écrivait ``manual``.
_SOURCE_PRECEDENCE: dict[str, int] = {
    "manual": 30,  # correction humaine (Admin → Ports) — jamais écrasée
    "world_ports": 20,  # catalogue embarqué, maintenu à la main
    "unlocode-improved": 15,  # UN/LOCODE + coordonnées corrigées (OSM/Wikidata)
}
_DEFAULT_SOURCE_PRECEDENCE = 10
"""Sources automatiques indifférenciées (unlocode brut, data.gouv, CSV, fichier)."""


def source_precedence(source: str | None) -> int:
    return _SOURCE_PRECEDENCE.get(source or "", _DEFAULT_SOURCE_PRECEDENCE)


def may_overwrite(existing_source: str | None, new_source: str | None) -> bool:
    """Une entrée ``existing_source`` peut-elle être réécrite par ``new_source`` ?

    Un ré-import de la **même** source met toujours à jour (c'est le principe
    d'un rafraîchissement). Sinon, seule une source de priorité supérieure ou
    égale écrase. Les sources automatiques sont à égalité : la dernière
    importée gagne, comportement historique conservé.
    """
    if existing_source == new_source:
        return True
    return source_precedence(new_source) >= source_precedence(existing_source)


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
            db.add(
                Port(
                    locode=row.locode,
                    name=row.name,
                    country=row.country,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    source=row.source,
                    function_code=row.function_code,
                    subdivision=row.subdivision,
                    timezone=row.timezone,
                )
            )
            inserted += 1
            pending += 1
        else:
            # Hiérarchie de sources : une donnée curée (correction humaine,
            # catalogue embarqué) n'est jamais dégradée par un import
            # automatique. Cf. ``may_overwrite``.
            if not may_overwrite(existing.source, row.source):
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
        rows.append(
            PortRow(
                locode=locode.replace(" ", "").upper()[:5],
                name=name[:100],
                country=country.upper()[:2],
                latitude=lat,
                longitude=lon,
                source=source,
                function_code=_pick(raw, _FUNC_COLS),
                subdivision=_pick(raw, _SUBDIV_COLS),
                timezone=_pick(raw, _TZ_COLS),
            )
        )
    return rows


def _detect_delimiter(text: str) -> str:
    head = text[:2048]
    counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get)


# ---------------------------------------------------------------------------
# UN/LOCODE — référentiel officiel des codes de lieux (UNECE)
# ---------------------------------------------------------------------------

UNLOCODE_RETIRED_STATUS = "XX"
"""Statut UN/LOCODE « entrée qui sera retirée de la prochaine édition ».

On n'ajoute pas ces codes au référentiel : ils sont en cours de retrait chez
UNECE. Exemple relevé le 2026-09-02 : ``REPDG`` (Pointe des Galets) est en
``XX`` **et** sans fonction port, alors que le port réel de La Réunion est
``RELPT`` (« Le Port », fonction ``1-3-5---``, statut ``AF``). Les entrées
déjà présentes en base ne sont jamais supprimées pour autant — ``upsert_ports``
n'efface rien, et un code retiré peut rester porté par un booking passé.
"""


@dataclass
class UnlocodeReport:
    """Compte-rendu de parsing — un import muet ne se contrôle pas.

    ``skipped_no_coordinates`` est le compteur qui compte : c'est lui qui
    explique un référentiel incomplet (et, en cascade, des legs sans distance
    théorique).
    """

    total_rows: int = 0
    kept: int = 0
    duplicates: int = 0
    skipped_no_coordinates: int = 0
    skipped_no_name: int = 0
    skipped_retired: int = 0
    from_decimal: int = 0
    from_packed: int = 0

    def summary(self) -> str:
        return (
            f"{self.total_rows} lignes lues → {self.kept} retenues "
            f"({self.from_decimal} coord. décimales, {self.from_packed} coord. DDMM) ; "
            f"écartées : {self.skipped_no_coordinates} sans coordonnées, "
            f"{self.skipped_no_name} sans nom, {self.skipped_retired} retirées (statut "
            f"{UNLOCODE_RETIRED_STATUS}), {self.duplicates} doublons de locode"
        )


def parse_unlocode_packed_coordinates(packed: str | None) -> tuple[float, float] | None:
    """``'4015N 12453W'`` → ``(40.25, -124.8833)``.

    Format UN/LOCODE historique : ``DDMM[N|S] DDDMM[E|W]`` — degré + minute,
    sans décimale. La précision plafonne donc à la minute d'arc (~1,8 km) :
    suffisant pour une orthodromie transatlantique, grossier pour poser un
    marqueur de port sur une carte. ``None`` si le parsing échoue.
    """
    if not packed:
        return None
    parts = packed.strip().split()
    if len(parts) != 2:
        return None
    lat_s, lon_s = parts
    try:
        lat_hemi = lat_s[-1]
        if lat_hemi not in ("N", "S"):
            return None
        lat_dd = int(lat_s[:-5]) if len(lat_s) >= 6 else int(lat_s[:-3])
        lat_mm = int(lat_s[-3:-1])
        lat = lat_dd + lat_mm / 60.0
        if lat_hemi == "S":
            lat = -lat

        lon_hemi = lon_s[-1]
        if lon_hemi not in ("E", "W"):
            return None
        lon_dd = int(lon_s[:-3])
        lon_mm = int(lon_s[-3:-1])
        lon = lon_dd + lon_mm / 60.0
        if lon_hemi == "W":
            lon = -lon
    except (ValueError, IndexError):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    return (round(lat, 4), round(lon, 4))


def parse_unlocode_decimal_coordinates(value: str | None) -> tuple[float, float] | None:
    """``'42.50000,1.51667'`` → ``(42.5, 1.51667)``.

    Colonne ``CoordinatesDecimal`` du jeu **improved-un-locodes** : elle
    couvre les lieux que UN/LOCODE laisse sans coordonnées (20 % du fichier
    officiel, dont de vrais ports) et corrige les positions fausses à partir
    d'OpenStreetMap / Wikidata.

    ⚠ Cette part dérivée d'OSM est sous **ODbL** : toute republication de ces
    positions (carte publique, export client) doit porter l'attribution
    OpenStreetMap. Cf. ``docs/integrations/unlocode-ports.md``.
    """
    if not value:
        return None
    parts = value.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    return (round(lat, 6), round(lon, 6))


def parse_unlocode_csv(
    content: bytes | str, *, report: UnlocodeReport | None = None
) -> list[PortRow]:
    """Parse un CSV UN/LOCODE (miroir officiel ou variante géolocalisée).

    Colonnes lues (``datasets/un-locode`` et ``cristan/improved-un-locodes``) :
    ``Country``, ``Location``, ``Name``, ``NameWoDiacritics``, ``Subdivision``,
    ``Status``, ``Function``, ``Coordinates`` et — quand elle existe —
    ``CoordinatesDecimal``.

    La colonne décimale est **prioritaire** : plus couvrante et plus précise
    que le DDMM. Le locode est reconstitué ``Country + Location`` (le fichier
    ne porte pas de colonne unique).

    Le fichier contient régulièrement **plusieurs lignes pour un même locode**
    (variantes orthographiques, ex. ``BEZUN`` « Zuen (Zuun) » / « Zuun
    (Zuen) ») : la **première occurrence** gagne.
    """
    rep = report if report is not None else UnlocodeReport()
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    seen: set[str] = set()
    out: list[PortRow] = []
    for row in csv.DictReader(io.StringIO(text)):
        if not row:
            continue
        rep.total_rows += 1
        country = (row.get("Country") or "").strip().upper()[:2]
        loc = (row.get("Location") or "").strip().upper()[:3]
        if not country or not loc:
            continue
        locode = (country + loc).replace(" ", "")[:5]
        if locode in seen:
            rep.duplicates += 1
            continue
        if (row.get("Status") or "").strip().upper() == UNLOCODE_RETIRED_STATUS:
            rep.skipped_retired += 1
            continue
        name = (row.get("Name") or row.get("NameWoDiacritics") or "").strip()
        if not name:
            rep.skipped_no_name += 1
            continue
        coords = parse_unlocode_decimal_coordinates(row.get("CoordinatesDecimal"))
        if coords is not None:
            rep.from_decimal += 1
            source = "unlocode-improved"
        else:
            coords = parse_unlocode_packed_coordinates(row.get("Coordinates"))
            if coords is None:
                rep.skipped_no_coordinates += 1
                continue
            rep.from_packed += 1
            source = "unlocode"
        seen.add(locode)
        rep.kept += 1
        out.append(
            PortRow(
                locode=locode,
                name=name[:100],
                country=country,
                latitude=coords[0],
                longitude=coords[1],
                source=source,
                function_code=(row.get("Function") or "").strip() or "1-------",
                subdivision=(row.get("Subdivision") or "").strip()[:8] or None,
            )
        )
    return out


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
