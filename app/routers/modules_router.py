"""Unified ERP modules — wide-and-minimal V3.0 implementations.

Each module gets:
- A landing page with real data from the DB (instead of the V3.0 stub).
- One or two creation endpoints (form-based, classic SSR).
- Read-only views for the rest.

Each module is kept in a single route block here to land them all at
once. They can be promoted to dedicated routers (own service + tests)
in V3.1 sprints.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.claim import Claim, ClaimTimelineEntry, VesselPosition
from app.models.crew import (
    CrewAssignment, CrewCertification, CrewLeave, CrewMember,
)
from app.models.escale import DockerShift, EscaleOperation
from app.models.finance import LegFinance, OpexParameter
from app.models.leg import Leg
from app.models.mrv import MRVEvent, MRVParameter
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.permissions import require_permission
from app.services import weather as wx
from app.services.activity import record as activity_record
from app.services.vessel_position import get_latest_position
from app.templating import templates

router = APIRouter(tags=["modules"])


# ────────────────────────────────────────────────────────────────────
#                            ONBOARD 4 espaces
# ────────────────────────────────────────────────────────────────────


@router.get("/onboard", response_class=HTMLResponse)
async def onboard_landing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    now = datetime.now(timezone.utc)
    active_legs = list((await db.execute(
        select(Leg).where(Leg.atd.is_not(None)).where(Leg.ata.is_(None))
        .order_by(Leg.etd.desc())
    )).scalars().all())
    next_etd = (await db.execute(
        select(Leg).where(Leg.etd > now).order_by(Leg.etd.asc()).limit(1)
    )).scalar_one_or_none()
    return templates.TemplateResponse(
        "staff/onboard/landing.html",
        {"request": request, "user": user, "active_legs": active_legs, "next_etd": next_etd},
    )


@router.get("/onboard/navigation", response_class=HTMLResponse)
async def onboard_navigation(
    request: Request,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    # Filtre RBAC : si l'user est rattaché à un navire (assigned_vessel_id),
    # on ne lui montre que les legs de ce navire.
    legs_stmt = select(Leg).order_by(Leg.etd.desc()).limit(30)
    if getattr(user, "assigned_vessel_id", None):
        legs_stmt = (
            select(Leg).where(Leg.vessel_id == user.assigned_vessel_id)
            .order_by(Leg.etd.desc()).limit(30)
        )
    legs = list((await db.execute(legs_stmt)).scalars().all())
    selected = (await db.get(Leg, leg_id)) if leg_id else (legs[0] if legs else None)
    noon_reports = []
    watch_logs = []
    latest_position = None
    weather_now = None
    if selected:
        noon_reports = list((await db.execute(
            select(NoonReport).where(NoonReport.leg_id == selected.id)
            .order_by(NoonReport.recorded_at.desc()).limit(30)
        )).scalars().all())
        watch_logs = list((await db.execute(
            select(WatchLog).where(WatchLog.leg_id == selected.id)
            .order_by(WatchLog.watch_date.desc(), WatchLog.watch_period.desc()).limit(30)
        )).scalars().all())
        # Pré-remplissage GPS — dernière position satcom < 6h
        latest_position = await get_latest_position(db, selected.vessel_id)
        # Pré-remplissage météo au point GPS courant (vent + houle)
        if latest_position:
            try:
                weather_now = await wx.fetch_current(
                    latest_position.latitude, latest_position.longitude,
                )
            except Exception:
                weather_now = None
    return templates.TemplateResponse(
        "staff/onboard/navigation.html",
        {"request": request, "user": user, "legs": legs, "leg": selected,
         "noon_reports": noon_reports, "watch_logs": watch_logs,
         "latest_position": latest_position,
         "weather_now": weather_now},
    )


@router.post("/onboard/navigation/noon-report")
async def post_noon_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    nr = NoonReport(
        leg_id=int(f["leg_id"]),
        recorded_at=datetime.now(timezone.utc),
        latitude=float(f["latitude"]),
        longitude=float(f["longitude"]),
        sog_avg=_maybe_float(f.get("sog_avg")),
        cog_avg=_maybe_float(f.get("cog_avg")),
        wind_speed_kn=_maybe_float(f.get("wind_speed_kn")),
        wind_direction_deg=_maybe_float(f.get("wind_direction_deg")),
        sea_state_bf=_maybe_int(f.get("sea_state_bf")),
        visibility_nm=_maybe_float(f.get("visibility_nm")),
        barometric_hpa=_maybe_float(f.get("barometric_hpa")),
        fuel_consumed_24h_l=_maybe_float(f.get("fuel_consumed_24h_l")),
        distance_24h_nm=_maybe_float(f.get("distance_24h_nm")),
        rob_fuel_l=_maybe_float(f.get("rob_fuel_l")),
        remarks=f.get("remarks") or None,
        recorded_by_id=user.id,
    )
    db.add(nr)
    await db.flush()
    await activity_record(db, action="noon_report_create", user_id=user.id, user_name=user.username,
                          module="captain", entity_type="noon_report", entity_id=nr.id)
    return RedirectResponse(url=f"/onboard/navigation?leg_id={nr.leg_id}", status_code=303)


@router.post("/onboard/navigation/watch-log")
async def post_watch_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    wl = WatchLog(
        leg_id=int(f["leg_id"]),
        watch_date=date.fromisoformat(f["watch_date"]),
        watch_period=f["watch_period"],
        officer_on_watch=f.get("officer_on_watch") or user.username,
        officer_id=user.id,
        entry=f["entry"],
        weather_summary=f.get("weather_summary") or None,
    )
    db.add(wl)
    await db.flush()
    return RedirectResponse(url=f"/onboard/navigation?leg_id={wl.leg_id}", status_code=303)


@router.get("/onboard/escale", response_class=HTMLResponse)
async def onboard_escale(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    now = datetime.now(timezone.utc)
    at_quay = list((await db.execute(
        select(Leg).where(Leg.ata.is_not(None)).where(Leg.atd.is_(None))
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/onboard/escale.html",
        {"request": request, "user": user, "at_quay": at_quay},
    )


@router.get("/onboard/cargo", response_class=HTMLResponse)
async def onboard_cargo(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(20))).scalars().all())
    return templates.TemplateResponse(
        "staff/onboard/cargo.html",
        {"request": request, "user": user, "legs": legs},
    )


@router.get("/onboard/crew", response_class=HTMLResponse)
async def onboard_crew(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(20))).scalars().all())
    visitors_today = list((await db.execute(
        select(VisitorLog).order_by(VisitorLog.time_in.desc()).limit(20)
    )).scalars().all())
    checklists = list((await db.execute(
        select(OnboardChecklist).order_by(OnboardChecklist.created_at.desc()).limit(20)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/onboard/crew.html",
        {"request": request, "user": user, "legs": legs,
         "visitors": visitors_today, "checklists": checklists},
    )


@router.post("/onboard/crew/visitor")
async def post_visitor(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    v = VisitorLog(
        leg_id=int(f["leg_id"]),
        full_name=f["full_name"],
        company=f.get("company") or None,
        purpose=f.get("purpose") or None,
        id_document=f.get("id_document") or None,
        time_in=datetime.now(timezone.utc),
        escorted_by=f.get("escorted_by") or None,
        notes=f.get("notes") or None,
    )
    db.add(v)
    await db.flush()
    return RedirectResponse(url="/onboard/crew", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                                CREW
# ────────────────────────────────────────────────────────────────────


# NOTE V3.1 — Les routes /crew, /crew/new (GET+POST) ont été retirées
# d'ici : le routeur dédié ``crew_router`` (monté plus haut dans main.py)
# les sert. Les conserver ici créait du code mort + risque de drift entre
# deux implémentations divergentes.


# ────────────────────────────────────────────────────────────────────
#                                  RH
# ────────────────────────────────────────────────────────────────────


@router.get("/rh", response_class=HTMLResponse)
async def rh_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    members = list((await db.execute(
        select(CrewMember).where(CrewMember.is_active.is_(True))
    )).scalars().all())
    leaves = list((await db.execute(
        select(CrewLeave).order_by(CrewLeave.created_at.desc()).limit(50)
    )).scalars().all())
    pending = [l for l in leaves if l.status == "requested"]
    return templates.TemplateResponse(
        "staff/rh/index.html",
        {"request": request, "user": user, "members": members,
         "leaves": leaves, "pending": pending},
    )


@router.post("/rh/leave")
async def rh_create_leave(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    f = await request.form()
    l = CrewLeave(
        crew_member_id=int(f["crew_member_id"]),
        kind=f["kind"],
        start_date=date.fromisoformat(f["start_date"]),
        end_date=date.fromisoformat(f["end_date"]),
        status="requested",
        reason=f.get("reason") or None,
    )
    db.add(l)
    await db.flush()
    return RedirectResponse(url="/rh", status_code=303)


@router.post("/rh/leave/{leave_id}/decide")
async def rh_decide_leave(
    leave_id: int,
    decision: str = Form(...),  # 'approved' | 'rejected'
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    l = await db.get(CrewLeave, leave_id)
    if not l:
        raise HTTPException(status_code=404, detail="Not found")
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"decision must be 'approved' or 'rejected', got {decision!r}",
        )
    l.status = decision
    l.decided_by_id = user.id
    l.decided_at = datetime.now(timezone.utc)
    await db.flush()
    return RedirectResponse(url="/rh", status_code=303)


# NOTE V3.1 — Les routes /escale, /escale/{leg_id}, /escale/{leg_id}/operation
# ont été retirées : le routeur dédié ``escale_router`` les sert.


# NOTE V3.2 — Routes /finance/* retirées : ``finance_router`` les sert désormais.

# NOTE V3.1 — Routes /kpi, /mrv et /claims, /claims/new (GET+POST) retirées :
# ``mrv_router`` et ``claims_router`` les servent désormais.


# ────────────────────────────────────────────────────────────────────
#                               TRACKING
# ────────────────────────────────────────────────────────────────────


@router.get("/tracking", response_class=HTMLResponse)
async def tracking_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    last_positions = {}
    for v in vessels:
        p = (await db.execute(
            select(VesselPosition).where(VesselPosition.vessel_id == v.id)
            .order_by(VesselPosition.recorded_at.desc()).limit(1)
        )).scalar_one_or_none()
        last_positions[v.id] = p
    from app.config import settings as _settings
    return templates.TemplateResponse(
        "staff/tracking/index.html",
        {"request": request, "user": user,
         "vessels": vessels, "last_positions": last_positions,
         "maptiler_token": _settings.map_token},
    )


# ────────────────────────────────────────────────────────────────────
#                             ANALYTICS
# ────────────────────────────────────────────────────────────────────


@router.get("/dashboard/analytics", response_class=HTMLResponse)
async def analytics_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("analytics", "C")),
) -> HTMLResponse:
    # Aggregate stats across modules
    from app.models.booking import Booking
    from app.models.client_account import ClientAccount
    from app.models.ticket import Ticket

    bookings_total = await db.scalar(select(func.count(Booking.id)))
    bookings_confirmed = await db.scalar(select(func.count(Booking.id)).where(Booking.status == "confirmed"))
    clients_total = await db.scalar(select(func.count(ClientAccount.id)))
    tickets_active = await db.scalar(
        select(func.count(Ticket.id)).where(Ticket.status.in_(("open", "in_progress", "pending_external")))
    )
    legs_bookable = await db.scalar(select(func.count(Leg.id)).where(Leg.is_bookable.is_(True)))

    return templates.TemplateResponse(
        "staff/analytics/index.html",
        {
            "request": request, "user": user,
            "bookings_total": bookings_total or 0,
            "bookings_confirmed": bookings_confirmed or 0,
            "clients_total": clients_total or 0,
            "tickets_active": tickets_active or 0,
            "legs_bookable": legs_bookable or 0,
        },
    )


@router.get("/dashboard/analytics/executive", response_class=HTMLResponse)
async def analytics_executive(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("analytics", "C")),
) -> HTMLResponse:
    from app.models.booking import Booking
    from app.models.client_account import ClientAccount
    from app.models.client_invoice import ClientInvoice
    from app.models.finance import LegKPI

    now = datetime.now(timezone.utc)
    year = now.year
    year_start = datetime(year, 1, 1, tzinfo=timezone.utc)
    prev_year_start = datetime(year - 1, 1, 1, tzinfo=timezone.utc)
    prev_year_end = datetime(year - 1, 12, 31, 23, 59, tzinfo=timezone.utc)

    # Legs by status (current year)
    legs_all = list((await db.execute(
        select(Leg).where(Leg.etd >= year_start)
    )).scalars().all())
    legs_by_status: dict[str, int] = {}
    for leg in legs_all:
        legs_by_status[leg.status] = legs_by_status.get(leg.status, 0) + 1

    # KPI totals (current year)
    kpis = list((await db.execute(
        select(LegKPI).join(Leg, Leg.id == LegKPI.leg_id).where(Leg.etd >= year_start)
    )).scalars().all())
    total_tonnage_t = sum(float(k.tonnage_kg) / 1000 for k in kpis)
    total_co2_avoided_kg = sum(float(k.co2_avoided_kg or 0) for k in kpis)
    on_time_count = sum(1 for k in kpis if k.on_time)
    on_time_pct = round(on_time_count / len(kpis) * 100) if kpis else 0

    # KPI totals (previous year for N-1 comparison)
    kpis_prev = list((await db.execute(
        select(LegKPI).join(Leg, Leg.id == LegKPI.leg_id).where(
            Leg.etd >= prev_year_start, Leg.etd <= prev_year_end
        )
    )).scalars().all())
    prev_tonnage_t = sum(float(k.tonnage_kg) / 1000 for k in kpis_prev)
    prev_co2_kg = sum(float(k.co2_avoided_kg or 0) for k in kpis_prev)

    # Revenue (invoices issued this year)
    revenue = await db.scalar(
        select(func.sum(ClientInvoice.amount_incl_vat_eur)).where(
            ClientInvoice.issued_at >= year_start
        )
    ) or 0
    prev_revenue = await db.scalar(
        select(func.sum(ClientInvoice.amount_incl_vat_eur)).where(
            ClientInvoice.issued_at >= prev_year_start,
            ClientInvoice.issued_at <= prev_year_end,
        )
    ) or 0

    clients_total = await db.scalar(select(func.count(ClientAccount.id))) or 0
    bookings_total = await db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= year_start)) or 0

    return templates.TemplateResponse(
        "staff/analytics/executive.html",
        {
            "request": request, "user": user, "year": year,
            "legs_by_status": legs_by_status,
            "legs_total": len(legs_all),
            "total_tonnage_t": round(total_tonnage_t, 1),
            "total_co2_avoided_kg": round(total_co2_avoided_kg),
            "on_time_pct": on_time_pct,
            "revenue": float(revenue),
            "clients_total": clients_total,
            "bookings_total": bookings_total,
            "prev_tonnage_t": round(prev_tonnage_t, 1),
            "prev_co2_kg": round(prev_co2_kg),
            "prev_revenue": float(prev_revenue),
            "year_prev": year - 1,
        },
    )


@router.get("/dashboard/analytics/commercial", response_class=HTMLResponse)
async def analytics_commercial(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("analytics", "C")),
) -> HTMLResponse:
    from app.models.booking import Booking
    from app.models.client_account import ClientAccount
    from app.models.client_invoice import ClientInvoice

    now = datetime.now(timezone.utc)
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)

    # Funnel: bookings par statut
    funnel_statuses = ["draft", "submitted", "confirmed", "loaded", "at_sea", "discharged", "delivered", "cancelled"]
    funnel_rows = (await db.execute(
        select(Booking.status, func.count(Booking.id).label("n"))
        .where(Booking.created_at >= year_start)
        .group_by(Booking.status)
    )).all()
    funnel: dict[str, int] = {s: 0 for s in funnel_statuses}
    for row in funnel_rows:
        if row.status in funnel:
            funnel[row.status] = row.n
    funnel_max = max(funnel.values()) or 1

    # Top clients by booking count
    top_clients_rows = (await db.execute(
        select(ClientAccount.company_name, func.count(Booking.id).label("n"))
        .join(Booking, Booking.client_account_id == ClientAccount.id)
        .where(Booking.created_at >= year_start)
        .group_by(ClientAccount.id, ClientAccount.company_name)
        .order_by(func.count(Booking.id).desc())
        .limit(8)
    )).all()

    # Invoices by status
    inv_rows = (await db.execute(
        select(ClientInvoice.status, func.count(ClientInvoice.id).label("n"),
               func.sum(ClientInvoice.amount_incl_vat_eur).label("total"))
        .where(ClientInvoice.issued_at >= year_start)
        .group_by(ClientInvoice.status)
    )).all()
    inv_by_status = {r.status: {"count": r.n, "total": float(r.total or 0)} for r in inv_rows}

    total_revenue = sum(v["total"] for v in inv_by_status.values())
    conversion_pct = (
        round(funnel["confirmed"] / funnel["submitted"] * 100)
        if funnel["submitted"] else 0
    )

    return templates.TemplateResponse(
        "staff/analytics/commercial.html",
        {
            "request": request, "user": user, "year": now.year,
            "funnel": funnel, "funnel_statuses": funnel_statuses, "funnel_max": funnel_max,
            "top_clients": top_clients_rows,
            "inv_by_status": inv_by_status,
            "total_revenue": total_revenue,
            "conversion_pct": conversion_pct,
        },
    )


@router.get("/dashboard/analytics/operations", response_class=HTMLResponse)
async def analytics_operations(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("analytics", "C")),
) -> HTMLResponse:
    from app.models.ticket import Ticket

    now = datetime.now(timezone.utc)
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)

    # Tickets: totals + SLA
    all_tickets = list((await db.execute(
        select(Ticket).where(Ticket.created_at >= year_start).order_by(Ticket.created_at.desc())
    )).scalars().all())

    by_priority: dict[str, dict] = {
        "P1": {"total": 0, "breached": 0, "open": 0},
        "P2": {"total": 0, "breached": 0, "open": 0},
        "P3": {"total": 0, "breached": 0, "open": 0},
    }
    closed_statuses = {"resolved", "closed"}
    for t in all_tickets:
        p = t.priority
        if p in by_priority:
            by_priority[p]["total"] += 1
            if t.sla_breached:
                by_priority[p]["breached"] += 1
            if t.status not in closed_statuses:
                by_priority[p]["open"] += 1

    # Active legs (inprogress)
    active_legs = list((await db.execute(
        select(Leg, Vessel).join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.status == "inprogress")
        .order_by(Leg.etd.asc())
    )).all())

    # Recent tickets (last 10 open)
    recent_tickets = [t for t in all_tickets if t.status not in closed_statuses][:10]

    total_breached = sum(p["breached"] for p in by_priority.values())
    total_open = sum(p["open"] for p in by_priority.values())

    return templates.TemplateResponse(
        "staff/analytics/operations.html",
        {
            "request": request, "user": user, "year": now.year,
            "by_priority": by_priority,
            "total_breached": total_breached,
            "total_open": total_open,
            "active_legs": active_legs,
            "recent_tickets": recent_tickets,
        },
    )


# ────────────────────────────────────────────────────────────────────
#                                ADMIN
# ────────────────────────────────────────────────────────────────────


@router.get("/admin", response_class=HTMLResponse)
async def admin_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    users = list((await db.execute(select(User).order_by(User.created_at.desc()).limit(50))).scalars().all())
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    # Aggregate port counts for the admin overview block
    total_ports = await db.scalar(select(func.count(Port.id)))
    active_ports = await db.scalar(select(func.count(Port.id)).where(Port.is_active.is_(True)))
    return templates.TemplateResponse(
        "staff/admin/index.html",
        {"request": request, "user": user, "users": users,
         "vessels": vessels,
         "total_ports": total_ports or 0, "active_ports": active_ports or 0},
    )


# ────────────────────────────────────────────────────────────────────
#                              ADMIN — PORTS
# ────────────────────────────────────────────────────────────────────


@router.get("/admin/ports", response_class=HTMLResponse)
async def admin_ports(
    request: Request,
    q: str | None = None,
    country: str | None = None,
    source: str | None = None,
    show: str = "all",      # 'all' | 'active' | 'inactive'
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    per_page = 50
    stmt = select(Port)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            (func.lower(Port.name).like(like)) | (func.lower(Port.locode).like(like))
        )
    if country:
        stmt = stmt.where(Port.country == country.upper())
    if source:
        stmt = stmt.where(Port.source == source)
    if show == "active":
        stmt = stmt.where(Port.is_active.is_(True))
    elif show == "inactive":
        stmt = stmt.where(Port.is_active.is_(False))
    total = (await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    stmt = stmt.order_by(Port.country, Port.locode).limit(per_page).offset((page - 1) * per_page)
    ports = list((await db.execute(stmt)).scalars().all())

    return templates.TemplateResponse(
        "staff/admin/ports.html",
        {
            "request": request, "user": user,
            "ports": ports, "page": page, "per_page": per_page,
            "total": total,
            "filters": {"q": q or "", "country": country or "", "source": source or "", "show": show},
        },
    )


@router.post("/admin/ports/{port_id}/toggle")
async def admin_port_toggle(
    port_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> RedirectResponse:
    port = await db.get(Port, port_id)
    if not port:
        raise HTTPException(status_code=404, detail="Port not found")
    port.is_active = not port.is_active
    await activity_record(
        db, action="port_toggle",
        user_id=user.id, user_name=user.username, user_role=user.role,
        module="admin", entity_type="port", entity_id=port.id,
        entity_label=port.locode,
        detail=f"is_active={port.is_active}",
    )
    return RedirectResponse(url=request_ports_back_url(), status_code=303)


def request_ports_back_url() -> str:
    return "/admin/ports"


# ───────────────────────── PortConfig (contacts agent / pilote / docs) ─────────


@router.get("/admin/ports/{port_id}/config", response_class=HTMLResponse)
async def admin_port_config_form(
    port_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    """Form d'édition des contacts portuaires + docs requis + restrictions."""
    from app.models.finance import PortConfig
    port = await db.get(Port, port_id)
    if not port:
        raise HTTPException(status_code=404, detail="Port not found")
    config = (await db.execute(
        select(PortConfig).where(PortConfig.port_id == port_id)
    )).scalar_one_or_none()
    return templates.TemplateResponse(
        "staff/admin/port_config.html",
        {"request": request, "user": user, "port": port, "config": config},
    )


