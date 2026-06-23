"""Capacity service — answers "how much room is left on this leg?".

The check_and_lock variant takes a pessimistic row lock to prevent
double-booking under concurrent confirmation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.commercial import PALETTE_COEFFICIENTS, Order, OrderAssignment
from app.models.leg import Leg
from app.models.vessel import Vessel


@dataclass(frozen=True)
class CapacityInfo:
    leg_id: int
    capacity_palettes: int
    reserved_palettes: int
    available_palettes: int

    @property
    def occupancy_pct(self) -> float:
        if self.capacity_palettes == 0:
            return 0.0
        return round(100 * self.reserved_palettes / self.capacity_palettes, 1)


class BookingClosed(Exception):
    """Booking window closed for this leg."""


class CapacityExceeded(Exception):
    """Requested palettes exceed available capacity."""


class NotBookable(Exception):
    """Leg is not flagged as bookable."""


# Statuses that count toward reserved capacity
_RESERVED_STATUSES: tuple[str, ...] = (
    "submitted",
    "confirmed",
    "loaded",
    "at_sea",
    "discharged",
)

# FLX-01 — le rail commercial classique (commandes) consomme la même cale
# que les bookings : ses statuts « engageants » réservent la capacité.
_ORDER_RESERVED_STATUSES: tuple[str, ...] = ("confirmed", "loaded")


async def _reserved_by_orders(db: AsyncSession, leg_id: int) -> int:
    """Palettes (équivalent EPAL) réservées par les commandes du rail classique.

    Les commandes ventilées comptent via leurs ``order_assignments`` (avec le
    coefficient du format) ; les commandes affectées directement au leg et
    jamais ventilées comptent via ``booked_palettes``.
    """
    assign_stmt = (
        select(OrderAssignment.palettes_count, OrderAssignment.pallet_format)
        .join(Order, Order.id == OrderAssignment.order_id)
        .where(
            OrderAssignment.leg_id == leg_id,
            Order.status.in_(_ORDER_RESERVED_STATUSES),
            # B2.2 — une commande reprise en booking est comptée via le booking ;
            # l'exclure ici évite le double-comptage.
            Order.booking_id.is_(None),
        )
    )
    total = Decimal("0")
    for palettes_count, pallet_format in (await db.execute(assign_stmt)).all():
        coef = Decimal(str(PALETTE_COEFFICIENTS.get(pallet_format, 1.0)))
        total += Decimal(palettes_count or 0) * coef

    has_assignment = select(OrderAssignment.id).where(OrderAssignment.order_id == Order.id).exists()
    direct_stmt = select(func.coalesce(func.sum(Order.booked_palettes), 0)).where(
        Order.leg_id == leg_id,
        Order.status.in_(_ORDER_RESERVED_STATUSES),
        ~has_assignment,
        # B2.2 — idem : une commande reprise en booking ne réserve plus
        # directement (sa palette est portée par le booking).
        Order.booking_id.is_(None),
    )
    direct = Decimal(int((await db.scalar(direct_stmt)) or 0))
    return math.ceil(total + direct)


async def _leg_with_vessel(
    db: AsyncSession, leg_id: int, *, lock: bool = False
) -> tuple[Leg, Vessel]:
    stmt = select(Leg, Vessel).join(Vessel, Vessel.id == Leg.vessel_id).where(Leg.id == leg_id)
    if lock:
        stmt = stmt.with_for_update(of=Leg)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise ValueError(f"Leg {leg_id} not found")
    return row[0], row[1]


async def get_available_capacity(
    db: AsyncSession, leg_id: int, *, lock: bool = False
) -> CapacityInfo:
    leg, vessel = await _leg_with_vessel(db, leg_id, lock=lock)

    if not leg.is_bookable:
        raise NotBookable()

    now = datetime.now(UTC)
    if leg.booking_close_at and leg.booking_close_at < now:
        raise BookingClosed()
    if leg.booking_open_at and leg.booking_open_at > now:
        raise BookingClosed()
    if leg.atd is not None:
        raise BookingClosed()

    capacity = leg.public_capacity_palettes or vessel.capacity_palettes

    reserved = await db.scalar(
        select(func.coalesce(func.sum(Booking.total_palettes), 0))
        .where(Booking.leg_id == leg_id)
        .where(Booking.status.in_(_RESERVED_STATUSES))
    )
    reserved = int(reserved or 0) + await _reserved_by_orders(db, leg_id)

    available = max(capacity - reserved, 0)
    return CapacityInfo(
        leg_id=leg_id,
        capacity_palettes=capacity,
        reserved_palettes=reserved,
        available_palettes=available,
    )


async def check_and_lock(db: AsyncSession, leg_id: int, palettes_requested: int) -> CapacityInfo:
    """Lock the leg row and verify capacity before confirming a booking.

    Caller must be inside a transaction that will eventually commit. Raises
    CapacityExceeded if palettes_requested > available.
    """
    info = await get_available_capacity(db, leg_id, lock=True)
    if palettes_requested > info.available_palettes:
        raise CapacityExceeded(
            f"Requested {palettes_requested}, only {info.available_palettes} available"
        )
    return info
