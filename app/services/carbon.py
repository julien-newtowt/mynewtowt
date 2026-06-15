"""Carbon Report — calcul auto des émissions CO₂ d'un leg (CFOTE_09).

Aligne l'ERP sur le *Carbon Report officiel TOWT* (CFOTE_09) tout en
**générant les calculs automatiquement** depuis les données déjà saisies :

- **Consommation DO** : agrégée depuis les noon reports du leg
  (``total_consumption_t`` par report, ou somme des moteurs à défaut).
- **Distance berth-to-berth** : ``leg.distance_nm`` (haversine).
- **Cargo** : tonnage des bookings confirmés du leg.
- **Facteur d'émission** : MEPC.391(81) — 3,206 tCO₂/tDO (éditable /admin/co2).

Résultats (mêmes intensités que le formulaire) : CO₂ total, par mille, par
tonne, par tonne·mille (EU MRV). Lecture seule, pur calcul — aucune écriture.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.leg import Leg
from app.models.noon_report import NoonReport, NoonReportEngine

_ACTIVE_BOOKING = ("confirmed", "loaded", "at_sea", "discharged", "delivered")


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


async def _do_consumed_t(db: AsyncSession, leg_id: int) -> Decimal:
    """Consommation DO totale (t) d'un leg, agrégée depuis les noon reports.

    Pour chaque noon report : ``total_consumption_t`` si renseigné, sinon
    somme des ``do_consumption_t`` de ses moteurs.
    """
    reports = list(
        (await db.execute(select(NoonReport).where(NoonReport.leg_id == leg_id))).scalars().all()
    )
    total = Decimal("0")
    for nr in reports:
        if nr.total_consumption_t is not None:
            total += Decimal(str(nr.total_consumption_t))
            continue
        engine_sum = (
            (
                await db.execute(
                    select(NoonReportEngine).where(NoonReportEngine.noon_report_id == nr.id)
                )
            )
            .scalars()
            .all()
        )
        total += sum(
            (
                Decimal(str(e.do_consumption_t))
                for e in engine_sum
                if e.do_consumption_t is not None
            ),
            Decimal("0"),
        )
    return total


async def _cargo_t(db: AsyncSession, leg_id: int) -> Decimal:
    bookings = list(
        (
            await db.execute(
                select(Booking).where(Booking.leg_id == leg_id, Booking.status.in_(_ACTIVE_BOOKING))
            )
        )
        .scalars()
        .all()
    )
    total_kg = sum((b.total_weight_kg or Decimal(0)) for b in bookings)
    return (Decimal(str(total_kg)) / Decimal("1000")).quantize(Decimal("0.001"))


async def compute_carbon_for_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    cargo_t: Decimal | None = None,
    distance_nm: Decimal | None = None,
) -> CarbonResult:
    """Calcule les indicateurs carbone d'un leg (auto, lecture seule).

    ``cargo_t`` / ``distance_nm`` peuvent être passés pour éviter de requêter
    deux fois (ex. depuis ``services.kpi.compute_for_leg``).
    """
    from app.services.co2 import estimate as co2_estimate
    from app.services.co2 import get_do_co2_factor, get_factors

    do_t = await _do_consumed_t(db, leg.id)
    dist = distance_nm if distance_nm is not None else leg.distance_nm
    cargo = cargo_t if cargo_t is not None else await _cargo_t(db, leg.id)

    factor = await get_do_co2_factor(db)
    co2_t = _q(do_t * factor, "0.001")

    co2_per_nm_kg = None
    co2_per_t_kg = None
    co2_per_tnm_g = None
    if dist and Decimal(str(dist)) > 0:
        co2_per_nm_kg = _q(co2_t * Decimal("1000") / Decimal(str(dist)), "0.001")
    if cargo and cargo > 0:
        co2_per_t_kg = _q(co2_t * Decimal("1000") / cargo, "0.001")
    if dist and Decimal(str(dist)) > 0 and cargo and cargo > 0:
        co2_per_tnm_g = _q(co2_t * Decimal("1000000") / (cargo * Decimal(str(dist))), "0.001")

    avoided = None
    if dist and Decimal(str(dist)) > 0 and cargo and cargo > 0:
        try:
            factors = await get_factors(db)
            avoided = co2_estimate(
                distance_nm=Decimal(str(dist)), tonnage_t=cargo, factors=factors
            ).avoided_co2_kg
        except Exception:
            avoided = None

    return CarbonResult(
        do_consumed_t=_q(do_t, "0.001"),
        distance_nm=Decimal(str(dist)) if dist else None,
        cargo_t=cargo,
        co2_emitted_t=co2_t,
        co2_per_nm_kg=co2_per_nm_kg,
        co2_per_t_kg=co2_per_t_kg,
        co2_per_tnm_g=co2_per_tnm_g,
        avoided_co2_kg=avoided,
        do_co2_factor=factor,
    )