@router.post("/admin/ports/{port_id}/config")
async def admin_port_config_save(
    port_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> RedirectResponse:
    from app.models.finance import PortConfig
    port = await db.get(Port, port_id)
    if not port:
        raise HTTPException(status_code=404, detail="Port not found")
    form = await request.form()
    config = (await db.execute(
        select(PortConfig).where(PortConfig.port_id == port_id)
    )).scalar_one_or_none()
    is_create = config is None
    if config is None:
        config = PortConfig(port_id=port_id)
        db.add(config)

    def _opt(field: str) -> str | None:
        v = (form.get(field) or "").strip()
        return v or None

    def _dec(field: str):
        v = (form.get(field) or "").strip()
        if not v:
            return None
        try:
            return Decimal(v.replace(",", "."))
        except (ValueError, ArithmeticError):
            return None

    config.agent_name = _opt("agent_name")
    config.agent_phone = _opt("agent_phone")
    config.agent_email = _opt("agent_email")
    # Communications VHF / téléphone pilote retirées du form (V3.6) —
    # on ne touche plus ces colonnes (données existantes préservées).
    config.documents_required = _opt("documents_required")
    config.restrictions = _opt("restrictions")
    config.notes_for_captain = _opt("notes_for_captain")
    # Fees
    config.agency_fee_eur = _dec("agency_fee_eur")
    config.pilot_fee_eur = _dec("pilot_fee_eur")
    config.berth_fee_per_day_eur = _dec("berth_fee_per_day_eur")
    config.docker_fee_per_palette_eur = _dec("docker_fee_per_palette_eur")
    config.notes = _opt("notes")

    await db.flush()
    await activity_record(
        db, action="create" if is_create else "update",
        user_id=user.id, user_name=user.username, user_role=user.role,
        module="admin", entity_type="port_config",
        entity_id=config.id, entity_label=port.locode,
    )
    return RedirectResponse(url=f"/admin/ports/{port_id}/config", status_code=303)


@router.post("/admin/ports/upload")
async def admin_ports_upload(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> RedirectResponse:
    """Upload a CSV of ports — same format as parsed by parse_csv()."""
    from app.services.ports import parse_csv, upsert_ports

    form = await request.form()
    f = form.get("file")
    source = (form.get("source") or "user").strip()
    if f is None or not hasattr(f, "read"):
        raise HTTPException(status_code=400, detail="No file uploaded")
    content = await f.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")
    rows = parse_csv(content, source=source)
    ins, upd = await upsert_ports(db, rows)
    await activity_record(
        db, action="ports_upload",
        user_id=user.id, user_name=user.username, user_role=user.role,
        module="admin", entity_type="port_batch",
        detail=f"source={source} parsed={len(rows)} inserted={ins} updated={upd}",
    )
    return RedirectResponse(url=f"/admin/ports?show=all", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                              Helpers
# ────────────────────────────────────────────────────────────────────


def _maybe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
