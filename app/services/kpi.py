"""KPI auto-calcul à partir des données réelles d'un leg clôturé.

Appelé automatiquement depuis closure_approve, et disponible en recalcul
manuel depuis POST /kpi/legs/{leg_id}/sync.
"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.sof_event import SofEvent


async def compute_for_leg(db: AsyncSession, leg: Leg) -> LegKPI:
    """Calcule et persiste un LegKPI pour le leg donné (idempotent : écrase si existant)."""
    from app.services.co2 import estimate as co2_estimate

    # ── Tonnage et palettes depuis les bookings confirmés ──────────────────
    bookings = list((await db.execute(
        select(Booking).where(
            Booking.leg_id == leg.id,
            Booking.status.in_(("confirmed", "loaded", "at_sea", "discharged", "delivered")),
        )
    )).scalars().all())

    total_palettes = sum(b.total_palettes or 0 for b in bookings)
    total_weight_kg = sum(b.total_weight_kg or Decimal(0) for b in bookings)
    tonnage_t = (total_weight_kg / Decimal(1000)).quantize(Decimal("0.01"))

    # ── Durée de mer depuis EOSP → SOSP (ou fallback atd/etd) ─────────────
    sof_events = list((await db.execute(
        select(SofEvent).where(SofEvent.leg_id == leg.id)
        .order_by(SofEvent.occurred_at.asc())
    )).scalars().all())

    eosp = next((e for e in sof_events if e.event_type == "EOSP"), None)
    sosp = next((e for e in sof_events if e.event_type == "SOSP"), None)
    duration_hours: Decimal | None = None
    if eosp and sosp and sosp.occurred_at > eosp.occurred_at:
        delta = sosp.occurred_at - eosp.occurred_at
        duration_hours = Decimal(str(round(delta.total_seconds() / 3600, 2)))
    elif leg.atd and leg.ata:
        delta = leg.ata - leg.atd
        duration_hours = Decimal(str(round(delta.total_seconds() / 3600, 2)))

    # ── Distance et vitesse ────────────────────────────────────────────────
    distance_nm = leg.distance_nm  # haversine calculé par migration 0014

    avg_speed_kn: Decimal | None = None
    if distance_nm and duration_hours and duration_hours > 0:
        avg_speed_kn = (distance_nm / duration_hours).quantize(Decimal("0.01"))

    # ── CO₂ évité ──────────────────────────────────────────────────────────
    co2_avoided_kg: Decimal | None = None
    if distance_nm and tonnage_t > 0:
        try:
            estimate = co2_estimate(distance_nm=distance_nm, tonnage_t=tonnage_t)
            co2_avoided_kg = estimate.avoided_co2_kg.quantize(Decimal("0.01"))
        except Exception:
            pass

    # ── On-time : ATA ≤ ETA contractuelle ─────────────────────────────────
    on_time = True
    if leg.ata and leg.eta_ref:
        on_time = leg.ata <= leg.eta_ref

    # ── Taux d'occupation ─────────────────────────────────────────────────
    occupancy_pct: Decimal | None = None
    if leg.public_capacity_palettes and leg.public_capacity_palettes > 0 and total_palettes > 0:
        occupancy_pct = (
            Decimal(total_palettes) / Decimal(leg.public_capacity_palettes) * 100
        ).quantize(Decimal("0.01"))

    # ── Upsert LegKPI ──────────────────────────────────────────────────────
    existing: LegKPI | None = (await db.execute(
        select(LegKPI).where(LegKPI.leg_id == leg.id)
    )).scalar_one_or_none()

    if existing is None:
        kpi = LegKPI(
            leg_id=leg.id,
            palettes_carried=total_palettes,
            tonnage_kg=total_weight_kg,
            distance_nm=distance_nm,
            duration_hours=duration_hours,
            avg_speed_kn=avg_speed_kn,
            on_time=on_time,
            occupancy_pct=occupancy_pct,
            co2_avoided_kg=co2_avoided_kg,
        )
        db.add(kpi)
    else:
        existing.palettes_carried = total_palettes
        existing.tonnage_kg = total_weight_kg
        existing.distance_nm = distance_nm
        existing.duration_hours = duration_hours
        existing.avg_speed_kn = avg_speed_kn
        existing.on_time = on_time
        existing.occupancy_pct = occupancy_pct
        existing.co2_avoided_kg = co2_avoided_kg
        kpi = existing

    await db.flush()
    return kpi
