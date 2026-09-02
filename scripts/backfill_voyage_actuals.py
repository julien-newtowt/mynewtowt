"""Reprise des dates RÉELLES de départ/arrivée (ATD/ATA) des legs — PLN-SEQ.

Rejoue, pour chaque leg d'un fichier CSV (``leg_code,atd,ata`` — dates ISO
jour ou jour+heure, UTC), les déclarations de départ et d'arrivée **par le
chemin unique** ``services.voyage_transitions`` : la séquence est donc
vérifiée (départ avant arrivée, leg précédent arrivé), le SOF est inscrit
(SOSP/EOSP), l'ETA est re-ancrée, les legs suivants recalés, chaque mouvement
historisé dans ``schedule_revisions`` et le leg précédent terminé
(``voyage_completed_at``) — exactement comme si l'opérateur l'avait déclaré
à l'escale. Mode ``quiet`` : pas de notifications (reprise en masse).

Ordre de traitement : par navire, chronologique (ETD) — indispensable pour
que la garde « un seul leg actif » et la complétion du leg précédent se
posent dans le bon sens. Une date **future** (postérieure à ``--today``,
défaut : maintenant UTC) n'est pas un fait : elle est ignorée et signalée.
Idempotent : rejouer le fichier ne réécrit rien si les dates sont identiques
(une date différente est une correction tracée dans l'historique).

OPÉRATION SENSIBLE — **dry-run par défaut**.

Usage :
  python -m scripts.backfill_voyage_actuals                       # dry-run
  python -m scripts.backfill_voyage_actuals --yes                 # applique
  python -m scripts.backfill_voyage_actuals --file autre.csv --yes
  python -m scripts.backfill_voyage_actuals --today 2026-09-01 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models.leg import Leg
from app.services.planning import ensure_utc
from app.services.voyage_transitions import (
    VoyageSequenceError,
    declare_arrival,
    declare_departure,
)

DEFAULT_FILE = Path(__file__).parent / "data" / "voyage_actuals_2026.csv"
ACTOR = "reprise-dates-reelles"


def _parse(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    return ensure_utc(datetime.fromisoformat(value.replace("T", " ")))


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if not r.get("leg_code"):
            raise SystemExit(f"Ligne sans leg_code : {r}")
        r["atd_dt"] = _parse(r.get("atd"))
        r["ata_dt"] = _parse(r.get("ata"))
        if r["atd_dt"] is None and r["ata_dt"] is not None:
            raise SystemExit(f"{r['leg_code']} : une ATA sans ATD viole la séquence.")
        if r["atd_dt"] and r["ata_dt"] and r["ata_dt"] < r["atd_dt"]:
            raise SystemExit(f"{r['leg_code']} : ATA antérieure à l'ATD.")
    return rows


async def run(path: Path, *, apply: bool, today: datetime) -> int:
    rows = load_rows(path)
    by_code = {r["leg_code"]: r for r in rows}
    async with SessionLocal() as db:
        legs = list(
            (await db.execute(select(Leg).where(Leg.leg_code.in_(list(by_code))))).scalars().all()
        )
        found = {lg.leg_code for lg in legs}
        missing = sorted(set(by_code) - found)
        if missing:
            print(f"⚠ legs introuvables en base (ignorés) : {', '.join(missing)}")
        # Par navire puis ETD : la séquence inter-legs doit se dérouler dans l'ordre.
        legs.sort(key=lambda lg: (lg.vessel_id, ensure_utc(lg.etd), lg.id))

        errors = 0
        for leg in legs:
            row = by_code[leg.leg_code]
            atd, ata = row["atd_dt"], row["ata_dt"]
            line = [f"{leg.leg_code:9}"]
            try:
                if atd is None:
                    line.append("— rien à déclarer")
                elif atd > today:
                    line.append(f"départ {atd:%Y-%m-%d} FUTUR → ignoré (reste prévisionnel)")
                else:
                    # Arrivée réelle connue → l'ETA prévisionnelle reste telle quelle
                    # (re-ancrer une prévision aussitôt supplantée par l'ATA
                    # fausserait le « prévu » affiché).
                    s = await declare_departure(
                        db,
                        leg,
                        at=atd,
                        actor_name=ACTOR,
                        quiet=True,
                        reanchor_eta=(ata is None or ata > today),
                    )
                    tag = "posé" if s["first"] else ("corrigé" if s["changed"] else "inchangé")
                    line.append(f"ATD {atd:%Y-%m-%d} {tag}")
                    if s["completed_leg_ids"]:
                        line.append(f"leg(s) précédent(s) terminé(s) : {s['completed_leg_ids']}")
                    if s["eta_shift_hours"]:
                        line.append(f"ETA re-ancrée {s['eta_shift_hours']:+.0f} h")
                    if ata is not None and ata > today:
                        line.append(f"arrivée {ata:%Y-%m-%d} FUTURE → ignorée")
                    elif ata is not None:
                        s2 = await declare_arrival(db, leg, at=ata, actor_name=ACTOR, quiet=True)
                        tag = (
                            "posée"
                            if s2["first"]
                            else ("corrigée" if s2["changed"] else "inchangée")
                        )
                        line.append(f"ATA {ata:%Y-%m-%d} {tag}")
                        if s2.get("next_leg_code"):
                            line.append(f"→ leg suivant {s2['next_leg_code']}")
                    # Seul un leg aval déjà appareillé qui bloque le recalage est un
                    # incident ; les autres entrées de ``skipped`` sont informatives
                    # (ex. packing lists sans date à décaler).
                    blocked = [
                        x
                        for x in ((s.get("cascade") or {}).get("skipped") or [])
                        if str(x).startswith("downstream_legs:")
                    ]
                    if blocked:
                        line.append(f"⚠ recalage aval bloqué : {blocked}")
                line.append(f"phase={leg.phase}")
            except VoyageSequenceError as e:
                errors += 1
                line.append(f"✖ SÉQUENCE : {e}")
            print(" · ".join(line))

        if apply and errors == 0:
            await db.commit()
            print(f"\n✔ {len(legs)} leg(s) traité(s) — modifications enregistrées.")
        else:
            await db.rollback()
            if errors:
                print(f"\n✖ {errors} violation(s) de séquence — rien n'a été enregistré.")
            else:
                print("\n(dry-run) rien n'a été enregistré — relancer avec --yes pour appliquer.")
        return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help="CSV leg_code,atd,ata")
    ap.add_argument("--yes", action="store_true", help="applique (sinon dry-run)")
    ap.add_argument(
        "--today",
        type=lambda v: ensure_utc(datetime.fromisoformat(v)),
        default=datetime.now(UTC),
        help="borne du réel (ISO) — une date postérieure est ignorée",
    )
    args = ap.parse_args(argv)
    if not args.file.exists():
        raise SystemExit(f"Fichier introuvable : {args.file}")
    return asyncio.run(run(args.file, apply=args.yes, today=args.today))


if __name__ == "__main__":
    sys.exit(main())
