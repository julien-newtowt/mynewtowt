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
from app.models.finance import LegFinance, LegKPI, OpexParameter
from app.models.leg import Leg
from app.models.mrv import MRVEvent, MRVParameter
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.permissions import require_permission
from app.services.activity import record as activity_record
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
    legs = list((await db.execute(
        select(Leg).order_by(Leg.etd.desc()).limit(30)
    )).scalars().all())
    selected = (await db.get(Leg, leg_id)) if leg_id else (legs[0] if legs else None)
    noon_reports = []
    watch_logs = []
    if selected:
        noon_reports = list((await db.execute(
            select(NoonReport).where(NoonReport.leg_id == selected.id)
            .order_by(NoonReport.recorded_at.desc()).limit(30)
        )).scalars().all())
        watch_logs = list((await db.execute(
            select(WatchLog).where(WatchLog.leg_id == selected.id)
            .order_by(WatchLog.watch_date.desc(), WatchLog.watch_period.desc()).limit(30)
        )).scalars().all())
    return templates.TemplateResponse(
        "staff/onboard/navigation.html",
        {"request": request, "user": user, "legs": legs, "leg": selected,
         "noon_reports": noon_reports, "watch_logs": watch_logs},
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


@router.get("/crew", response_class=HTMLResponse)
async def crew_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "C")),
) -> HTMLResponse:
    members = list((await db.execute(
        select(CrewMember).where(CrewMember.is_active.is_(True)).order_by(CrewMember.full_name)
    )).scalars().all())
    schengen_warning = [m for m in members if m.schengen_status in ("warning", "non_compliant")]
    return templates.TemplateResponse(
        "staff/crew/index.html",
        {"request": request, "user": user, "members": members,
         "schengen_warning": schengen_warning},
    )


@router.get("/crew/new", response_class=HTMLResponse)
async def crew_new_form(
    request: Request,
    user=Depends(require_permission("crew", "M")),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/crew/new.html",
        {"request": request, "user": user, "error": None},
    )


