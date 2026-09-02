"""Reprise des distances théoriques de planning manquantes.

``Leg.distance_nm`` (orthodromie POL→POD × coefficient d'élongation) est posée
à la création et à chaque modification d'un leg — mais elle vaut ``None`` dès
que l'un des deux ports n'avait **pas de coordonnées** à ce moment-là, et rien
ne la recalcule ensuite. Conséquence visible : sur ``/performance/navigation``,
les colonnes **Théorique**, **Écart** et **Allongement** restent vides pour ces
voyages (l'écart et l'allongement se dérivent de la théorique).

Ce script rejoue le calcul à froid. Il ne peut évidemment pas inventer des
coordonnées : les ports qui en manquent sont **listés nommément** en fin de
rapport, à renseigner dans Admin → Ports → (port) → Position géographique
(l'écran recalcule alors les legs concernés tout seul).

Dry-run par défaut.

Usage :
  python -m scripts.backfill_leg_distances              # dry-run
  python -m scripts.backfill_leg_distances --yes        # applique
  python -m scripts.backfill_leg_distances --all --yes  # recalcule AUSSI
                                                        # les distances déjà
                                                        # posées (après
                                                        # correction de
                                                        # coordonnées)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models.leg import Leg
from app.models.port import Port
from app.services.planning import recompute_leg_distances


async def _ports_without_coordinates(db) -> list[Port]:
    """Ports dépourvus de latitude ou de longitude et utilisés par un leg."""
    used = select(Leg.departure_port_id).union(select(Leg.arrival_port_id))
    rows = await db.execute(
        select(Port)
        .where(Port.id.in_(used))
        .where(or_(Port.latitude.is_(None), Port.longitude.is_(None)))
        .order_by(Port.locode)
    )
    return list(rows.scalars().all())


async def run(*, apply: bool, only_missing: bool) -> int:
    async with SessionLocal() as db:
        before_missing = await db.scalar(select(Leg.id).where(Leg.distance_nm.is_(None)).limit(1))
        changed = await recompute_leg_distances(db, only_missing=only_missing)
        for leg_id, leg_code, old, new in changed:
            old_txt = f"{old:.0f} NM" if old is not None else "—"
            print(f"  {leg_code:9} (#{leg_id})  {old_txt:>9}  ⇒  {new:.0f} NM")
        if not changed:
            print(
                "  aucune distance à recalculer."
                if before_missing is None
                else "  aucune distance recalculable (voir les ports sans coordonnées)."
            )

        blind = await _ports_without_coordinates(db)
        remaining = list(
            (await db.execute(select(Leg).where(Leg.distance_nm.is_(None)).order_by(Leg.etd.asc())))
            .scalars()
            .all()
        )
        if blind:
            print("\n⚠ Ports sans coordonnées (à renseigner dans Admin → Ports) :")
            for p in blind:
                print(f"    {p.locode:6} {p.name} ({p.country})")
        if remaining:
            print(
                f"\n⚠ {len(remaining)} leg(s) restent sans distance théorique : "
                + ", ".join(lg.leg_code for lg in remaining[:15])
                + (" …" if len(remaining) > 15 else "")
            )

        if apply:
            await db.commit()
            print(f"\n✔ {len(changed)} leg(s) mis à jour — modifications enregistrées.")
        else:
            await db.rollback()
            print(
                f"\n(dry-run) {len(changed)} leg(s) seraient mis à jour — "
                "relancer avec --yes pour appliquer."
            )
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--all",
        action="store_true",
        help="recalcule aussi les distances déjà posées (défaut : uniquement les manquantes)",
    )
    ap.add_argument("--yes", action="store_true", help="applique (sinon dry-run)")
    args = ap.parse_args(argv)
    return asyncio.run(run(apply=args.yes, only_missing=not args.all))


if __name__ == "__main__":
    sys.exit(main())
