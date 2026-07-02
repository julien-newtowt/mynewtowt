"""Bulk-load ports into the directory.

Usage :
  python -m scripts.load_ports                                   # embedded + datagouv FR
  python -m scripts.load_ports --with-unlocode                   # + UN/LOCODE mondial
  python -m scripts.load_ports --datagouv-slug seaports-locations-data
                                                                 # par slug
  python -m scripts.load_ports --from-file /tmp/seaports.csv     # depuis un fichier local
  python -m scripts.load_ports --skip-datagouv --skip-embedded   # ne charge que --from-file

Sources :
- **Embedded catalogue** (`scripts/data/world_ports.py`) — ~250 ports
  commerciaux majeurs mondiaux maintenus à la main (default ON).
- **data.gouv.fr** — par défaut le dataset Ports de France
  (ressource ac2c8109-...). Avec ``--datagouv-slug``, le script
  appelle l'API ``/api/1/datasets/{slug}/`` pour récupérer la première
  ressource CSV. Avec ``--datagouv-url``, on passe directement
  l'URL de la ressource.
- **UN/LOCODE community mirror** — ~110 000 entrées (--with-unlocode).
- **--from-file** — lit n'importe quel CSV local (utile quand l'host
  d'exécution n'a pas d'accès réseau vers data.gouv.fr).

Idempotent : upsert sur le locode, ne remplace jamais une entrée
manuelle par une entrée automatique.
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
from scripts.data.world_ports import as_port_rows as embedded_world_ports

DATAGOUV_DEFAULT_URL = "https://www.data.gouv.fr/fr/datasets/r/ac2c8109-8db3-40ff-af88-9e68ddafe66d"
UNLOCODE_DEFAULT_URL = (
    "https://raw.githubusercontent.com/datasets/un-locode/master/data/code-list.csv"
)

logger = logging.getLogger("load_ports")


async def _download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "mynewtowt-ports-loader/1.0"})
            r.raise_for_status()
            return r.content
    except httpx.HTTPError as e:
        logger.warning("Download failed for %s: %s", url, e)
        return None


async def _resolve_datagouv_slug(slug: str) -> str | None:
    """Résout un slug data.gouv.fr → URL de la 1re ressource CSV.

    Appelle ``https://www.data.gouv.fr/api/1/datasets/{slug}/`` puis pioche
    la première ressource dont le format CSV ou xlsx est disponible.
    """
    api = f"https://www.data.gouv.fr/api/1/datasets/{slug}/"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(api, headers={"User-Agent": "mynewtowt-ports-loader/1.0"})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        logger.warning("data.gouv slug resolution failed for %r: %s", slug, e)
        return None
    for res in data.get("resources", []):
        fmt = (res.get("format") or "").lower()
        url = res.get("url") or ""
        if fmt in ("csv", "xlsx") and url:
            logger.info(
                "Resolved slug %r → %s (%s, %s)",
                slug,
                res.get("title") or res.get("id"),
                fmt,
                url,
            )
            return url
    logger.warning("No CSV/XLSX resource found in dataset %r", slug)
    return None


def _parse_unlocode_coords(packed: str) -> tuple[float, float] | None:
    """Parse a UN/LOCODE coordinates string like '4015N 12453W' → (40.25, -124.883).

    Format : DDMM[N|S] DDDMM[E|W]   (degré + minute, sans décimale ni espace
    intra-coord). Retourne (lat, lon) en décimal, ou None si parsing échoue.
    """
    if not packed:
        return None
    packed = packed.strip()
    parts = packed.split()
    if len(parts) != 2:
        return None
    lat_s, lon_s = parts
    try:
        # Latitude DDMM[N|S]
        lat_hemi = lat_s[-1]
        if lat_hemi not in ("N", "S"):
            return None
        lat_dd = int(lat_s[:-5]) if len(lat_s) >= 6 else int(lat_s[:-3])
        lat_mm = int(lat_s[-3:-1])
        lat = lat_dd + lat_mm / 60.0
        if lat_hemi == "S":
            lat = -lat

        # Longitude DDDMM[E|W]
        lon_hemi = lon_s[-1]
        if lon_hemi not in ("E", "W"):
            return None
        lon_dd = int(lon_s[:-3])
        lon_mm = int(lon_s[-3:-1])
        lon = lon_dd + lon_mm / 60.0
        if lon_hemi == "W":
            lon = -lon
        return (round(lat, 4), round(lon, 4))
    except (ValueError, IndexError):
        return None


def parse_unlocode_csv(content: bytes) -> list[PortRow]:
    """Parse the UN/LOCODE github mirror CSV (with packed coordinates).

    Columns expected (datasets/un-locode/code-list.csv) :
        Change, Country, Location, Name, NameWoDiacritics, Subdivision,
        Status, Function, Date, IATA, Coordinates, Remarks

    Le CSV UN/LOCODE contient régulièrement des **doublons** pour un même
    locode (variantes orthographiques, ex. BEZUN "Zuen (Zuun)" et
    "Zuun (Zuen)"). On garde la **première occurrence** de chaque locode.
    """
    import csv
    import io

    seen: set[str] = set()
    out: list[PortRow] = []
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        country = (row.get("Country") or "").strip().upper()[:2]
        loc = (row.get("Location") or "").strip().upper()[:3]
        if not country or not loc:
            continue
        locode = (country + loc).replace(" ", "")[:5]
        if locode in seen:
            continue
        name = (row.get("Name") or row.get("NameWoDiacritics") or "").strip()
        function = (row.get("Function") or "").strip()
        coords = _parse_unlocode_coords((row.get("Coordinates") or "").strip())
        if not coords or not name:
            continue
        seen.add(locode)
        out.append(
            PortRow(
                locode=locode,
                name=name[:100],
                country=country,
                latitude=coords[0],
                longitude=coords[1],
                source="unlocode",
                function_code=function or "1-------",
                subdivision=(row.get("Subdivision") or "").strip()[:8] or None,
            )
        )
    return out


async def load(
    *,
    skip_embedded: bool,
    skip_datagouv: bool,
    with_unlocode: bool,
    datagouv_url: str,
    datagouv_slug: str | None,
    datagouv_country_filter: str | None,
    unlocode_url: str,
    from_file: str | None,
) -> None:
    async with SessionLocal() as db:
        # ─── Catalogue embarqué ──────────────────────────────────────
        if not skip_embedded:
            rows = embedded_world_ports()
            ins, upd = await upsert_ports(db, rows)
            await db.commit()
            logger.info(
                "Embedded world catalogue : %d entries — %d inserted, %d updated",
                len(rows),
                ins,
                upd,
            )

        # ─── data.gouv (URL fixe OU slug auto-résolu) ─────────────────
        if not skip_datagouv:
            url = datagouv_url
            if datagouv_slug:
                resolved = await _resolve_datagouv_slug(datagouv_slug)
                if resolved:
                    url = resolved
                else:
                    logger.warning(
                        "Slug %r non résolu — fallback URL %s",
                        datagouv_slug,
                        url,
                    )
            logger.info("Fetching data.gouv from %s", url)
            payload = await _download(url)
            if payload:
                rows = parse_csv(payload, source=f"datagouv:{datagouv_slug or 'default'}")
                if datagouv_country_filter:
                    rows = [r for r in rows if r.country.upper() == datagouv_country_filter.upper()]
                ins, upd = await upsert_ports(db, rows)
                await db.commit()
                logger.info(
                    "data.gouv : %d parsed, %d inserted, %d updated",
                    len(rows),
                    ins,
                    upd,
                )
            else:
                logger.warning("Skipping data.gouv (download failed)")

        # ─── Fichier local (--from-file) ─────────────────────────────
        if from_file:
            from pathlib import Path

            path = Path(from_file)
            if not path.exists():
                logger.error("File not found: %s", path)
            else:
                logger.info("Loading from local file %s", path)
                payload = path.read_bytes()
                rows = parse_csv(payload, source=f"file:{path.name}")
                ins, upd = await upsert_ports(db, rows)
                await db.commit()
                logger.info(
                    "Local file : %d parsed, %d inserted, %d updated",
                    len(rows),
                    ins,
                    upd,
                )

        # ─── UN/LOCODE (option) ──────────────────────────────────────
        if with_unlocode:
            logger.info("Fetching UN/LOCODE from %s", unlocode_url)
            payload = await _download(unlocode_url)
            if payload:
                rows = parse_unlocode_csv(payload)
                rows = _filter_unlocode_seaports(rows)
                # Garde tous les pays — l'embedded couvre l'essentiel
                # mais UN/LOCODE complète avec la long tail.
                ins, upd = await upsert_ports(db, rows)
                await db.commit()
                logger.info("UN/LOCODE : %d inserted, %d updated", ins, upd)
            else:
                logger.warning("Skipping UN/LOCODE (download failed)")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Load ports into the directory")
    parser.add_argument(
        "--skip-embedded", action="store_true", help="Ne charge pas le catalogue embarqué"
    )
    parser.add_argument(
        "--skip-datagouv", action="store_true", help="Ne charge pas les ports data.gouv.fr"
    )
    parser.add_argument(
        "--with-unlocode",
        action="store_true",
        help="Charge en plus le mirror UN/LOCODE github (long tail mondiale)",
    )
    parser.add_argument(
        "--datagouv-url",
        default=DATAGOUV_DEFAULT_URL,
        help="URL directe d'une ressource data.gouv (CSV)",
    )
    parser.add_argument(
        "--datagouv-slug",
        default=None,
        help="Slug data.gouv.fr (ex. seaports-locations-data) — "
        "résolu automatiquement vers la 1re ressource CSV",
    )
    parser.add_argument(
        "--datagouv-country",
        default=None,
        help="Filtre les rows data.gouv par code pays ISO-2 "
        "(ex. FR). Par défaut: pas de filtre.",
    )
    parser.add_argument("--unlocode-url", default=UNLOCODE_DEFAULT_URL)
    parser.add_argument(
        "--from-file", default=None, help="Charge un CSV local (utile sans accès réseau)"
    )
    args = parser.parse_args()

    asyncio.run(
        load(
            skip_embedded=args.skip_embedded,
            skip_datagouv=args.skip_datagouv,
            with_unlocode=args.with_unlocode,
            datagouv_url=args.datagouv_url,
            datagouv_slug=args.datagouv_slug,
            datagouv_country_filter=args.datagouv_country,
            unlocode_url=args.unlocode_url,
            from_file=args.from_file,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
