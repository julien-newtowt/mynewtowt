"""Recalage des legs planifiés sur la règle « ETA + escale » (PLN-SEQ).

Un navire n'enchaîne jamais deux legs sans son temps d'escale. La cascade de
planification applique cette règle à chaque mouvement de date ; ce script la
rejoue **à froid** sur la planification existante (legs saisis ou hérités
enchaînés le même jour) : pour chaque navire, à partir de son voyage courant
(dernier leg ayant un réel — ATD/ATA — sinon le premier leg planifié), les legs
suivants non appareillés sont repoussés au plus tôt à la disponibilité du
précédent (ETA + escale planifiée, défaut 24 h), durée conservée. Les legs déjà
appareillés ne bougent jamais. Chaque déplacement est historisé
(``schedule_revisions``, source ``cascade``, ancre = voyage courant) et les
clients des réservations impactées sont notifiés comme pour toute cascade.

OPÉRATION SENSIBLE (déplace des dates prévisionnelles) — **dry-run par défaut**.

Usage :
  python -m scripts.respace_downstream_legs                 # dry-run, tous navires
  python -m scripts.respace_downstream_legs --vessel 2      # dry-run, navire code 2
  python -m scripts.respace_downstream_legs --yes           # applique
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.services.date_cascade import respace_downstream
from app.services.planning import ensure_utc

ACTOR = "recalage-escales"


def _anchor(lane: list[Leg]) -> Leg | None:
    """Voyage courant : dernier leg avec un réel, sinon le premier planifié."""
    real = [lg for lg in lane if lg.atd is not None or lg.ata is not None]
    if real:
        return real[-1]
    return lane[0] if lane else None


async def run(*, apply: bool, vessel_code: str | None) -> int:
    async with SessionLocal() as db:
        stmt = select(Vessel).order_by(Vessel.code)
        if vessel_code:
            stmt = stmt.where(Vessel.code == vessel_code)
        vessels = list((await db.execute(stmt)).scalars().all())
        if not vessels:
            raise SystemExit(
                f"Navire {vessel_code!r} introuvable." if vessel_code else "Aucun navire."
            )
        total = 0
        for v in vessels:
            lane = list(
                (
                    await db.execute(
                        select(Leg)
                        .where(Leg.vessel_id == v.id)
                        .where(Leg.status != "cancelled")
                        .order_by(Leg.etd.asc(), Leg.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            anchor = _anchor(lane)
            if anchor is None:
                print(f"{v.code} {v.name:10} · aucun leg")
                continue
            before = {lg.id: (ensure_utc(lg.etd), ensure_utc(lg.eta), lg.leg_code) for lg in lane}
            summary = await respace_downstream(db, anchor, actor_name=ACTOR)
            moved = [
                lg
                for lg in lane
                if lg.id != anchor.id
                and (ensure_utc(lg.etd), ensure_utc(lg.eta)) != before[lg.id][:2]
            ]
            total += len(moved)
            print(
                f"{v.code} {v.name:10} · ancre {anchor.leg_code} (phase={anchor.phase}) · {len(moved)} leg(s) recalé(s)"
            )
            for lg in moved:
                o_etd, o_eta, o_code = before[lg.id]
                print(
                    f"    {o_code:9} {o_etd:%Y-%m-%d} → {o_eta:%Y-%m-%d}   ⇒   "
                    f"{ensure_utc(lg.etd):%Y-%m-%d} → {ensure_utc(lg.eta):%Y-%m-%d}"
                    + (f"   (renommé {lg.leg_code})" if lg.leg_code != o_code else "")
                )
            blocked = [
                x for x in summary.get("skipped", []) if str(x).startswith("downstream_legs:")
            ]
            if blocked:
                print(f"    ⚠ recalage bloqué par un leg appareillé : {blocked}")
        if apply:
            await db.commit()
            print(f"\n✔ {total} leg(s) recalé(s) — modifications enregistrées.")
        else:
            await db.rollback()
            print(
                f"\n(dry-run) {total} leg(s) seraient recalés — relancer avec --yes pour appliquer."
            )
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vessel", help="code navire (défaut : tous)")
    ap.add_argument("--yes", action="store_true", help="applique (sinon dry-run)")
    args = ap.parse_args(argv)
    return asyncio.run(run(apply=args.yes, vessel_code=args.vessel))


if __name__ == "__main__":
    sys.exit(main())
