"""Reprise d'historique TOWT — création des legs d'archive (ADR-014).

NEWTOWT est la reprise d'une compagnie antérieure (TOWT). Ce script crée dans
``legs`` les voyages 2024-2026 de l'ancienne compagnie, tels que consignés
dans le classeur « Historique_Traversées_V2.xlsx » (onglets ANEMOS / ARTEMIS
LIST VIEW), consolidé dans ``scripts/data/towt_legs_history.csv`` avec les
corrections documentées colonne par colonne (``notes``, ``source_ata_raw``).

Ce que fait un leg d'archive :
- ``origin = "towt_archive"`` → lecture seule (``services.planning.assert_leg_mutable``),
  exclu de la renumérotation des codes, filtrable dans /planning (« Archive TOWT »).
- ``leg_code`` = TRIP CODE TOWT d'origine (``1YMB4``…), jamais recalculé : c'est
  la clé de rapprochement avec les noon reports (« Voyage number ») et l'ancien
  tableau de bord Power BI (TRIP CODE).
- Dates : seules les dates RÉELLES au jour sont connues (ATD/ATA). Le
  prévisionnel n'existe pas dans l'archive : ``etd_ref = etd = atd`` et
  ``eta_ref = eta = ata`` (minuit UTC, convention « planification à la journée »),
  ``status = completed``, ``voyage_completed_at = ata``. Aucune clôture
  administrative n'est fabriquée (``closure_*`` restent NULL, sauf la note de
  provenance).
- Les ATD/ATA sont posées **directement** (comme ``import_mrv_2025``), pas via
  ``voyage_transitions`` : il n'y a ni SOF à créer, ni ETA à re-ancrer, ni leg
  suivant à activer — c'est une reprise de faits, pas une déclaration.
- Validation de séquence (chevauchement, continuité géographique) : NON
  appliquée en blocage — l'archive contient des ruptures connues (arrêts
  techniques non tracés), signalées en ``⚠`` dans le rapport, jamais inventées.

Idempotence : un ``leg_code`` déjà présent est ignoré (jamais modifié). Un leg
homonyme d'origine ``newtowt`` est signalé comme collision et bloque le commit.

OPÉRATION SENSIBLE — dry-run par défaut (tout le travail est fait puis ROLLBACK,
le rapport est donc identique au réel) ; ``--yes`` pour committer.

Usage :
    python -m scripts.import_towt_legs                    # dry-run
    python -m scripts.import_towt_legs --yes              # applique
    python -m scripts.import_towt_legs --file autre.csv --vessel ANEMOS
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models.leg import LEG_ORIGIN_TOWT, Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import compute_effective_distance_nm

DEFAULT_FILE = Path(__file__).parent / "data" / "towt_legs_history.csv"
ACTOR = "reprise-historique-towt"
SOURCE_LABEL = "Historique_Traversées_V2.xlsx"

# Ports cités par l'archive et absents des catalogues embarqués du dépôt
# (COSTM, GTPBR, REREU, CAMAT…). Coordonnées approximatives du port (degrés
# décimaux) — ``source="user"`` (précédence 10) : un import UN/LOCODE ou
# World Ports ultérieur les raffinera sans jamais être bloqué. Un port déjà
# présent en base n'est JAMAIS modifié par ce script.
PORT_CATALOGUE: dict[str, tuple[str, str, float, float]] = {
    "FRLEH": ("Le Havre", "FR", 49.4858, 0.1108),
    "FRFEC": ("Fécamp", "FR", 49.7597, 0.3703),
    "FRCOC": ("Concarneau", "FR", 47.8736, -3.9169),
    "USNYC": ("New York", "US", 40.6892, -74.0445),
    "COSTM": ("Santa Marta", "CO", 11.2465, -74.2131),
    "CAQUE": ("Québec", "CA", 46.8189, -71.2011),
    "CAMAT": ("Matane", "CA", 48.8494, -67.5311),
    "CUHAV": ("La Havane", "CU", 23.1400, -82.3520),
    "GTPBR": ("Puerto Barrios", "GT", 15.7275, -88.5947),
    "GPPTP": ("Pointe-à-Pitre", "GP", 16.2333, -61.5333),
    "PTOPO": ("Porto (Leixões)", "PT", 41.1850, -8.7030),
    "REREU": ("La Réunion (archive TOWT — Le Port)", "RE", -20.9370, 55.2930),
    "BRSSO": ("São Sebastião", "BR", -23.8060, -45.4030),
    "VNSGN": ("Hô Chi Minh-Ville", "VN", 10.7626, 106.7050),
}


@dataclass
class ArchiveLegRow:
    vessel: str
    trip_code: str
    pol: str
    pod: str
    atd: date
    ata: date
    notes: str = ""
    source_sheet: str = ""


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    ports_created: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _day(value: str, *, code: str, label: str) -> date:
    value = (value or "").strip()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{code} : {label} illisible « {value} » (attendu AAAA-MM-JJ)") from exc


def _midnight_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def load_rows(path: Path, *, vessel_filter: str | None = None) -> list[ArchiveLegRow]:
    with path.open(encoding="utf-8", newline="") as fh:
        raw = list(csv.DictReader(fh))
    rows: list[ArchiveLegRow] = []
    seen: set[str] = set()
    for r in raw:
        code = (r.get("trip_code") or "").strip()
        if not code:
            raise SystemExit(f"Ligne sans trip_code : {r}")
        if code in seen:
            raise SystemExit(f"trip_code en double dans le fichier : {code}")
        seen.add(code)
        vessel = (r.get("vessel") or "").strip().upper()
        if vessel_filter and vessel != vessel_filter.upper():
            continue
        row = ArchiveLegRow(
            vessel=vessel,
            trip_code=code,
            pol=(r.get("pol") or "").strip().upper(),
            pod=(r.get("pod") or "").strip().upper(),
            atd=_day(r.get("atd", ""), code=code, label="atd"),
            ata=_day(r.get("ata", ""), code=code, label="ata"),
            notes=(r.get("notes") or "").strip(),
            source_sheet=(r.get("source_sheet") or "").strip(),
        )
        if row.ata < row.atd:
            raise SystemExit(f"{code} : ATA {row.ata} antérieure à l'ATD {row.atd}")
        if len(row.pol) != 5 or len(row.pod) != 5:
            raise SystemExit(f"{code} : LOCODE invalide ({row.pol} / {row.pod})")
        rows.append(row)
    return rows


async def _vessels_by_name(db, rows: list[ArchiveLegRow]) -> dict[str, Vessel]:
    vessels = list((await db.execute(select(Vessel))).scalars().all())
    by_name = {v.name.strip().upper(): v for v in vessels}
    missing = sorted({r.vessel for r in rows} - set(by_name))
    if missing:
        raise SystemExit(
            f"Navire(s) absent(s) de la base : {', '.join(missing)} — créer les navires "
            "(Admin → flotte ou scripts/seed_demo.py) avant la reprise."
        )
    return by_name


async def ensure_ports(db, rows: list[ArchiveLegRow], report: ImportReport) -> dict[str, Port]:
    wanted = sorted({r.pol for r in rows} | {r.pod for r in rows})
    existing = {
        p.locode: p
        for p in (await db.execute(select(Port).where(Port.locode.in_(wanted)))).scalars().all()
    }
    for locode in wanted:
        if locode in existing:
            continue
        cat = PORT_CATALOGUE.get(locode)
        if cat is None:
            report.errors.append(
                f"port {locode} absent de la base et du catalogue embarqué — "
                "l'ajouter dans Admin → Ports (ou PORT_CATALOGUE) avant la reprise."
            )
            continue
        name, country, lat, lon = cat
        port = Port(
            locode=locode,
            name=name,
            country=country,
            latitude=lat,
            longitude=lon,
            source="user",
            is_active=True,
        )
        db.add(port)
        existing[locode] = port
        report.ports_created.append(locode)
    await db.flush()
    return existing


def _sequence_warnings(rows: list[ArchiveLegRow]) -> list[str]:
    """Ruptures internes à l'archive — signalées, jamais corrigées."""
    out: list[str] = []
    by_vessel: dict[str, list[ArchiveLegRow]] = {}
    for r in rows:
        by_vessel.setdefault(r.vessel, []).append(r)
    for vessel, seq in by_vessel.items():
        seq.sort(key=lambda r: (r.atd, r.ata, r.trip_code))
        prev: ArchiveLegRow | None = None
        for r in seq:
            if r.pol == r.pod:
                out.append(f"{vessel} {r.trip_code} : POL = POD ({r.pol}) — mouvement portuaire")
            if prev is not None:
                if r.atd < prev.ata:
                    out.append(
                        f"{vessel} {r.trip_code} : ATD {r.atd} avant l'ATA {prev.ata} "
                        f"du leg précédent {prev.trip_code}"
                    )
                if prev.pod != r.pol:
                    out.append(
                        f"{vessel} {r.trip_code} : POL {r.pol} ≠ POD {prev.pod} "
                        f"de {prev.trip_code} (repositionnement non tracé)"
                    )
            prev = r
    return out


