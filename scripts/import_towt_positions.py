"""Reprise d'historique TOWT — import des positions GPS consolidées (ADR-014).

Charge dans ``vessel_positions`` les CSV produits par
``scripts/towt_gps_consolidate.py`` (``towt_gps_<navire>_<année>.csv``) :
- ``source = "towt_archive"`` (protégé de toute purge — ``admin_data.PURGE_PROTECTED_ROWS``),
- ``import_batch`` = nom du fichier consolidé (traçabilité TRK-05),
- idempotent sur la clé naturelle ``(vessel_id, recorded_at)`` : les points déjà
  présents (live ou archive) sont ignorés, jamais réécrits.

Les positions ne portent pas de ``leg_id`` : le rattachement à un voyage est
temporel (``voyage_track.leg_window``), d'où l'ordre : **d'abord les legs**
(``import_towt_legs``), puis les positions. Aucune météo n'est associée à
l'archive (le snapshot Windy ne concerne que le dernier point).

OPÉRATION SENSIBLE — dry-run par défaut (travail complet puis ROLLBACK) ;
``--yes`` pour committer.

Usage :
    python -m scripts.import_towt_positions --dir ./gps_towt              # dry-run
    python -m scripts.import_towt_positions --dir ./gps_towt --yes
    python -m scripts.import_towt_positions --file towt_gps_anemos_2025.csv --yes
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert, select

from app.database import SessionLocal
from app.models.claim import VesselPosition
from app.models.vessel import Vessel
from app.services.admin_data import TOWT_ARCHIVE_SOURCE

CHUNK = 5000
MAX_SOG_KN = 40.0  # au-delà : valeur capteur aberrante → SOG ignorée, point conservé


@dataclass
class FileReport:
    name: str
    read: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    first: str = ""
    last: str = ""
    errors: list[str] = field(default_factory=list)


def _parse_ts(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _float(value: str | None) -> float | None:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_points(path: Path, report: FileReport) -> tuple[str | None, list[dict]]:
    """Lit un CSV consolidé ; renvoie (nom de navire, points valides)."""
    vessel_name: str | None = None
    points: list[dict] = []
    seen: set[datetime] = set()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            report.read += 1
            ts = _parse_ts(row.get("recorded_at_utc", ""))
            lat = _float(row.get("latitude"))
            lon = _float(row.get("longitude"))
            if ts is None or lat is None or lon is None:
                report.skipped_invalid += 1
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                report.skipped_invalid += 1
                continue
            if ts in seen:
                report.skipped_duplicate += 1
                continue
            v = (row.get("vessel") or "").strip().upper()
            if vessel_name is None:
                vessel_name = v
            elif v != vessel_name:
                report.errors.append(f"plusieurs navires dans {path.name} ({vessel_name}, {v})")
                return vessel_name, []
            seen.add(ts)
            sog = _float(row.get("sog_kn"))
            if sog is not None and not (0 <= sog <= MAX_SOG_KN):
                sog = None
            cog = _float(row.get("cog_deg"))
            if cog is not None and not (0 <= cog <= 360):
                cog = None
            points.append(
                {
                    "recorded_at": ts,
                    "latitude": lat,
                    "longitude": lon,
                    "sog_kn": sog,
                    "cog_deg": cog,
                }
            )
    points.sort(key=lambda p: p["recorded_at"])
    if points:
        report.first = points[0]["recorded_at"].isoformat()
        report.last = points[-1]["recorded_at"].isoformat()
    return vessel_name, points


async def import_file(db, path: Path) -> FileReport:
    report = FileReport(name=path.name)
    vessel_name, points = load_points(path, report)
    if report.errors:
        return report
    if not points:
        report.errors.append("aucun point valide")
        return report
    vessels = list((await db.execute(select(Vessel))).scalars().all())
    vessel = next((v for v in vessels if v.name.strip().upper() == (vessel_name or "")), None)
    if vessel is None:
        report.errors.append(f"navire « {vessel_name} » introuvable en base")
        return report
    lo, hi = points[0]["recorded_at"], points[-1]["recorded_at"]
    existing_naive = {
        _naive(ts)
        for ts in (
            await db.execute(
                select(VesselPosition.recorded_at)
                .where(VesselPosition.vessel_id == vessel.id)
                .where(VesselPosition.recorded_at >= lo)
                .where(VesselPosition.recorded_at <= hi)
            )
        ).scalars()
    }
    batch = path.name[:100]
    rows: list[dict] = []
    for p in points:
        if _naive(p["recorded_at"]) in existing_naive:
            report.skipped_existing += 1
            continue
        rows.append(
            {
                "vessel_id": vessel.id,
                "source": TOWT_ARCHIVE_SOURCE,
                "import_batch": batch,
                **p,
            }
        )
    for i in range(0, len(rows), CHUNK):
        await db.execute(_insert_stmt(db), rows[i : i + CHUNK])
    report.inserted = len(rows)
    await db.flush()
    return report


def _insert_stmt(db):
    """INSERT en lot ; sur PostgreSQL, ``ON CONFLICT DO NOTHING`` sur la clé
    naturelle — le cron satcom live peut écrire pendant l'import, et un seul
    doublon ne doit pas faire échouer la transaction entière."""
    bind = db.get_bind() if hasattr(db, "get_bind") else None
    if bind is not None and bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(VesselPosition.__table__).on_conflict_do_nothing(
            constraint="uq_vessel_position_time"
        )
    return insert(VesselPosition.__table__)


def _naive(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo else ts


def _files(args) -> list[Path]:
    if args.file:
        return [args.file]
    return sorted(args.dir.glob("towt_gps_*.csv"))


async def run(files: list[Path], *, apply: bool) -> int:
    if not files:
        print("✖ aucun fichier towt_gps_*.csv à importer")
        return 2
    failed = False
    async with SessionLocal() as db:
        for path in files:
            rep = await import_file(db, path)
            status = "✖" if rep.errors else "✔"
            print(
                f"{status} {rep.name}: {rep.read} lus, {rep.inserted} insérés, "
                f"{rep.skipped_existing} déjà présents, {rep.skipped_duplicate} doublons, "
                f"{rep.skipped_invalid} invalides"
                + (f" ({rep.first} → {rep.last})" if rep.first else "")
            )
            for e in rep.errors:
                print(f"    {e}")
            failed = failed or bool(rep.errors)
        if failed:
            await db.rollback()
            print("✖ Erreurs : rien n'est écrit.")
            return 1
        if apply:
            await db.commit()
            print("✔ Commit effectué.")
        else:
            await db.rollback()
            print("ℹ Dry-run : aucune écriture (relancer avec --yes pour appliquer).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", type=Path, help="dossier des CSV consolidés")
    group.add_argument("--file", type=Path, help="un seul CSV consolidé")
    parser.add_argument("--yes", action="store_true", help="Appliquer (sinon dry-run)")
    args = parser.parse_args(argv)
    return asyncio.run(run(_files(args), apply=args.yes))


if __name__ == "__main__":
    sys.exit(main())
