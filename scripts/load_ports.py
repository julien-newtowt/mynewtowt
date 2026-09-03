"""Bulk-load ports into the directory.

Usage :
  python -m scripts.load_ports                                   # embedded + datagouv FR
  python -m scripts.load_ports --with-unlocode                   # + UN/LOCODE mondial géolocalisé
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
- **UN/LOCODE géolocalisé** — ~116 000 lieux, dont **16 669 ports
  maritimes** exploitables (--with-unlocode). Le miroir brut UNECE n'en donne
  que 11 763 : UNECE laisse 20 % des lieux sans coordonnées, dont de vrais
  ports (Manille en était absente). ⚠ La part de coordonnées corrigées est
  dérivée d'OpenStreetMap (**ODbL**) : attribution obligatoire en cas de
  republication (cf. docs/integrations/unlocode-ports.md).
- **--from-file** — lit n'importe quel CSV local (utile quand l'host
  d'exécution n'a pas d'accès réseau vers data.gouv.fr).

Idempotent : upsert sur le locode, avec une **hiérarchie de sources**
(`services.ports.may_overwrite`) — une correction humaine (`manual`) et le
catalogue embarqué (`world_ports`) ne sont jamais dégradés par un import
automatique ; les coordonnées géolocalisées (`unlocode-improved`) ne sont pas
écrasées par le miroir brut. Détail et attribution ODbL :
`docs/integrations/unlocode-ports.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from app.database import SessionLocal
from app.services.ports import (
    UnlocodeReport,
    _filter_unlocode_seaports,
    parse_csv,
    parse_unlocode_csv,
    upsert_ports,
)
from scripts.data.world_ports import as_port_rows as embedded_world_ports

DATAGOUV_DEFAULT_URL = "https://www.data.gouv.fr/fr/datasets/r/ac2c8109-8db3-40ff-af88-9e68ddafe66d"
# Miroir UN/LOCODE **géolocalisé** : même liste que le miroir officiel
# (datasets/un-locode) plus une colonne ``CoordinatesDecimal`` qui couvre les
# lieux laissés sans coordonnées par UNECE (20 % du fichier, dont de vrais
# ports : Manille en était absente) et corrige les positions fausses.
# Mesuré le 2026-09-02 : 16 676 ports maritimes exploitables contre 11 763
# avec le miroir brut. Repli disponible via ``--unlocode-url``.
UNLOCODE_DEFAULT_URL = (
    "https://raw.githubusercontent.com/cristan/improved-un-locodes/master/data/"
    "code-list-improved.csv"
)
UNLOCODE_PLAIN_URL = (
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
                report = UnlocodeReport()
                rows = parse_unlocode_csv(payload, report=report)
                logger.info("UN/LOCODE : %s", report.summary())
                # Filtre maritime : position 1 de la fonction = port. On garde
                # tous les pays — l'embedded couvre l'essentiel, UN/LOCODE
                # complète la long tail mondiale.
                seaports = _filter_unlocode_seaports(rows)
                logger.info(
                    "UN/LOCODE : %d ports maritimes retenus sur %d lieux " "(fonction 1 = port)",
                    len(seaports),
                    len(rows),
                )
                ins, upd = await upsert_ports(db, seaports)
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
        help="Charge en plus UN/LOCODE géolocalisé (long tail mondiale, 16 669 ports)",
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
    parser.add_argument(
        "--unlocode-url",
        default=UNLOCODE_DEFAULT_URL,
        help="URL du CSV UN/LOCODE (défaut : miroir géolocalisé ; miroir brut "
        f"UNECE : {UNLOCODE_PLAIN_URL})",
    )
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
