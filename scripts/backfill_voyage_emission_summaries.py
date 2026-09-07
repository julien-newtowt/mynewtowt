"""Reprise à froid des résumés d'émissions par voyage.

``voyage_emission_summaries`` est un **cache recalculable** du grand livre
(``services.emission_ledger.refresh_summary``), mais il n'était recalculé que
par un seul chemin : le hook de finalisation/validation d'un événement
(``event_capture``). Aucune route, aucun script ne le rejouait à la demande —
la mention « ou à la demande » du docstring décrivait un chemin inexistant.

Conséquence, et c'est le motif de ce script : les colonnes ajoutées par les
migrations ``20260907_0144`` (émissions d'escale) et ``20260907_0145``
(émissions au mouillage, hors périmètre MRV) sont créées à ``NULL`` **sans
backfill** — délibérément, une migration ne devant pas dépendre du code de
calcul du moment. Sans reprise, tout voyage antérieur au déploiement garde
donc ``NULL`` **pour toujours**, et ``/mrv/emissions/port`` affiche « non
calculé » sur chacune de ses lignes : le même écran vide que le hook
``legs_affected_by_event`` a été écrit pour éviter.

Le script ne calcule rien lui-même : il appelle ``refresh_summary``, donc la
règle d'or est respectée (l'unique multiplication consommation × facteur reste
dans le grand livre).

Dry-run par défaut.

Usage :
  python -m scripts.backfill_voyage_emission_summaries               # dry-run
  python -m scripts.backfill_voyage_emission_summaries --yes         # applique
  python -m scripts.backfill_voyage_emission_summaries --vessel ANE --yes
  python -m scripts.backfill_voyage_emission_summaries --missing-only --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services.emission_ledger import refresh_summary


async def _legs_to_refresh(db, *, vessel_code: str | None, missing_only: bool) -> list[Leg]:
    """Voyages déjà partis, du plus récent au plus ancien.

    Borne sur le **départ effectif** (``coalesce(atd, etd)``) : un voyage parti
    en avance n'a pas une ETD à jour, et il a pourtant des émissions à
    matérialiser. Même convention que les écrans de restitution.
    """
    effective_etd = func.coalesce(Leg.atd, Leg.etd)
    stmt = select(Leg).where(effective_etd <= func.now())
    if vessel_code:
        stmt = stmt.where(
            Leg.vessel_id.in_(select(Vessel.id).where(Vessel.code == vessel_code.upper()))
        )
    if missing_only:
        # Résumé absent, OU présent mais sans les grandeurs des deux dernières
        # migrations (le cas exact qui motive ce script).
        stmt = stmt.outerjoin(VoyageEmissionSummary, VoyageEmissionSummary.leg_id == Leg.id).where(
            or_(
                VoyageEmissionSummary.id.is_(None),
                VoyageEmissionSummary.co2_escale_t.is_(None),
                VoyageEmissionSummary.co2_mouillage_t.is_(None),
            )
        )
    return list((await db.execute(stmt.order_by(effective_etd.desc()))).scalars().all())


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="applique (sinon dry-run)")
    parser.add_argument("--vessel", help="restreint à un code navire (ex. ANE)")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="ne rejoue que les voyages sans résumé ou sans émissions escale/mouillage",
    )
    args = parser.parse_args()

    async with SessionLocal() as db:
        legs = await _legs_to_refresh(db, vessel_code=args.vessel, missing_only=args.missing_only)
        if not legs:
            print("Aucun voyage à reprendre.")
            return 0

        print(f"{len(legs)} voyage(s) à reprendre" + ("" if args.yes else " (dry-run)"))
        if not args.yes:
            for leg in legs[:20]:
                print(f"  - {leg.leg_code}")
            if len(legs) > 20:
                print(f"  … et {len(legs) - 20} autres")
            print("\nRelancer avec --yes pour appliquer.")
            return 0

        # 🔴 Un voyage en échec n'interrompt pas la reprise, et il est NOMMÉ.
        # Une reprise silencieusement partielle serait pire que pas de reprise :
        # on croirait le cache à jour.
        done, failed = 0, []
        for leg in legs:
            try:
                await refresh_summary(db, leg)
                done += 1
            except Exception as exc:  # rapport, pas d'arrêt
                failed.append(f"{leg.leg_code} ({type(exc).__name__}: {exc})")
        await db.commit()

        print(f"\n{done} résumé(s) recalculé(s).")
        if failed:
            print(f"{len(failed)} échec(s) — à examiner un par un :")
            for f in failed:
                print(f"  - {f}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