@router.post("/crew/new")
async def crew_new_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("crew", "M")),
) -> RedirectResponse:
    f = await request.form()
    m = CrewMember(
        full_name=f["full_name"],
        role=f["role"],
        nationality=(f.get("nationality") or "").upper()[:2] or None,
        date_of_birth=(date.fromisoformat(f["date_of_birth"]) if f.get("date_of_birth") else None),
        passport_number=f.get("passport_number") or None,
        passport_expires_at=(date.fromisoformat(f["passport_expires_at"]) if f.get("passport_expires_at") else None),
        email=f.get("email") or None,
        phone=f.get("phone") or None,
        notes=f.get("notes") or None,
    )
    db.add(m)
    await db.flush()
    return RedirectResponse(url="/crew", status_code=303)


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
    if decision in ("approved", "rejected"):
        l.status = decision
        l.decided_by_id = user.id
        l.decided_at = datetime.now(timezone.utc)
    return RedirectResponse(url="/rh", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                              ESCALE Import/Export
# ────────────────────────────────────────────────────────────────────


@router.get("/escale", response_class=HTMLResponse)
async def escale_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "C")),
) -> HTMLResponse:
    now = datetime.now(timezone.utc)
    upcoming = list((await db.execute(
        select(Leg).where(Leg.eta > now - timedelta(days=14))
        .order_by(Leg.eta.asc()).limit(20)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/escale/index.html",
        {"request": request, "user": user, "legs": upcoming},
    )


@router.get("/escale/{leg_id}", response_class=HTMLResponse)
async def escale_detail(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "C")),
) -> HTMLResponse:
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg not found")
    ops_import = list((await db.execute(
        select(EscaleOperation).where(EscaleOperation.leg_id == leg_id)
        .where(EscaleOperation.direction == "IMPORT").order_by(EscaleOperation.planned_start)
    )).scalars().all())
    ops_export = list((await db.execute(
        select(EscaleOperation).where(EscaleOperation.leg_id == leg_id)
        .where(EscaleOperation.direction == "EXPORT").order_by(EscaleOperation.planned_start)
    )).scalars().all())
    ops_common = list((await db.execute(
        select(EscaleOperation).where(EscaleOperation.leg_id == leg_id)
        .where((EscaleOperation.direction == "BOTH") | (EscaleOperation.direction.is_(None)))
        .order_by(EscaleOperation.planned_start)
    )).scalars().all())
    dockers = list((await db.execute(
        select(DockerShift).where(DockerShift.leg_id == leg_id)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/escale/detail.html",
        {"request": request, "user": user, "leg": leg,
         "ops_import": ops_import, "ops_export": ops_export, "ops_common": ops_common,
         "dockers": dockers},
    )


@router.post("/escale/{leg_id}/operation")
async def escale_add_op(
    leg_id: int,
    direction: str = Form(""),
    operation_type: str = Form(...),
    action: str = Form(...),
    label: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("escale", "M")),
) -> RedirectResponse:
    op = EscaleOperation(
        leg_id=leg_id,
        direction=direction or None,
        operation_type=operation_type,
        action=action,
        label=label or None,
    )
    db.add(op)
    await db.flush()
    return RedirectResponse(url=f"/escale/{leg_id}", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                              FINANCE
# ────────────────────────────────────────────────────────────────────


@router.get("/finance", response_class=HTMLResponse)
async def finance_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("finance", "C")),
) -> HTMLResponse:
    finances = list((await db.execute(
        select(LegFinance).order_by(LegFinance.updated_at.desc()).limit(20)
    )).scalars().all())
    opex = list((await db.execute(
        select(OpexParameter).order_by(OpexParameter.parameter_name)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/finance/index.html",
        {"request": request, "user": user, "finances": finances, "opex": opex},
    )


# ────────────────────────────────────────────────────────────────────
#                                  KPI
# ────────────────────────────────────────────────────────────────────


@router.get("/kpi", response_class=HTMLResponse)
async def kpi_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    kpis = list((await db.execute(select(LegKPI).order_by(LegKPI.updated_at.desc()).limit(30))).scalars().all())
    total_tonnage = sum(float(k.tonnage_kg or 0) for k in kpis) / 1000
    total_co2_avoided = sum(float(k.co2_avoided_kg or 0) for k in kpis)
    on_time_count = sum(1 for k in kpis if k.on_time)
    on_time_pct = (on_time_count / len(kpis) * 100) if kpis else 0
    return templates.TemplateResponse(
        "staff/kpi/index.html",
        {"request": request, "user": user, "kpis": kpis,
         "total_tonnage_t": total_tonnage, "total_co2_avoided_kg": total_co2_avoided,
         "on_time_pct": on_time_pct},
    )


# ────────────────────────────────────────────────────────────────────
#                                  MRV
# ────────────────────────────────────────────────────────────────────


@router.get("/mrv", response_class=HTMLResponse)
async def mrv_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    events = list((await db.execute(
        select(MRVEvent).order_by(MRVEvent.recorded_at.desc()).limit(50)
    )).scalars().all())
    params = list((await db.execute(select(MRVParameter).order_by(MRVParameter.name))).scalars().all())
    return templates.TemplateResponse(
        "staff/mrv/index.html",
        {"request": request, "user": user, "events": events, "params": params},
    )


# ────────────────────────────────────────────────────────────────────
#                                CLAIMS
# ────────────────────────────────────────────────────────────────────


@router.get("/claims", response_class=HTMLResponse)
async def claims_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "C")),
) -> HTMLResponse:
    claims = list((await db.execute(
        select(Claim).order_by(Claim.declared_at.desc()).limit(50)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/claims/index.html",
        {"request": request, "user": user, "claims": claims},
    )


@router.get("/claims/new", response_class=HTMLResponse)
async def claims_new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
) -> HTMLResponse:
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(50))).scalars().all())
    return templates.TemplateResponse(
        "staff/claims/new.html",
        {"request": request, "user": user, "legs": legs},
    )


@router.post("/claims/new")
async def claims_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("claims", "M")),
) -> RedirectResponse:
    f = await request.form()
    ref = f"CLM-{datetime.now(timezone.utc).year}-{secrets.token_hex(2).upper()}"
    c = Claim(
        reference=ref,
        claim_type=f["claim_type"],
        leg_id=int(f["leg_id"]) if f.get("leg_id") else None,
        title=f["title"],
        description=f["description"],
        occurred_at=datetime.fromisoformat(f["occurred_at"].replace("T", " ")).replace(tzinfo=timezone.utc),
        status="open",
        provision_eur=Decimal(f["provision_eur"]) if f.get("provision_eur") else None,
        insurer=f.get("insurer") or None,
        created_by_id=user.id,
    )
    db.add(c)
    await db.flush()
    return RedirectResponse(url="/claims", status_code=303)


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
    from app.models.feature_flag import FeatureFlag
    flags = list((await db.execute(select(FeatureFlag).order_by(FeatureFlag.key))).scalars().all())
    # Aggregate port counts for the admin overview block
    total_ports = await db.scalar(select(func.count(Port.id)))
    active_ports = await db.scalar(select(func.count(Port.id)).where(Port.is_active.is_(True)))
    return templates.TemplateResponse(
        "staff/admin/index.html",
        {"request": request, "user": user, "users": users,
         "vessels": vessels, "flags": flags,
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
