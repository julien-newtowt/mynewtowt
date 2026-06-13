"""Renumérotation des routes (legs) — recalcule les ``leg_code``.

Réaligne tous les ``leg_code`` sur le format canonique
``{seq}{vessel_code}{POL}{POD}{year_digit}`` (cf. ``services.planning``),
où ``seq`` est la position **chronologique** (par ETD) du leg dans son
année, pour son navire. Utile après des imports, des suppressions ou des
décalages qui ont laissé des séquences avec des trous ou des doublons.

Le recalcul se fait en **deux phases dans une seule transaction** pour ne
jamais violer la contrainte ``UNIQUE(leg_code)`` pendant la bascule :

  1. tous les legs visés reçoivent un code temporaire ``TMP-{id}`` ;
  2. chaque leg reçoit son code final recalculé.

OPÉRATION SENSIBLE (le ``leg_code`` est imprimé sur des documents) —
**dry-run par défaut**.

Usage :
  python -m scripts.renumber_legs                  # dry-run : montre le plan
  python -m scripts.renumber_legs --yes            # exécute (tous les navires)
  python -m scripts.renumber_legs --vessel ANE     # dry-run, navire ANE
  python -m scripts.renumber_legs --vessel ANE --yes
  python -m scripts.renumber_legs --year 2026 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.planning import _leg_code_for


async def _load_legs(db, *, vessel_code: str | None, year: int | None) -> list[Leg]:
    stmt = select(Leg).order_by(Leg.vessel_id, Leg.etd)
    if vessel_code:
        vessel = (
            await db.execute(select(Vessel).where(Vessel.code == vessel_code))
        ).scalar_one_or_none()
        if vessel is None:
            raise SystemExit(f"Navire {vessel_code!r} introuvable.")
        stmt = stmt.where(Leg.vessel_id == vessel.id)
    legs = list((await db.execute(stmt)).scalars().all())
    if year:
        legs = [leg for leg in legs if leg.etd and leg.etd.year == year]
    return legs


async def _build_plan(db, legs: list[Leg]) -> list[tuple[Leg, str]]:
    """Retourne [(leg, nouveau_code)] en séquence chronologique par (navire, année)."""
    vessels = {
        v.id: v for v in (await db.execute(select(Vessel))).scalars().all()
    }
    ports = {
        p.id: p for p in (await db.execute(select(Port))).scalars().all()
    }

    # Numérotation chronologique : ETD croissant dans chaque groupe.
    counters: dict[tuple[int, int], int] = {}
    plan: list[tuple[Leg, str]] = []
    for leg in sorted(legs, key=lambda li: (li.vessel_id, li.etd)):
        vessel = vessels.get(leg.vessel_id)
        pol = ports.get(leg.departure_port_id)
        pod = ports.get(leg.arrival_port_id)
        if not (vessel and pol and pod and leg.etd):
            print(f"  ! leg id={leg.id} ({leg.leg_code}) ignoré — référence manquante")
            continue
        key = (leg.vessel_id, leg.etd.year)
        counters[key] = counters.get(key, 0) + 1
        new_code = _leg_code_for(vessel.code, pol.country, pod.country, leg.etd, counters[key])
        plan.append((leg, new_code))
    return plan


async def run(*, execute: bool, vessel_code: str | None, year: int | None) -> int:
    async with SessionLocal() as db:
        legs = await _load_legs(db, vessel_code=vessel_code, year=year)
        if not legs:
            print("Aucun leg à renuméroter.")
            return 0

        plan = await _build_plan(db, legs)
        changes = [(leg, code) for leg, code in plan if leg.leg_code != code]

        print(f"Legs analysés : {len(plan)} · à renuméroter : {len(changes)}\n")
        for leg, code in plan:
            flag = "→" if leg.leg_code != code else " "
            mark = "  CHANGE" if leg.leg_code != code else ""
            print(f"  {leg.leg_code:>10} {flag} {code:<10} (id={leg.id}, ETD {leg.etd:%Y-%m-%d}){mark}")

        if not changes:
            print("\nTout est déjà conforme — rien à faire.")
            return 0

        if not execute:
            print(f"\n[dry-run] {len(changes)} code(s) seraient modifiés. "
                  "Relancez avec --yes pour appliquer.")
            return 0

        # Phase 1 — codes temporaires (évite les collisions UNIQUE transitoires).
        for leg, _code in changes:
            leg.leg_code = f"TMP-{leg.id}"
        await db.flush()

        # Phase 2 — codes finaux.
        for leg, code in changes:
            leg.leg_code = code
        await db.flush()

        await db.commit()
        print(f"\n✓ {len(changes)} leg(s) renuméroté(s).")
        return len(changes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Renumérotation des routes (legs).")
    parser.add_argument("--yes", action="store_true", help="applique réellement (sinon dry-run).")
    parser.add_argument("--vessel", help="code navire (ex. ANE) — sinon tous.")
    parser.add_argument("--year", type=int, help="année d'ETD à cibler — sinon toutes.")
    args = parser.parse_args(argv)
    asyncio.run(run(execute=args.yes, vessel_code=args.vessel, year=args.year))
    return 0


if __name__ == "__main__":
    sys.exit(main())
