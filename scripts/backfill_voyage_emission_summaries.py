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

🔴 **Après un déploiement qui ajoute des colonnes, lancer SANS filtre.** C'est
la seule forme dont la justesse ne dépend d'aucune subtilité : ``refresh_summary``
est idempotent, donc reprendre tout est sûr — simplement plus long. Les deux
filtres ci-dessous sont des optimisations, et chacun a un piège :

- ``--missing-only`` ne prend que les voyages **sans résumé du tout** : il ne
  remplira donc **jamais** une colonne nouvelle sur un résumé déjà existant,
  qui est précisément le cas à réparer après une migration additive ;
- ``--computed-before`` exige un **instant de déploiement réel**, pas une date
  du jour : le hook d'événement recalcule en continu, donc « minuit
  aujourd'hui » saute tout résumé déjà rafraîchi plus tôt dans la journée.

Usage :
  python -m scripts.backfill_voyage_emission_summaries               # dry-run
  python -m scripts.backfill_voyage_emission_summaries --yes         # ← APRÈS MIGRATION
  python -m scripts.backfill_voyage_emission_summaries --vessel ANE --yes
  python -m scripts.backfill_voyage_emission_summaries --missing-only --yes
  python -m scripts.backfill_voyage_emission_summaries --computed-before 2026-09-08T14:30:00Z --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services.emission_ledger import refresh_summary


async def _legs_to_refresh(
    db,
    *,
    vessel_code: str | None,
    missing_only: bool,
    computed_before: datetime | None,
) -> list[Leg]:
    """Voyages déjà partis, du plus récent au plus ancien.

    Borne sur le **départ effectif** (``coalesce(atd, etd)``) : un voyage parti
    en avance n'a pas une ETD à jour, et il a pourtant des émissions à
    matérialiser. Même convention que les écrans de restitution.

    🔴 **Deux sélecteurs, et aucun ne se fonde sur un ``NULL``.**

    Une première version filtrait sur ``co2_escale_t IS NULL OR
    co2_mouillage_t IS NULL``. C'était faux : ces ``NULL`` sont souvent
    **légitimes et définitifs** — un leg de source ``legacy_noon`` (ou une
    archive TOWT) n'a aucune granularité d'intervalle, donc jamais de conso au
    mouillage ; une escale encore ouverte n'a pas de conso d'escale tant que le
    départ suivant n'est pas finalisé. Le filtre resélectionnait donc
    éternellement les mêmes voyages et ne pouvait **jamais** annoncer un cache
    à jour.

    - ``--missing-only`` : résumé **absent**, point. Critère net, sans
      ambiguïté.
    - ``--computed-before`` : résumés dont le ``computed_at`` précède une date
      — c'est le vrai critère opérationnel après un déploiement qui ajoute des
      colonnes (« recalculer tout ce qui n'a pas été recalculé depuis »).

    Sans option, tout est repris : ``refresh_summary`` est idempotent, donc
    c'est sûr, simplement plus long.
    """
    effective_etd = func.coalesce(Leg.atd, Leg.etd)
    stmt = select(Leg).where(effective_etd <= func.now())
    if vessel_code:
        stmt = stmt.where(
            Leg.vessel_id.in_(select(Vessel.id).where(Vessel.code == vessel_code.upper()))
        )
    if missing_only:
        stmt = stmt.outerjoin(VoyageEmissionSummary, VoyageEmissionSummary.leg_id == Leg.id).where(
            VoyageEmissionSummary.id.is_(None)
        )
    elif computed_before is not None:
        stmt = stmt.outerjoin(VoyageEmissionSummary, VoyageEmissionSummary.leg_id == Leg.id).where(
            or_(
                VoyageEmissionSummary.id.is_(None),
                VoyageEmissionSummary.computed_at < computed_before,
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
        help="ne rejoue que les voyages dont le résumé est ABSENT",
    )
    parser.add_argument(
        "--computed-before",
        metavar="ISO",
        help=(
            "ne rejoue que les résumés calculés avant cette date "
            "(ex. 2026-09-08 — le vrai critère après un déploiement "
            "qui ajoute des colonnes)"
        ),
    )
    args = parser.parse_args()

    computed_before: datetime | None = None
    if args.computed_before:
        try:
            computed_before = datetime.fromisoformat(args.computed_before)
        except ValueError:
            print(f"--computed-before : date ISO invalide ({args.computed_before!r})")
            return 2
        # 🔴 `computed_at` est un `timestamptz`. Une date ISO sans fuseau donne
        # un datetime NAÏF, qu'asyncpg résout dans le fuseau LOCAL DU
        # CONTENEUR : sur un hôte non-UTC, la borne se décalait silencieusement
        # de plusieurs heures — et une borne de reprise décalée laisse des
        # colonnes NULL sans que rien ne le signale. On force donc UTC, et on
        # le dit à l'écran pour qu'il n'y ait aucun doute sur l'instant retenu.
        if computed_before.tzinfo is None:
            computed_before = computed_before.replace(tzinfo=UTC)
        print(f"Borne retenue : {computed_before.isoformat()} (UTC)")
    if args.missing_only and computed_before is not None:
        print("--missing-only et --computed-before s'excluent : choisir un seul critère.")
        return 2

    async with SessionLocal() as db:
        legs = await _legs_to_refresh(
            db,
            vessel_code=args.vessel,
            missing_only=args.missing_only,
            computed_before=computed_before,
        )
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

        # 🔴 Chaque voyage est une transaction À PART ENTIÈRE.
        #
        # Attraper l'exception sans `rollback()` ne suffit pas : une erreur au
        # niveau base laisse la session empoisonnée, tous les voyages suivants
        # échouent en cascade, et le `commit()` final lève à son tour — jetant
        # au passage TOUT ce qui avait déjà été recalculé. Le commentaire
        # d'origine promettait exactement l'inverse de ce que le code faisait.
        #
        # On commite donc voyage par voyage, et on annule proprement en cas
        # d'échec. Plus de transactions, mais une reprise interrompue laisse un
        # état cohérent et reprenable — c'est ce qu'on veut d'un script à
        # froid, pas la vitesse.
        done, failed = 0, []
        for leg in legs:
            try:
                await refresh_summary(db, leg)
                await db.commit()
                done += 1
            except Exception as exc:  # rapport, pas d'arrêt
                await db.rollback()
                failed.append(f"{leg.leg_code} ({type(exc).__name__}: {exc})")

        print(f"\n{done} résumé(s) recalculé(s).")
        if failed:
            print(f"{len(failed)} échec(s) — à examiner un par un :")
            for f in failed:
                print(f"  - {f}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
