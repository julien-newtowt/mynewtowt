"""Carbon Report — adaptateur legacy sur le grand livre d'émissions (lot 9).

Depuis le lot 9, ce module ne calcule plus rien lui-même : il **adapte** le
grand livre unique (``services.emission_ledger``) vers la dataclass historique
``CarbonResult`` (mêmes champs, mêmes arrondis) consommée par
``services.kpi.compute_for_leg`` (persistance ``LegKPI``) et la vue voyage
``/mrv/voyages/{id}`` (lot 5 ; les anciennes vues ``/mrv/legs/{id}`` ont été
retirées à la bascule, lot 14).

Le grand livre lit les **événements** (``nav_events``) quand ils existent, sinon
retombe sur les ``noon_reports`` legacy (source ``legacy_noon``) — pour un leg
sans événement, les chiffres sont **strictement identiques** à l'ancien calcul
(cf. ``tests/unit/test_carbon.py``, gelé dans la suite de non-régression du lot 9).

Résultats (mêmes intensités que le formulaire CFOTE_09) : CO₂ total, par mille,
par tonne, par tonne·mille (EU MRV). Lecture seule — aucune multiplication
conso × facteur ici (règle d'or : elle vit dans ``emission_ledger``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg


@dataclass(frozen=True)
class CarbonResult:
    """Résultats carbone d'un leg (tonnes / kg / grammes selon l'intensité)."""

    do_consumed_t: Decimal
    distance_nm: Decimal | None
    cargo_t: Decimal
    co2_emitted_t: Decimal
    co2_per_nm_kg: Decimal | None  # kgCO₂ / mille
    co2_per_t_kg: Decimal | None  # kgCO₂ / tonne de cargo
    co2_per_tnm_g: Decimal | None  # gCO₂ / (tonne·mille) — intensité EU MRV
    avoided_co2_kg: Decimal | None  # vs cargo conventionnel (réutilise co2.estimate)
    do_co2_factor: Decimal  # tCO₂ / tDO appliqué


def _q(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places))


async def compute_carbon_for_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    cargo_t: Decimal | None = None,
    distance_nm: Decimal | None = None,
) -> CarbonResult:
    """Indicateurs carbone d'un leg (adaptateur du grand livre, lecture seule).

    ``cargo_t`` / ``distance_nm`` peuvent être passés pour éviter de requêter
    deux fois (ex. depuis ``services.kpi.compute_for_leg``) — ils sont transmis
    au grand livre comme overrides.

    Mapping vers ``CarbonResult`` À L'IDENTIQUE (mêmes champs, mêmes arrondis
    ``0.001`` qu'avant le lot 9) : le CO₂ brut vient du grand livre (seule
    multiplication conso × facteur), les intensités en dérivent par arrondi.
    """
    from app.services.emission_ledger import compute_for_leg as ledger_compute

    result = await ledger_compute(db, leg, cargo_t=cargo_t, distance_nm=distance_nm)

    do_t = result.do_consumed_t if result.do_consumed_t is not None else Decimal("0")
    dist = result.distance_nm
    cargo = result.cargo_bl_t if result.cargo_bl_t is not None else Decimal("0")
    factor = result.do_co2_factor

    # CO₂ (t) : le grand livre a déjà fait la multiplication ; on ne fait
    # qu'arrondir (règle d'or). None (assiette absente) ⇒ 0,000 comme avant.
    co2_t = _q(result.co2_emitted_t, "0.001") if result.co2_emitted_t is not None else _q(
        Decimal("0"), "0.001"
    )

    co2_per_nm_kg = None
    co2_per_t_kg = None
    co2_per_tnm_g = None
    if dist and Decimal(str(dist)) > 0:
        co2_per_nm_kg = _q(co2_t * Decimal("1000") / Decimal(str(dist)), "0.001")
    if cargo and cargo > 0:
        co2_per_t_kg = _q(co2_t * Decimal("1000") / cargo, "0.001")
    if dist and Decimal(str(dist)) > 0 and cargo and cargo > 0:
        co2_per_tnm_g = _q(co2_t * Decimal("1000000") / (cargo * Decimal(str(dist))), "0.001")

    return CarbonResult(
        do_consumed_t=_q(do_t, "0.001"),
        distance_nm=Decimal(str(dist)) if dist else None,
        cargo_t=cargo,
        co2_emitted_t=co2_t,
        co2_per_nm_kg=co2_per_nm_kg,
        co2_per_t_kg=co2_per_t_kg,
        co2_per_tnm_g=co2_per_tnm_g,
        avoided_co2_kg=result.avoided_co2_kg,
        do_co2_factor=factor,
    )
