"""Planning service — create / update legs + cascade across the same vessel.

When an upstream leg's ETD or ETA shifts, all downstream legs that haven't
sailed yet (ATD null) are shifted by the same delta. This is the conservative
behaviour: it preserves the relative scheduling humans set, doesn't try to
guess transit times. Recompute-from-distance can come in V3.1.

Bookings of impacted legs do not need date updates themselves — they FK to
the leg, so reading ``booking.leg.etd`` reflects the new value. Notifications
to impacted clients are emitted by NotificationService (V3.1).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.planning_share import PlanningShare


class PlanningError(Exception):
    """Base planning error."""


class InvalidLegDates(PlanningError):
    pass


@dataclass(frozen=True)
class CascadeReport:
    leg_id: int
    delta: timedelta
    impacted_leg_ids: list[int]

    @property
    def delta_hours(self) -> float:
        return self.delta.total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_dates(etd: datetime, eta: datetime) -> None:
    if etd >= eta:
        raise InvalidLegDates("ETD must be strictly before ETA")
    if (eta - etd) > timedelta(days=180):
        raise InvalidLegDates("Leg duration cannot exceed 180 days")


def _leg_code_for(
    vessel_code: str,
    pol_country: str,
    pod_country: str,
    etd: datetime,
    sequence_letter: str = "A",
) -> str:
    """Generate leg_code per V2 convention: {SHIP}{LETTER}{POL}{POD}{YR}.

    Caller may pass sequence_letter='B','C',... for repeated trips in a year.
    """
    year_last_digit = str(etd.year)[-1]
    return (
        f"{vessel_code}{sequence_letter}"
        f"{pol_country.upper()[:2]}"
        f"{pod_country.upper()[:2]}"
        f"{year_last_digit}"
    )


# ---------------------------------------------------------------------------
# Create / Update / Delete
# ---------------------------------------------------------------------------


BOOKING_CLOSE_LEAD_HOURS = 48
"""Auto-cloture des réservations : ETD - 48h si le staff ne précise rien."""


async def create_leg(
    db: AsyncSession,
    *,
    vessel_id: int,
    departure_port_id: int,
    arrival_port_id: int,
    etd: datetime,
    eta: datetime,
    is_bookable: bool = False,
    public_capacity_palettes: int | None = None,
    public_price_per_palette_eur: Decimal | None = None,
    booking_close_at: datetime | None = None,
    leg_code: str | None = None,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    port_stay_planned_hours: int | None = None,
) -> Leg:
    validate_dates(etd, eta)
    if departure_port_id == arrival_port_id:
        raise InvalidLegDates("Departure and arrival ports must differ")

    # Auto-cloture des réservations à ETD - 48h si non précisé par le staff.
    if booking_close_at is None:
        booking_close_at = etd - timedelta(hours=BOOKING_CLOSE_LEAD_HOURS)

    # If leg_code not supplied, derive one (best-effort; admin can edit).
    if leg_code is None:
        # Need vessel.code + port countries — do a small load.
        from app.models.port import Port
        from app.models.vessel import Vessel

        vessel = await db.get(Vessel, vessel_id)
        pol = await db.get(Port, departure_port_id)
        pod = await db.get(Port, arrival_port_id)
        if not (vessel and pol and pod):
            raise PlanningError("Invalid vessel/port references")
        leg_code = _leg_code_for(vessel.code, pol.country, pod.country, etd)
        # Bump letter A/B/C if code already exists.
        for letter in "ABCDEFGHIJ":
            candidate = _leg_code_for(vessel.code, pol.country, pod.country, etd, letter)
            existing = (
                await db.execute(select(Leg).where(Leg.leg_code == candidate))
            ).scalar_one_or_none()
            if not existing:
                leg_code = candidate
                break

    leg = Leg(
        leg_code=leg_code,
        vessel_id=vessel_id,
        departure_port_id=departure_port_id,
        arrival_port_id=arrival_port_id,
        etd_ref=etd,
        eta_ref=eta,
        etd=etd,
        eta=eta,
        status="planned",
        is_bookable=is_bookable,
        public_capacity_palettes=public_capacity_palettes,
        public_price_per_palette_eur=public_price_per_palette_eur,
        booking_close_at=booking_close_at,
        transit_speed_kn=transit_speed_kn,
        elongation_coef=elongation_coef,
        port_stay_planned_hours=port_stay_planned_hours,
    )
    db.add(leg)
    await db.flush()
    return leg


async def update_leg(
    db: AsyncSession,
    leg: Leg,
    *,
    vessel_id: int | None = None,
    etd: datetime | None = None,
    eta: datetime | None = None,
    departure_port_id: int | None = None,
    arrival_port_id: int | None = None,
    is_bookable: bool | None = None,
    public_capacity_palettes: int | None = None,
    public_price_per_palette_eur: Decimal | None = None,
    booking_close_at: datetime | None = None,
    transit_speed_kn: float | None = None,
    elongation_coef: float | None = None,
    port_stay_planned_hours: int | None = None,
    cascade: bool = True,
) -> CascadeReport | None:
    """Update a leg in place. If etd shifts and cascade=True, propagate the
    delta to all downstream legs of the same vessel that haven't sailed yet.

    When vessel_id, departure_port_id, arrival_port_id ou etd's year change,
    the leg_code is recomputed (format {SHIP}{LETTER}{POL}{POD}{YR}) avec
    une lettre de séquence (A→J) unique pour éviter les collisions.

    Returns the CascadeReport, ou None si aucune cascade n'a été effectuée.
    """
    from app.models.port import Port
    from app.models.vessel import Vessel

    new_etd = etd or leg.etd
    new_eta = eta or leg.eta
    validate_dates(new_etd, new_eta)

    delta = new_etd - leg.etd
    # Capture old reference points BEFORE applying changes
    old_vessel_id = leg.vessel_id
    old_pol_id = leg.departure_port_id
    old_pod_id = leg.arrival_port_id
    old_year_digit = str(leg.etd.year)[-1]

    leg.etd = new_etd
    leg.eta = new_eta

    if vessel_id is not None:
        leg.vessel_id = vessel_id
    if departure_port_id is not None:
        leg.departure_port_id = departure_port_id
    if arrival_port_id is not None:
        leg.arrival_port_id = arrival_port_id
    if leg.departure_port_id == leg.arrival_port_id:
        raise InvalidLegDates("Departure and arrival ports must differ")
    if is_bookable is not None:
        leg.is_bookable = is_bookable
    if public_capacity_palettes is not None:
        leg.public_capacity_palettes = public_capacity_palettes
    if public_price_per_palette_eur is not None:
        leg.public_price_per_palette_eur = public_price_per_palette_eur
    if booking_close_at is not None:
        leg.booking_close_at = booking_close_at
    elif etd is not None and leg.booking_close_at is None:
        # ETD modifiée + pas de clôture définie -> auto ETD-48h
        leg.booking_close_at = new_etd - timedelta(hours=BOOKING_CLOSE_LEAD_HOURS)
    if transit_speed_kn is not None:
        leg.transit_speed_kn = transit_speed_kn
    if elongation_coef is not None:
        leg.elongation_coef = elongation_coef
    if port_stay_planned_hours is not None:
        leg.port_stay_planned_hours = port_stay_planned_hours

    # Recompute leg_code si l'une de ses entrées a changé
    if (
        leg.vessel_id != old_vessel_id
        or leg.departure_port_id != old_pol_id
        or leg.arrival_port_id != old_pod_id
        or str(leg.etd.year)[-1] != old_year_digit
    ):
        vessel = await db.get(Vessel, leg.vessel_id)
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        if vessel and pol and pod:
            for letter in "ABCDEFGHIJ":
                candidate = _leg_code_for(
                    vessel.code, pol.country, pod.country, leg.etd, letter,
                )
                if candidate == leg.leg_code:
                    break  # Déjà unique avec cette lettre — on garde
                existing = (
                    await db.execute(
                        select(Leg)
                        .where(Leg.leg_code == candidate)
                        .where(Leg.id != leg.id)
                    )
                ).scalar_one_or_none()
                if not existing:
                    leg.leg_code = candidate
                    break

    if not cascade or delta == timedelta(0):
        await db.flush()
        return None

    # Cascade : all later legs of the same vessel that haven't sailed yet
    stmt = (
        select(Leg)
        .where(Leg.vessel_id == leg.vessel_id)
        .where(Leg.id != leg.id)
        .where(Leg.etd > leg.etd_ref)        # downstream relative to original ETD
        .where(Leg.atd.is_(None))            # hasn't actually sailed
        .order_by(Leg.etd.asc())
    )
    downstream = list((await db.execute(stmt)).scalars().all())
    impacted_ids: list[int] = []
    for dn in downstream:
        dn.etd = dn.etd + delta
        dn.eta = dn.eta + delta
        if dn.booking_close_at:
            dn.booking_close_at = dn.booking_close_at + delta
        impacted_ids.append(dn.id)

    await db.flush()
    return CascadeReport(leg_id=leg.id, delta=delta, impacted_leg_ids=impacted_ids)


async def delete_leg(db: AsyncSession, leg: Leg) -> None:
    """Delete a leg. Refuses if it has bookings (data integrity)."""
    from app.models.booking import Booking

    has_bookings = await db.scalar(
        select(Booking.id).where(Booking.leg_id == leg.id).limit(1)
    )
    if has_bookings:
        raise PlanningError(
            f"Cannot delete leg {leg.leg_code}: has bookings (cancel them first)"
        )
    await db.delete(leg)
    await db.flush()


# ---------------------------------------------------------------------------
# Queries for Gantt views
# ---------------------------------------------------------------------------


async def list_legs_in_window(
    db: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    vessel_id: int | None = None,
    status: str | None = None,
) -> list[Leg]:
    stmt = select(Leg).order_by(Leg.etd.asc())
    if date_from is not None:
        stmt = stmt.where(Leg.eta >= date_from)
    if date_to is not None:
        stmt = stmt.where(Leg.etd <= date_to)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    if status:
        stmt = stmt.where(Leg.status == status)
    return list((await db.execute(stmt)).scalars().all())


def detect_port_conflicts(legs: Sequence[Leg]) -> list[tuple[int, int]]:
    """Return pairs (leg_id_a, leg_id_b) where two vessels overlap at the same port.

    Conflict criterion: same arrival_port_id, time windows overlap by ≥ 12h.
    """
    conflicts: list[tuple[int, int]] = []
    items = list(legs)
    for i in range(len(items)):
        a = items[i]
        for j in range(i + 1, len(items)):
            b = items[j]
            if a.arrival_port_id != b.arrival_port_id:
                continue
            if a.vessel_id == b.vessel_id:
                continue
            # overlap on arrival window (eta ± 24h)
            window = timedelta(hours=12)
            if abs((a.eta - b.eta).total_seconds()) < window.total_seconds():
                conflicts.append((a.id, b.id))
    return conflicts


# ---------------------------------------------------------------------------
# Public share
# ---------------------------------------------------------------------------


def _new_share_token() -> str:
    return secrets.token_urlsafe(24)


async def create_share(
    db: AsyncSession,
    *,
    label: str | None,
    vessel_id: int | None,
    only_bookable: bool,
    description: str | None,
    expires_at: datetime | None,
    created_by_id: int | None,
) -> PlanningShare:
    share = PlanningShare(
        token=_new_share_token(),
        label=label,
        vessel_id=vessel_id,
        only_bookable=only_bookable,
        description=description,
        expires_at=expires_at,
        created_by_id=created_by_id,
        is_active=True,
    )
    db.add(share)
    await db.flush()
    return share


async def lookup_share(db: AsyncSession, token: str) -> PlanningShare | None:
    share = (
        await db.execute(select(PlanningShare).where(PlanningShare.token == token))
    ).scalar_one_or_none()
    if not share or not share.is_active:
        return None
    if share.expires_at and share.expires_at < datetime.now(timezone.utc):
        return None
    return share


async def list_shares(db: AsyncSession) -> list[PlanningShare]:
    stmt = select(PlanningShare).order_by(PlanningShare.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def revoke_share(db: AsyncSession, share: PlanningShare) -> None:
    share.is_active = False
    await db.flush()
