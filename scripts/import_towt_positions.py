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

**Borne d'archive (garde-fou).** Le dossier satcom couvre aussi la période
NEWTOWT et des navires postérieurs à la reprise (Atlantis, Atlas), alors que
ses fichiers sont découpés par année civile. Étiqueter une position NEWTOWT
vivante en ``towt_archive`` la rendrait de plus **impurgeable**. Deux critères
cumulatifs délimitent donc l'archive :

1. **Date de reprise** — ``NEWTOWT_TAKEOVER_DATE`` (2026-05-11, arbitrage
   Julien Gondé du 2026-09-03) : à compter de ce jour, les navires exploitent
   sous NEWTOWT. Tout point à cette date ou après est ignoré (compté « hors
   archive » au rapport). ``--until`` remplace cette borne explicitement.
2. **Navire de l'ancienne compagnie** — le navire doit porter au moins un leg
   ``origin='towt_archive'``. Sinon le fichier est **ignoré** (``⊘``) : Atlantis
   et Atlas n'ont jamais navigué pour TOWT, leurs positions sont vivantes. Une
   exclusion par conception n'interrompt pas le lot ; seul un **échec** (navire
   inconnu en base, fichier illisible, plusieurs navires dans un fichier)
   annule le fichier concerné et fait sortir en code 1.

Conséquence connue : l'Excel des traversées s'arrête au 2026-01-31, alors que
l'exploitation TOWT court jusqu'au 2026-05-11 — les positions de février à mai
2026 sont donc reprises comme archive sans leg auquel se rattacher (elles
restent visibles dans l'historique par dates, pas dans la trace d'un voyage).

OPÉRATION SENSIBLE — dry-run par défaut (travail complet puis ROLLBACK) ;
``--yes`` pour committer. Le commit a lieu **fichier par fichier** : un lot
interrompu est repris à l'identique (idempotence), sans transaction géante.

Usage :
    python -m scripts.import_towt_positions --dir ./gps_towt              # dry-run
    python -m scripts.import_towt_positions --dir ./gps_towt --yes
    python -m scripts.import_towt_positions --file towt_gps_anemos_2025.csv --yes
    python -m scripts.import_towt_positions --dir ./gps_towt --vessel ANEMOS
    python -m scripts.import_towt_positions --dir ./gps_towt --until 2026-05-10
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import insert, select

from app.database import SessionLocal
from app.models.claim import VesselPosition
from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.vessel import Vessel
from app.services.admin_data import TOWT_ARCHIVE_SOURCE

# Date de reprise par NEWTOWT (arbitrage Julien Gondé, 2026-09-03) : à compter
# de ce jour **inclus**, les navires exploitent sous la nouvelle compagnie et
# leurs positions ne sont PAS de l'archive — elles arrivent par le cron
# ``/api/tracking/upload`` et doivent rester purgeables.
NEWTOWT_TAKEOVER_DATE = date(2026, 5, 11)

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
    skipped_after_cutoff: int = 0
    cutoff: str = ""
    first: str = ""
    last: str = ""
    # Exclusions **par conception** (navire hors TOWT, fichier entièrement
    # postérieur à la reprise) : le fichier est ignoré, le lot continue.
    excluded: list[str] = field(default_factory=list)
    # Échecs réels (navire inconnu, fichier illisible, plusieurs navires) :
    # ils annulent le fichier et font échouer le lot.
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


