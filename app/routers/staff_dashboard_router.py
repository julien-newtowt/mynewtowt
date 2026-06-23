"""Staff dashboard — landing for collaborators."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_staff
from app.config import settings
from app.database import get_db
from app.models.booking import Booking
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.ticket import Ticket
from app.models.vessel import Vessel
from app.services.notifications import list_for as list_notifications
from app.templating import templates

router = APIRouter(tags=["staff-dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user=Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    now = datetime.now(UTC)

    bookings_to_confirm = await db.scalar(
        select(func.count(Booking.id)).where(Booking.status == "submitted")
    )
    legs_upcoming = await db.scalar(select(func.count(Leg.id)).where(Leg.etd > now))
    tickets_p1 = await db.scalar(
        select(func.count(Ticket.id))
        .where(Ticket.priority == "P1")
        .where(Ticket.status.in_(("open", "in_progress", "pending_external")))
    )

    # Fleet positions for the map widget
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    last_positions: dict[int, VesselPosition | None] = {}
    for v in vessels:
        p = (
            await db.execute(
                select(VesselPosition)
                .where(VesselPosition.vessel_id == v.id)
                .order_by(VesselPosition.recorded_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        last_positions[v.id] = p

    # ADM-02 — alertes proactives (« qu'est-ce qui ne va pas aujourd'hui ? »).
    from app.services.dashboard_alerts import compute_alerts

    alerts = await compute_alerts(db, now.year)

    # Notifications dashboard
    notifications = await list_notifications(
        db,
        user_id=user.id,
        user_role=user.role,
        include_archived=False,
        limit=20,
    )

    # ADM-03 — KPI métier : CA prévisionnel, CO₂ évité, remplissage, prochains départs.
    from app.services.co2 import co2_equivalences
    from app.services.dashboard_kpis import (
        ca_previsionnel,
        fleet_kpis,
        upcoming_departures,
    )

    ca_forecast = await ca_previsionnel(db)
    fleet = await fleet_kpis(db, now)
    departures = await upcoming_departures(db, now, limit=8)
    co2_equiv = co2_equivalences(fleet["co2_avoided_kg"])

    return templates.TemplateResponse(
        "staff/dashboard.html",
        {
            "request": request,
            "user": user,
            "bookings_to_confirm": int(bookings_to_confirm or 0),
            "legs_upcoming": int(legs_upcoming or 0),
            "tickets_p1": int(tickets_p1 or 0),
            "vessels": vessels,
            "last_positions": last_positions,
            "maptiler_token": settings.map_token,
            "notifications": notifications,
            "notif_count": sum(1 for n in notifications if not n.is_read),
            "alerts": alerts,
            "alerts_danger": sum(1 for a in alerts if a["severity"] == "danger"),
            # ADM-03
            "ca_forecast": ca_forecast,
            "fleet_kpis": fleet,
            "co2_equiv": co2_equiv,
            "upcoming_departures": departures,
        },
    )
