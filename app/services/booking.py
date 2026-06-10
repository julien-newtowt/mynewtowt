"""Booking service — orchestrates booking lifecycle.

Routers should call into this service instead of manipulating ORM objects
directly. Keeps business invariants in one place.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking, BookingItem
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.services.capacity import (
    CapacityExceeded,
    check_and_lock,
    get_available_capacity,
)
from app.services.pricing import PriceQuote, compute_quote


@dataclass(frozen=True)
class BookingItemInput:
    pallet_format: str
    pallet_count: int
    cargo_description: str
    unit_weight_kg: Decimal | None = None
    stackable: bool = True
    hazardous: bool = False
    imdg_class: str | None = None
    un_number: str | None = None
    hs_code: str | None = None


class BookingError(Exception):
    """Base booking error."""


class InvalidStatusTransition(BookingError):
    pass


_REFERENCE_PREFIX = "BK-"


def generate_reference(year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    suffix = secrets.token_hex(2).upper()
    return f"{_REFERENCE_PREFIX}{year}-{suffix}"


def _aggregate_totals(items: Sequence[BookingItemInput]) -> tuple[int, Decimal, bool]:
    total_palettes = sum(i.pallet_count for i in items)
    total_weight = sum(
        (i.unit_weight_kg or Decimal("0")) * Decimal(i.pallet_count) for i in items
    )
    hazardous = any(i.hazardous for i in items)
    return total_palettes, total_weight, hazardous


async def create_draft(
    db: AsyncSession,
    *,
    client: ClientAccount,
    leg: Leg,
    items: Sequence[BookingItemInput],
    pickup_address: str | None,
    delivery_address: str | None,
    shipper_reference: str | None,
    notes: str | None,
) -> tuple[Booking, PriceQuote]:
    """Create a booking in draft status, with an indicative price.

    No capacity lock yet — only at confirm() time.
    """
    capacity = await get_available_capacity(db, leg.id)
    total_palettes, total_weight, hazardous = _aggregate_totals(items)
    if total_palettes <= 0:
        raise BookingError("At least one item with a positive pallet count required")
    if total_palettes > capacity.available_palettes:
        raise CapacityExceeded(
            f"Requested {total_palettes}, available {capacity.available_palettes}"
        )

    quote = compute_quote(
        base_price_per_palette_eur=leg.public_price_per_palette_eur,
        items=[(i.pallet_format, i.pallet_count) for i in items],
        hazardous=hazardous,
        oversize=False,
        etd=leg.etd,
        capacity=capacity,
        client_segment=client.segment,
    )

    booking = Booking(
        reference=generate_reference(),
        client_account_id=client.id,
        leg_id=leg.id,
        status="draft",
        total_palettes=total_palettes,
        total_weight_kg=total_weight,
        hazardous=hazardous,
        estimated_price_eur=quote.total_eur,
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        shipper_reference=shipper_reference,
        notes=notes,
    )
    db.add(booking)
    await db.flush()

    for i in items:
        db.add(
            BookingItem(
                booking_id=booking.id,
                pallet_format=i.pallet_format,
                pallet_count=i.pallet_count,
                cargo_description=i.cargo_description,
                unit_weight_kg=i.unit_weight_kg,
                total_weight_kg=(i.unit_weight_kg or Decimal("0"))
                * Decimal(i.pallet_count),
                stackable=i.stackable,
                hazardous=i.hazardous,
                imdg_class=i.imdg_class,
                un_number=i.un_number,
                hs_code=i.hs_code,
            )
        )

    return booking, quote


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"submitted", "cancelled"},
    "submitted": {"confirmed", "cancelled"},
    "confirmed": {"loaded", "cancelled"},
    "loaded": {"at_sea", "cancelled"},
    "at_sea": {"discharged"},
    "discharged": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}


def _assert_transition(current: str, target: str) -> None:
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStatusTransition(f"{current} → {target} not allowed")


async def submit(db: AsyncSession, booking: Booking) -> Booking:
    _assert_transition(booking.status, "submitted")
    booking.status = "submitted"
    booking.submitted_at = datetime.now(timezone.utc)
    await db.flush()
    return booking


async def confirm(
    db: AsyncSession, booking: Booking, *, price_eur: Decimal | None = None
) -> Booking:
    _assert_transition(booking.status, "confirmed")
    # Re-check capacity with row lock
    await check_and_lock(db, booking.leg_id, booking.total_palettes)
    booking.status = "confirmed"
    booking.confirmed_at = datetime.now(timezone.utc)
    booking.confirmed_price_eur = price_eur or booking.estimated_price_eur
    await db.flush()
    return booking


async def cancel(db: AsyncSession, booking: Booking, reason: str) -> Booking:
    _assert_transition(booking.status, "cancelled")
    booking.status = "cancelled"
    booking.cancelled_at = datetime.now(timezone.utc)
    booking.cancelled_reason = reason
    await db.flush()
    return booking


_STATUS_TIMESTAMP: dict[str, str] = {
    "submitted": "submitted_at",
    "confirmed": "confirmed_at",
    "loaded": "loaded_at",
    "at_sea": "at_sea_at",
    "discharged": "discharged_at",
    "delivered": "delivered_at",
    "cancelled": "cancelled_at",
}


async def advance(db: AsyncSession, booking: Booking, target: str) -> Booking:
    """Generic forward transition for voyage-progression states.

    Centralises the post-confirmation workflow (loaded → at_sea →
    discharged → delivered) so lifecycle side-effects fire from a single
    chokepoint. ``submit`` / ``confirm`` / ``cancel`` keep their own
    pre/post logic (capacity lock, pricing, reason) and are not routed here.
    """
    _assert_transition(booking.status, target)
    booking.status = target
    field = _STATUS_TIMESTAMP.get(target)
    if field and getattr(booking, field, None) is None:
        setattr(booking, field, datetime.now(timezone.utc))
    await db.flush()
    # Effets de bord (notifications client, email, label Anemos). Import
    # tardif pour éviter tout cycle d'import au chargement du module.
    from app.services.booking_lifecycle import on_status_change

    await on_status_change(db, booking, target)
    return booking


async def list_for_client(
    db: AsyncSession, client_id: int, limit: int = 50
) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.client_account_id == client_id)
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def find_by_reference(db: AsyncSession, ref: str) -> Booking | None:
    stmt = select(Booking).where(Booking.reference == ref)
    return (await db.execute(stmt)).scalar_one_or_none()