async def is_towt_vessel(db, vessel_id: int) -> bool:
    """Le navire a-t-il navigué pour l'ancienne compagnie ?

    Critère : au moins un leg ``origin='towt_archive'``. Un navire NEWTOWT
    (Atlantis, Atlas) n'en a aucun — ses positions ne sont jamais de l'archive,
    même antérieures à la date de reprise.
    """
    found = (
        await db.execute(
            select(Leg.id)
            .where(Leg.vessel_id == vessel_id)
            .where(Leg.origin == LEG_ORIGIN_TOWT)
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


def takeover_cutoff() -> datetime:
    """Borne d'archive : minuit UTC du jour de reprise NEWTOWT (exclusive)."""
    d = NEWTOWT_TAKEOVER_DATE
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def import_file(db, path: Path, *, until: datetime | None = None) -> FileReport:
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

    if not await is_towt_vessel(db, vessel.id):
        report.excluded.append(
            f"« {vessel_name} » n'a aucun leg d'archive TOWT : ce navire n'a pas "
            "navigué pour l'ancienne compagnie. Ses positions sont vivantes et "
            "arrivent par /api/tracking/upload — les marquer 'towt_archive' les "
            "rendrait impurgeables."
        )
        return report

    cutoff = until or takeover_cutoff()
    report.cutoff = cutoff.date().isoformat()
    kept = [p for p in points if p["recorded_at"] < cutoff]
    report.skipped_after_cutoff = len(points) - len(kept)
    points = kept
    if not points:
        report.excluded.append(
            f"aucun point antérieur à la reprise NEWTOWT du {report.cutoff} — "
            "fichier entièrement hors archive"
        )
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
    """Fichiers à importer. ``--dir`` cherche **récursivement** : un ``docker cp``
    ou un ``scp`` répété ajoute volontiers un niveau de dossier, et l'import ne
    doit pas échouer pour cette raison."""
    if args.file:
        return [args.file]
    pattern = f"towt_gps_{args.vessel.lower()}_*.csv" if args.vessel else "towt_gps_*.csv"
    return sorted(args.dir.rglob(pattern))


async def run(files: list[Path], *, apply: bool, until: datetime | None = None) -> int:
    if not files:
        print("✖ aucun fichier towt_gps_*.csv à importer")
        return 2
    failed = False
    total_inserted = 0
    total_excluded = 0
    async with SessionLocal() as db:
        for path in files:
            rep = await import_file(db, path, until=until)
            status = "✖" if rep.errors else ("⊘" if rep.excluded else "✔")
            print(
                f"{status} {rep.name}: {rep.read} lus, {rep.inserted} insérés, "
                f"{rep.skipped_existing} déjà présents, {rep.skipped_duplicate} doublons, "
                f"{rep.skipped_invalid} invalides"
                + (
                    f", {rep.skipped_after_cutoff} hors archive (≥ {rep.cutoff})"
                    if rep.skipped_after_cutoff
                    else ""
                )
                + (f" ({rep.first} → {rep.last})" if rep.first else "")
            )
            for msg in rep.excluded + rep.errors:
                print(f"    {msg}")
            # Un fichier en échec est annulé seul ; une exclusion par conception
            # (Atlantis, Atlas, fichier post-reprise) n'empêche pas le reste du
            # lot d'être écrit — sinon le seul fichier hors périmètre bloquerait
            # toute la reprise.
            if rep.errors:
                failed = True
                await db.rollback()
                continue
            total_inserted += rep.inserted
            total_excluded += 1 if rep.excluded else 0
            if apply:
                await db.commit()
            else:
                await db.rollback()
        if apply:
            print(f"✔ Commit effectué — {total_inserted} position(s) d'archive écrite(s).")
        else:
            print(
                f"ℹ Dry-run : aucune écriture. {total_inserted} position(s) seraient "
                "insérées (relancer avec --yes pour appliquer)."
            )
        if total_excluded:
            print(f"⊘ {total_excluded} fichier(s) hors archive, ignoré(s) — voir ci-dessus.")
        if failed:
            print("✖ Des fichiers ont échoué (voir ✖ ci-dessus) : ils n'ont rien écrit.")
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", type=Path, help="dossier des CSV consolidés")
    group.add_argument("--file", type=Path, help="un seul CSV consolidé")
    parser.add_argument("--vessel", help="Ne reprendre qu'un navire (anemos | artemis)")
    parser.add_argument(
        "--until",
        help=f"Borne d'archive explicite AAAA-MM-JJ (inclus) — remplace la date "
        f"de reprise NEWTOWT ({NEWTOWT_TAKEOVER_DATE.isoformat()})",
    )
    parser.add_argument("--yes", action="store_true", help="Appliquer (sinon dry-run)")
    args = parser.parse_args(argv)
    until = None
    if args.until:
        try:
            day = datetime.strptime(args.until, "%Y-%m-%d")
        except ValueError:
            print("✖ --until attend une date AAAA-MM-JJ", file=sys.stderr)
            return 2
        until = (day + timedelta(days=1)).replace(tzinfo=UTC)
    if args.dir is not None:
        # Diagnostics explicites : dossier absent, ou illisible parce que
        # ``docker compose cp`` dépose en root alors que le conteneur tourne
        # sous l'utilisateur ``app`` — sinon la recherche renvoie « rien » sans
        # dire pourquoi.
        if not args.dir.is_dir():
            print(f"✖ dossier introuvable : {args.dir}", file=sys.stderr)
            return 2
        if not os.access(args.dir, os.R_OK | os.X_OK):
            print(
                f"✖ dossier illisible par l'utilisateur courant : {args.dir}\n"
                "  Après un « docker compose cp », les fichiers appartiennent à "
                "root : donner l'accès en lecture, par exemple\n"
                "  docker compose exec -u root app chmod -R a+rX "
                f"{args.dir}",
                file=sys.stderr,
            )
            return 2
    files = _files(args)
    if not files and args.dir is not None:
        # Dire ce qui a été trouvé plutôt que « rien à importer » : le cas
        # courant est un niveau de dossier en trop, ou un dossier vide.
        found = sorted(p.name for p in args.dir.rglob("*.csv"))[:10]
        print(f"✖ aucun fichier towt_gps_*.csv sous {args.dir}", file=sys.stderr)
        print(
            f"  CSV présents : {', '.join(found) if found else 'aucun'}",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(run(files, apply=args.yes, until=until))


if __name__ == "__main__":
    sys.exit(main())