async def import_legs(db, rows: list[ArchiveLegRow], report: ImportReport) -> None:
    vessels = await _vessels_by_name(db, rows)
    ports = await ensure_ports(db, rows, report)
    report.warnings.extend(_sequence_warnings(rows))
    codes = [r.trip_code for r in rows]
    existing = {
        lg.leg_code: lg
        for lg in (await db.execute(select(Leg).where(Leg.leg_code.in_(codes)))).scalars().all()
    }
    for r in sorted(rows, key=lambda x: (x.vessel, x.atd, x.trip_code)):
        found = existing.get(r.trip_code)
        if found is not None:
            if found.origin != LEG_ORIGIN_TOWT:
                report.errors.append(
                    f"{r.trip_code} : un leg NEWTOWT porte déjà ce code (id={found.id}) — "
                    "collision à résoudre avant la reprise."
                )
            else:
                report.skipped_existing.append(r.trip_code)
            continue
        pol, pod = ports.get(r.pol), ports.get(r.pod)
        if pol is None or pod is None:
            continue  # erreur de port déjà consignée
        vessel = vessels[r.vessel]
        atd, ata = _midnight_utc(r.atd), _midnight_utc(r.ata)
        note = f"Reprise historique TOWT — source {SOURCE_LABEL} ({r.source_sheet})."
        if r.notes:
            note = f"{note} {r.notes}"
        leg = Leg(
            leg_code=r.trip_code,
            vessel_id=vessel.id,
            departure_port_id=pol.id,
            arrival_port_id=pod.id,
            etd_ref=atd,
            eta_ref=ata,
            etd=atd,
            eta=ata,
            atd=atd,
            ata=ata,
            status="completed",
            origin=LEG_ORIGIN_TOWT,
            voyage_completed_at=ata,
            closure_notes=note,
            elongation_coef=None,
            transit_speed_kn=None,
        )
        if pol.id != pod.id:
            leg.distance_nm = await compute_effective_distance_nm(
                db,
                departure_port_id=pol.id,
                arrival_port_id=pod.id,
                elongation_coef=vessel.default_elongation,
            )
        db.add(leg)
        report.created.append(r.trip_code)
    await db.flush()


async def run(path: Path, *, apply: bool, vessel_filter: str | None = None) -> int:
    rows = load_rows(path, vessel_filter=vessel_filter)
    report = ImportReport()
    async with SessionLocal() as db:
        await import_legs(db, rows, report)
        print(f"Source : {path} — {len(rows)} voyage(s) d'archive lus")
        if report.ports_created:
            print(f"✔ ports créés (source=user) : {', '.join(report.ports_created)}")
        print(f"✔ legs créés (origin=towt_archive) : {len(report.created)}")
        for code in report.created:
            print(f"    + {code}")
        if report.skipped_existing:
            print(f"= déjà présents, inchangés : {', '.join(report.skipped_existing)}")
        for w in report.warnings:
            print(f"⚠ {w}")
        for e in report.errors:
            print(f"✖ {e}")
        if report.errors:
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
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="CSV des voyages TOWT")
    parser.add_argument("--vessel", help="Ne reprendre qu'un navire (ANEMOS | ARTEMIS)")
    parser.add_argument("--yes", action="store_true", help="Appliquer (sinon dry-run)")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.file, apply=args.yes, vessel_filter=args.vessel))


if __name__ == "__main__":
    sys.exit(main())
