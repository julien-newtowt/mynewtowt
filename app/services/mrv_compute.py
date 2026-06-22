"""MRV — calcul de consommation (compteurs DO) + contrôle qualité (A1 hybride).

Paradigme V2 réintroduit : la consommation ME/AE d'un événement se déduit des
**deltas de compteurs DO** entre événements consécutifs d'un même leg (×
densité MDO), et le ROB calculé se chaîne (ROB précédent + soutage − conso). Le
contrôle qualité applique plusieurs règles et pose un statut ``error`` bloquant.

Compteurs exprimés en m³ ; densité en t/m³ → consommation en tonnes.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mrv import MRVEvent, MRVParameter

DEFAULT_DENSITY_T_M3 = Decimal("0.845")
DEFAULT_DEVIATION_T = Decimal("2.0")
DEFAULT_CO2_FACTOR = Decimal("3.206")

_COUNTERS = ("port_me_do_counter", "stbd_me_do_counter", "fwd_gen_do_counter", "aft_gen_do_counter")


async def _param(db: AsyncSession, name: str, default: Decimal) -> Decimal:
    row = (
        await db.execute(select(MRVParameter).where(MRVParameter.name == name))
    ).scalar_one_or_none()
    return Decimal(str(row.value)) if row is not None else default


async def resolve_density(db: AsyncSession) -> Decimal:
    return await _param(db, "avg_mdo_density", DEFAULT_DENSITY_T_M3)


async def resolve_deviation(db: AsyncSession) -> Decimal:
    return await _param(db, "mdo_admissible_deviation", DEFAULT_DEVIATION_T)


def _has_all_counters(ev: MRVEvent) -> bool:
    return all(getattr(ev, c) is not None for c in _COUNTERS)


def _declared_rob_t(ev: MRVEvent, density: Decimal) -> Decimal | None:
    if ev.rob_l is None:
        return None
    # litres → m³ → tonnes
    return (Decimal(str(ev.rob_l)) / Decimal(1000)) * density


def validate_event(ev: MRVEvent, prev: MRVEvent | None, *, density: Decimal, deviation: Decimal) -> None:
    """Pose ``quality_status`` (ok/warning/error) + notes sur un événement."""
    errors: list[str] = []
    warnings: list[str] = []

    # Règle 1 — compteurs monotones croissants (une baisse = anomalie/erreur).
    if prev is not None and _has_all_counters(ev) and _has_all_counters(prev):
        for c in _COUNTERS:
            if Decimal(str(getattr(ev, c))) < Decimal(str(getattr(prev, c))):
                errors.append(f"Compteur {c} en baisse vs événement précédent.")

    # Règle 2 — ROB déclaré vs calculé.
    declared = _declared_rob_t(ev, density)
    if declared is not None and ev.rob_calculated_t is not None:
        diff = abs(Decimal(str(ev.rob_calculated_t)) - declared)
        if diff > deviation:
            errors.append(f"Écart ROB déclaré/calculé {diff:.2f} t > {deviation} t admissibles.")
        elif diff > Decimal("0.5"):
            warnings.append(f"Écart ROB déclaré/calculé {diff:.2f} t.")

    # Règle 3 — cargo constant en transit (consommation).
    if (
        prev is not None
        and ev.event_kind == "noon_consumption"
        and ev.cargo_carried_t is not None
        and prev.cargo_carried_t is not None
        and Decimal(str(ev.cargo_carried_t)) != Decimal(str(prev.cargo_carried_t))
    ):
        warnings.append("Cargo modifié en transit.")

    if errors:
        ev.quality_status = "error"
    elif warnings:
        ev.quality_status = "warning"
    else:
        ev.quality_status = "ok"
    ev.quality_notes = " ".join(errors + warnings) or None


async def recompute_leg(db: AsyncSession, leg_id: int) -> int:
    """Recalcule conso ME/AE, ROB calculé et qualité de tous les events d'un leg.

    Chaîne les événements par ``recorded_at``. Retourne le nombre d'événements
    recalculés.
    """
    density = await resolve_density(db)
    deviation = await resolve_deviation(db)
    events = list(
        (
            await db.execute(
                select(MRVEvent)
                .where(MRVEvent.leg_id == leg_id)
                .order_by(MRVEvent.recorded_at.asc(), MRVEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    prev: MRVEvent | None = None
    for ev in events:
        # Consommation ME/AE depuis les deltas de compteurs.
        if _has_all_counters(ev) and prev is not None and _has_all_counters(prev):
            d = lambda c: Decimal(str(getattr(ev, c))) - Decimal(str(getattr(prev, c)))  # noqa: E731
            me = (d("port_me_do_counter") + d("stbd_me_do_counter")) * density
            ae = (d("fwd_gen_do_counter") + d("aft_gen_do_counter")) * density
            ev.me_consumption_t = me
            ev.ae_consumption_t = ae
            ev.total_consumption_t = me + ae
        elif ev.fuel_mass_t is not None:
            # Repli noon report : la masse fournie EST la consommation totale.
            ev.me_consumption_t = None
            ev.ae_consumption_t = None
            ev.total_consumption_t = Decimal(str(ev.fuel_mass_t))
        else:
            ev.me_consumption_t = ev.ae_consumption_t = ev.total_consumption_t = None

        # ROB calculé chaîné : base + soutage − consommation.
        bunker = Decimal(str(ev.bunkering_qty_t)) if ev.bunkering_qty_t is not None else Decimal(0)
        cons = Decimal(str(ev.total_consumption_t)) if ev.total_consumption_t is not None else Decimal(0)
        if prev is not None and prev.rob_calculated_t is not None:
            ev.rob_calculated_t = Decimal(str(prev.rob_calculated_t)) + bunker - cons
        else:
            # 1er événement : on initialise sur le ROB déclaré s'il existe.
            ev.rob_calculated_t = _declared_rob_t(ev, density)

        validate_event(ev, prev, density=density, deviation=deviation)
        prev = ev

    await db.flush()
    return len(events)


async def leg_has_quality_errors(db: AsyncSession, leg_id: int | None = None) -> bool:
    """True si au moins un événement (du leg, ou global) est en statut error."""
    stmt = select(MRVEvent.id).where(MRVEvent.quality_status == "error")
    if leg_id is not None:
        stmt = stmt.where(MRVEvent.leg_id == leg_id)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None
