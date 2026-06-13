"""Instrumentation du funnel commercial (COM-13).

Calcule, depuis les données existantes (devis + réservations), les métriques
clés de l'audit :
- volume de devis et **conversion devis → réservation** (lien ``Booking.quote_id``) ;
- entonnoir des réservations par statut + taux de conversion ``submitted → confirmed`` ;
- **délai submitted → confirmed** (médian) et **part confirmée sous 4 h** — la
  promesse produit ;
- **part self-service** (``channel = client``) vs back-office opérateur ;
- top routes par volume de réservations.

Aucun pixel ni tracking tiers : 100 % serveur, sans PII.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import Quote

# Statuts « engagés » d'une réservation (au-delà du panier draft).
_ENGAGED = ("submitted", "confirmed", "loaded", "at_sea", "discharged", "delivered")
_FUNNEL_ORDER = (
    "draft",
    "submitted",
    "confirmed",
    "loaded",
    "at_sea",
    "discharged",
    "delivered",
)
_CONFIRM_PROMISE_HOURS = 4.0


@dataclass
class FunnelReport:
    days: int
    since: datetime
    quotes_total: int = 0
    quotes_converted: int = 0  # devis ayant donné une réservation
    quote_to_booking_pct: float = 0.0
    bookings_by_status: dict[str, int] = field(default_factory=dict)
    submitted_to_confirmed_pct: float = 0.0
    confirm_delay_median_h: float | None = None
    confirm_under_promise_pct: float | None = None
    confirm_promise_hours: float = _CONFIRM_PROMISE_HOURS
    self_service_pct: float | None = None
    engaged_total: int = 0
    top_routes: list[dict] = field(default_factory=list)


async def commercial_funnel(db: AsyncSession, *, days: int = 90) -> FunnelReport:
    since = datetime.now(UTC) - timedelta(days=days)
    rep = FunnelReport(days=days, since=since)

    # --- Devis + conversion devis → booking -------------------------------
    rep.quotes_total = int(
        await db.scalar(
            select(func.count(Quote.id)).where(Quote.created_at >= since)
        )
        or 0
    )
    rep.quotes_converted = int(
        await db.scalar(
            select(func.count(func.distinct(Booking.quote_id))).where(
                Booking.quote_id.is_not(None), Booking.created_at >= since
            )
        )
        or 0
    )
    if rep.quotes_total:
        rep.quote_to_booking_pct = round(100 * rep.quotes_converted / rep.quotes_total, 1)

    # --- Entonnoir réservations par statut --------------------------------
    rows = (
        await db.execute(
            select(Booking.status, func.count(Booking.id).label("n"))
            .where(Booking.created_at >= since)
            .group_by(Booking.status)
        )
    ).all()
    by_status = dict.fromkeys((*_FUNNEL_ORDER, "cancelled"), 0)
    for r in rows:
        by_status[r.status] = r.n
    rep.bookings_by_status = by_status
    if by_status.get("submitted"):
        rep.submitted_to_confirmed_pct = round(
            100 * by_status.get("confirmed", 0) / by_status["submitted"], 1
        )

    # --- Délai submitted → confirmed + promesse 4 h -----------------------
    delay_rows = (
        await db.execute(
            select(Booking.submitted_at, Booking.confirmed_at).where(
                Booking.confirmed_at.is_not(None),
                Booking.submitted_at.is_not(None),
                Booking.confirmed_at >= since,
            )
        )
    ).all()
    delays_h = [
        (c - s).total_seconds() / 3600
        for s, c in delay_rows
        if c is not None and s is not None and c >= s
    ]
    if delays_h:
        rep.confirm_delay_median_h = round(statistics.median(delays_h), 1)
        under = sum(1 for d in delays_h if d <= _CONFIRM_PROMISE_HOURS)
        rep.confirm_under_promise_pct = round(100 * under / len(delays_h), 1)

    # --- Part self-service (canal client) ---------------------------------
    chan_rows = (
        await db.execute(
            select(Booking.channel, func.count(Booking.id).label("n"))
            .where(Booking.created_at >= since, Booking.status.in_(_ENGAGED))
            .group_by(Booking.channel)
        )
    ).all()
    engaged = {r.channel: r.n for r in chan_rows}
    rep.engaged_total = sum(engaged.values())
    if rep.engaged_total:
        rep.self_service_pct = round(100 * engaged.get("client", 0) / rep.engaged_total, 1)

    # --- Top routes par volume de réservations ----------------------------
    pol = Port.__table__.alias("pol")
    pod = Port.__table__.alias("pod")
    route_rows = (
        await db.execute(
            select(
                pol.c.locode.label("pol"),
                pod.c.locode.label("pod"),
                func.count(Booking.id).label("n"),
            )
            .select_from(
                Booking.__table__.join(Leg.__table__, Leg.id == Booking.leg_id)
                .join(pol, pol.c.id == Leg.departure_port_id)
                .join(pod, pod.c.id == Leg.arrival_port_id)
            )
            .where(Booking.created_at >= since)
            .group_by(pol.c.locode, pod.c.locode)
            .order_by(func.count(Booking.id).desc())
            .limit(8)
        )
    ).all()
    rep.top_routes = [{"pol": r.pol, "pod": r.pod, "count": r.n} for r in route_rows]

    return rep
