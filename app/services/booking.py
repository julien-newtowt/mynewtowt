"""Booking service — orchestrates booking lifecycle.

Routers should call into this service instead of manipulating ORM objects
directly. Keeps business invariants in one place.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking, BookingItem
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.services.capacity import (
    CapacityExceeded,
    check_and_lock,
    get_available_capacity,
)
from app.services.quoting import GridQuote, compute_grid_quote, resolve_grid


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
    year = year or datetime.now(UTC).year
    suffix = secrets.token_hex(2).upper()
    return f"{_REFERENCE_PREFIX}{year}-{suffix}"


def _aggregate_totals(items: Sequence[BookingItemInput]) -> tuple[int, Decimal, bool]:
    total_palettes = sum(i.pallet_count for i in items)
    total_weight = sum((i.unit_weight_kg or Decimal("0")) * Decimal(i.pallet_count) for i in items)
    hazardous = any(i.hazardous for i in items)
    return total_palettes, total_weight, hazardous


async def create_draft(
    db: AsyncSession,
    *,
    client: ClientAccount | None,
    leg: Leg,
    items: Sequence[BookingItemInput],
    pickup_address: str | None,
    delivery_address: str | None,
    shipper_reference: str | None,
    notes: str | None,
    channel: str = "client",
    source_quote_reference: str | None = None,
) -> tuple[Booking, GridQuote]:
    """Create a booking in draft status, with an indicative price.

    No capacity lock yet — only at confirm() time. Le prix indicatif est
    calculé sur la grille tarifaire applicable (grille du client connu,
    sinon grille par défaut de la route) — cf. services.quoting.

    ``client`` peut être ``None`` : c'est le **brouillon invité** du wizard
    (session anonyme). Le compte client est créé et rattaché à la validation
    (étape 3) — cf. autocréation. Le brouillon est alors tarifé sur la grille
    par défaut de la route (pas de grille négociée tant que le compte n'est pas
    connu).

    ``channel`` (B2) trace le rail de remplissage : "client" (wizard public,
    défaut) ou "operator" (back-office). Le wizard client n'a pas à le passer.
    """
    capacity = await get_available_capacity(db, leg.id)
    total_palettes, total_weight, hazardous = _aggregate_totals(items)
    if total_palettes <= 0:
        raise BookingError("At least one item with a positive pallet count required")
    if total_palettes > capacity.available_palettes:
        raise CapacityExceeded(
            f"Requested {total_palettes}, available {capacity.available_palettes}"
        )

    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    if pol is None or pod is None:
        raise BookingError("Route incomplète : ports du leg introuvables")
    grid, route = await resolve_grid(
        db,
        pol_locode=pol.locode,
        pod_locode=pod.locode,
        on_date=leg.etd.date() if leg.etd else None,
        commercial_client_id=(client.commercial_client_id if client else None),
    )
    quote = compute_grid_quote(
        grid,
        route,
        items=[(i.pallet_format, i.pallet_count) for i in items],
        tonnage_t=(total_weight / Decimal("1000")) if total_weight else None,
        hazardous=hazardous,
    )

    booking = Booking(
        reference=generate_reference(),
        client_account_id=(client.id if client else None),
        leg_id=leg.id,
        status="draft",
        channel=channel,
        total_palettes=total_palettes,
        total_weight_kg=total_weight,
        hazardous=hazardous,
        estimated_price_eur=quote.total_eur,
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        shipper_reference=shipper_reference,
        notes=notes,
        source_quote_reference=source_quote_reference,
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
                total_weight_kg=(i.unit_weight_kg or Decimal("0")) * Decimal(i.pallet_count),
                stackable=i.stackable,
                hazardous=i.hazardous,
                imdg_class=i.imdg_class,
                un_number=i.un_number,
                hs_code=i.hs_code,
            )
        )

    return booking, quote


async def create_operator_draft(
    db: AsyncSession,
    *,
    client_account: ClientAccount,
    leg: Leg,
    items: Sequence[BookingItemInput],
    pickup_address: str | None = None,
    delivery_address: str | None = None,
    shipper_reference: str | None = None,
    notes: str | None = None,
) -> tuple[Booking, GridQuote]:
    """Crée une réservation pour le compte d'un client connu (rail opérateur).

    Helper fin au-dessus de :func:`create_draft` : même contrôle de capacité et
    même tarification grille que le wizard client, mais ``channel="operator"``.
    L'opérateur réserve au nom d'un :class:`ClientAccount` existant.
    """
    return await create_draft(
        db,
        client=client_account,
        leg=leg,
        items=items,
        pickup_address=pickup_address,
        delivery_address=delivery_address,
        shipper_reference=shipper_reference,
        notes=notes,
        channel="operator",
    )


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
    booking.submitted_at = datetime.now(UTC)
    await db.flush()
    return booking


async def confirm(
    db: AsyncSession, booking: Booking, *, price_eur: Decimal | None = None
) -> Booking:
    _assert_transition(booking.status, "confirmed")
    # Re-check capacity with row lock
    await check_and_lock(db, booking.leg_id, booking.total_palettes)
    booking.status = "confirmed"
    booking.confirmed_at = datetime.now(UTC)
    booking.confirmed_price_eur = price_eur or booking.estimated_price_eur
    await db.flush()
    return booking


# Grille des frais d'annulation (COM-08) : palier (jours avant ETD) → quote-part.
#   > 30 j  → 0 %   ·   J-30 à J-7 → 25 %   ·   J-7 à J-2 → 50 %   ·   < J-2 → 100 %
_CANCELLATION_TIERS: tuple[tuple[int, Decimal], ...] = (
    (30, Decimal("0")),
    (7, Decimal("0.25")),
    (2, Decimal("0.50")),
    (0, Decimal("1.00")),
)


def cancellation_fee_rate(days_to_etd: int | None) -> Decimal:
    """Quote-part de frais d'annulation pour ``days_to_etd`` jours avant l'ETD."""
    if days_to_etd is None:
        return Decimal("0")
    for threshold, rate in _CANCELLATION_TIERS:
        if days_to_etd >= threshold:
            return rate
    return Decimal("1.00")


def compute_cancellation_fee(
    *,
    status: str,
    price_eur: Decimal | None,
    days_to_etd: int | None,
) -> Decimal:
    """Frais d'annulation (EUR) selon la grille COM-08.

    Aucun frais tant que la réservation n'est pas confirmée (annulation libre
    en ``draft``/``submitted``). Au-delà, application de la grille sur le prix
    confirmé (ou estimé à défaut).
    """
    if status not in ("confirmed", "loaded", "at_sea"):
        return Decimal("0")
    base = price_eur or Decimal("0")
    rate = cancellation_fee_rate(days_to_etd)
    return (base * rate).quantize(Decimal("0.01"))


def _days_to_etd(leg: Leg | None, on: datetime | None = None) -> int | None:
    if leg is None or leg.etd is None:
        return None
    now = on or datetime.now(UTC)
    return (leg.etd.date() - now.date()).days


async def cancel(db: AsyncSession, booking: Booking, reason: str) -> Booking:
    _assert_transition(booking.status, "cancelled")
    # Frais d'annulation figés au moment de l'annulation (grille COM-08).
    leg = await db.get(Leg, booking.leg_id)
    booking.cancellation_fee_eur = compute_cancellation_fee(
        status=booking.status,
        price_eur=booking.confirmed_price_eur or booking.estimated_price_eur,
        days_to_etd=_days_to_etd(leg),
    )
    booking.status = "cancelled"
    booking.cancelled_at = datetime.now(UTC)
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
        setattr(booking, field, datetime.now(UTC))
    await db.flush()
    # Effets de bord (notifications client, email, certificat Anemos). Import
    # tardif pour éviter tout cycle d'import au chargement du module.
    from app.services.booking_lifecycle import on_status_change

    await on_status_change(db, booking, target)
    return booking


async def list_for_client(db: AsyncSession, client_id: int, limit: int = 50) -> list[Booking]:
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
