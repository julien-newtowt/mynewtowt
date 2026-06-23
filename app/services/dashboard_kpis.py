"""ADM-03 — KPI métier du dashboard staff (CA, CO₂ évité, remplissage, départs).

Le dashboard V3 s'était appauvri (legs / commandes / tickets seulement). On
restaure les indicateurs de pilotage de la V2 : chiffre d'affaires
prévisionnel, CO₂ évité (prévisionnel), taux de remplissage de la flotte et la
table des prochains départs.

Tout est calculé en lecture seule, de façon robuste (un leg en erreur de
capacité — fenêtre fermée, sans navire — est simplement ignoré).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Order
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.services import capacity as capacity_svc
from app.services import co2 as co2_svc

# Statuts de commande comptés au CA prévisionnel (engagés et au-delà).
CA_FORECAST_STATUSES = ("confirmed", "loaded", "delivered")
# Poids moyen par palette pour l'estimation CO₂ prévisionnelle (tonnes).
DEFAULT_PALLET_WEIGHT_T = Decimal("0.5")


async def ca_previsionnel(db: AsyncSession) -> Decimal:
    """CA prévisionnel = Σ total des commandes engagées (confirmées et +)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(Order.total_eur), 0)).where(
            Order.status.in_(CA_FORECAST_STATUSES)
        )
    )
    return Decimal(total or 0)


async def fleet_kpis(db: AsyncSession, now: datetime) -> dict:
    """Remplissage flotte + CO₂ évité prévisionnel sur les legs à venir.

    Une seule passe sur les legs réservables non encore appareillés. Les legs
    dont la capacité n'est pas calculable (fenêtre fermée, sans navire) sont
    ignorés.
    """
    legs = list(
        (await db.execute(select(Leg).where(Leg.etd > now, Leg.is_bookable.is_(True))))
        .scalars()
        .all()
    )
    reserved = 0
    capacity = 0
    co2_avoided_kg = Decimal(0)
    for leg in legs:
        try:
            info = await capacity_svc.get_available_capacity(db, leg.id)
        except (capacity_svc.NotBookable, capacity_svc.BookingClosed, ValueError):
            continue
        reserved += info.reserved_palettes
        capacity += info.capacity_palettes
        if leg.distance_nm and info.reserved_palettes > 0:
            tonnage = Decimal(info.reserved_palettes) * DEFAULT_PALLET_WEIGHT_T
            est = co2_svc.estimate(distance_nm=Decimal(leg.distance_nm), tonnage_t=tonnage)
            co2_avoided_kg += est.avoided_co2_kg
    occupancy_pct = round(100 * reserved / capacity, 1) if capacity else 0.0
    return {
        "reserved": reserved,
        "capacity": capacity,
        "occupancy_pct": occupancy_pct,
        "co2_avoided_kg": co2_avoided_kg,
    }


async def upcoming_departures(db: AsyncSession, now: datetime, limit: int = 8) -> list[dict]:
    """Table des prochains départs (legs à venir, par ETD croissant)."""
    legs = list(
        (await db.execute(select(Leg).where(Leg.etd > now).order_by(Leg.etd.asc()).limit(limit)))
        .scalars()
        .all()
    )
    out: list[dict] = []
    for leg in legs:
        vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
        out.append(
            {
                "leg_id": leg.id,
                "leg_code": leg.leg_code,
                "vessel": vessel.name if vessel else "—",
                "etd": leg.etd,
            }
        )
    return out
