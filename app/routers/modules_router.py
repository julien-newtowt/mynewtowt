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

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(tags=["modules"])


# NOTE V3.7 (ARC-03) — Les routes /onboard* (landing, navigation, escale,
# cargo, crew + POST noon-report / watch-log / visitor) ont été extraites
# vers le routeur dédié ``onboard_router`` (monté dans main.py).


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


# NOTE SIRH-L0 — Les routes /rh, /rh/leave (POST), /rh/leave/{id}/decide
# ont été extraites vers le routeur dédié ``rh_router`` (monté dans
# main.py), prélude à la montée en charge du SIRH sédentaires. Voir
# ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md``.


# NOTE V3.1 — Les routes /escale, /escale/{leg_id}, /escale/{leg_id}/operation
# ont été retirées : le routeur dédié ``escale_router`` les sert.


# NOTE V3.2 — Routes /finance/* retirées : ``finance_router`` les sert désormais.

# NOTE V3.1 — Routes /kpi, /mrv et /claims, /claims/new (GET+POST) retirées :
# ``mrv_router`` et ``claims_router`` les servent désormais.


# ────────────────────────────────────────────────────────────────────
#                               TRACKING
# ────────────────────────────────────────────────────────────────────


def _parse_day(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse un ``<input type=date>`` (YYYY-MM-DD) en datetime aware UTC."""
    if not value:
        return None
    try:
        d = date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None
    t = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return t.replace(hour=23, minute=59, second=59) if end_of_day else t


@router.get("/tracking", response_class=HTMLResponse)
async def tracking_index(
    request: Request,
    history: int = 0,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    last_positions = {}
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
    from app.config import settings as _settings

    # ── Mode « historique des trajets » ───────────────────────────────────
    # Toggle en haut de page : affiche le filtre de référence (navire × année ×
    # leg, cf. _leg_filter.html) + un cadrage par dates, puis trace tous les
    # points enregistrés reliés par un trait (trajet réellement réalisé).
    history_on = bool(history)
    history_ctx = None
    if history_on:
        from urllib.parse import urlencode

        from app.services.leg_filter import build_leg_filter
        from app.services.voyage_track import (
            positions_for_leg,
            positions_in_window,
            positions_payload,
        )

        f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id)
        selected_leg = f["selected_leg"]
        sel_vessel = next((v for v in vessels if v.code == f["selected_vessel"]), None)

        df = _parse_day(date_from)
        dt_to = _parse_day(date_to, end_of_day=True)

        positions: list[VesselPosition] = []
        if selected_leg is not None:
            # Un leg sélectionné prime : sa fenêtre départ→arrivée fait foi.
            positions = await positions_for_leg(db, selected_leg)
        elif sel_vessel is not None:
            # Sinon, cadrage par dates explicites, à défaut l'année courante.
            start = df or datetime(f["current_year"], 1, 1, tzinfo=UTC)
            end = dt_to or datetime(f["current_year"], 12, 31, 23, 59, 59, tzinfo=UTC)
            positions = await positions_in_window(
                db, vessel_id=sel_vessel.id, start=start, end=end
            )

        # Query-params propagés sur les liens du filtre (préserve le mode + dates)
        xq_pairs = [("history", "1")]
        if date_from:
            xq_pairs.append(("date_from", date_from))
        if date_to:
            xq_pairs.append(("date_to", date_to))
        extra_query = urlencode(xq_pairs)

        track_vessel = None
        if selected_leg is not None:
            track_vessel = next(
                (v for v in vessels if v.id == selected_leg.vessel_id), sel_vessel
            )
        else:
            track_vessel = sel_vessel

        history_ctx = {
            "f": f,
            "points": positions_payload(positions),
            "date_from": date_from or "",
            "date_to": date_to or "",
            "extra_query": extra_query,
            "selected_leg": selected_leg,
            "track_vessel": track_vessel,
        }

    return templates.TemplateResponse(
        "staff/tracking/index.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "last_positions": last_positions,
            "maptiler_token": _settings.map_token,
            "history_on": history_on,
            "history": history_ctx,
        },
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
    bookings_confirmed = await db.scalar(
        select(func.count(Booking.id)).where(Booking.status == "confirmed")
    )
    clients_total = await db.scalar(select(func.count(ClientAccount.id)))
    tickets_active = await db.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.status.in_(("open", "in_progress", "pending_external"))
        )
    )
    legs_bookable = await db.scalar(select(func.count(Leg.id)).where(Leg.is_bookable.is_(True)))

    return templates.TemplateResponse(
        "staff/analytics/index.html",
        {
            "request": request,
            "user": user,
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

    now = datetime.now(UTC)
    year = now.year
    year_start = datetime(year, 1, 1, tzinfo=UTC)
    prev_year_start = datetime(year - 1, 1, 1, tzinfo=UTC)
    prev_year_end = datetime(year - 1, 12, 31, 23, 59, tzinfo=UTC)

    # Legs by status (current year)
    legs_all = list((await db.execute(select(Leg).where(Leg.etd >= year_start))).scalars().all())
    legs_by_status: dict[str, int] = {}
    for leg in legs_all:
        legs_by_status[leg.status] = legs_by_status.get(leg.status, 0) + 1

    # KPI totals (current year)
    kpis = list(
        (
            await db.execute(
                select(LegKPI).join(Leg, Leg.id == LegKPI.leg_id).where(Leg.etd >= year_start)
            )
        )
        .scalars()
        .all()
    )
    total_tonnage_t = sum(float(k.tonnage_kg) / 1000 for k in kpis)
    total_co2_avoided_kg = sum(float(k.co2_avoided_kg or 0) for k in kpis)
    on_time_count = sum(1 for k in kpis if k.on_time)
    on_time_pct = round(on_time_count / len(kpis) * 100) if kpis else 0

    # KPI totals (previous year for N-1 comparison)
    kpis_prev = list(
        (
            await db.execute(
                select(LegKPI)
                .join(Leg, Leg.id == LegKPI.leg_id)
                .where(Leg.etd >= prev_year_start, Leg.etd <= prev_year_end)
            )
        )
        .scalars()
        .all()
    )
    prev_tonnage_t = sum(float(k.tonnage_kg) / 1000 for k in kpis_prev)
    prev_co2_kg = sum(float(k.co2_avoided_kg or 0) for k in kpis_prev)

    # Revenue (invoices issued this year)
    revenue = (
        await db.scalar(
            select(func.sum(ClientInvoice.amount_incl_vat_eur)).where(
                ClientInvoice.issued_at >= year_start
            )
        )
        or 0
    )
    prev_revenue = (
        await db.scalar(
            select(func.sum(ClientInvoice.amount_incl_vat_eur)).where(
                ClientInvoice.issued_at >= prev_year_start,
                ClientInvoice.issued_at <= prev_year_end,
            )
        )
        or 0
    )

    clients_total = await db.scalar(select(func.count(ClientAccount.id))) or 0
    bookings_total = (
        await db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= year_start)) or 0
    )

    return templates.TemplateResponse(
        "staff/analytics/executive.html",
        {
            "request": request,
            "user": user,
            "year": year,
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

    now = datetime.now(UTC)
    year_start = datetime(now.year, 1, 1, tzinfo=UTC)

    # Funnel: bookings par statut
    funnel_statuses = [
        "draft",
        "submitted",
        "confirmed",
        "loaded",
        "at_sea",
        "discharged",
        "delivered",
        "cancelled",
    ]
    funnel_rows = (
        await db.execute(
            select(Booking.status, func.count(Booking.id).label("n"))
            .where(Booking.created_at >= year_start)
            .group_by(Booking.status)
        )
    ).all()
    funnel: dict[str, int] = dict.fromkeys(funnel_statuses, 0)
    for row in funnel_rows:
        if row.status in funnel:
            funnel[row.status] = row.n
    funnel_max = max(funnel.values()) or 1

    # Top clients by booking count
    top_clients_rows = (
        await db.execute(
            select(ClientAccount.company_name, func.count(Booking.id).label("n"))
            .join(Booking, Booking.client_account_id == ClientAccount.id)
            .where(Booking.created_at >= year_start)
            .group_by(ClientAccount.id, ClientAccount.company_name)
            .order_by(func.count(Booking.id).desc())
            .limit(8)
        )
    ).all()

    # Invoices by status
    inv_rows = (
        await db.execute(
            select(
                ClientInvoice.status,
                func.count(ClientInvoice.id).label("n"),
                func.sum(ClientInvoice.amount_incl_vat_eur).label("total"),
            )
            .where(ClientInvoice.issued_at >= year_start)
            .group_by(ClientInvoice.status)
        )
    ).all()
    inv_by_status = {r.status: {"count": r.n, "total": float(r.total or 0)} for r in inv_rows}

    total_revenue = sum(v["total"] for v in inv_by_status.values())
    conversion_pct = (
        round(funnel["confirmed"] / funnel["submitted"] * 100) if funnel["submitted"] else 0
    )

    return templates.TemplateResponse(
        "staff/analytics/commercial.html",
        {
            "request": request,
            "user": user,
            "year": now.year,
            "funnel": funnel,
            "funnel_statuses": funnel_statuses,
            "funnel_max": funnel_max,
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

    now = datetime.now(UTC)
    year_start = datetime(now.year, 1, 1, tzinfo=UTC)

    # Tickets: totals + SLA
    all_tickets = list(
        (
            await db.execute(
                select(Ticket)
                .where(Ticket.created_at >= year_start)
                .order_by(Ticket.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

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
    active_legs = list(
        (
            await db.execute(
                select(Leg, Vessel)
                .join(Vessel, Vessel.id == Leg.vessel_id)
                .where(Leg.status == "inprogress")
                .order_by(Leg.etd.asc())
            )
        ).all()
    )

    # Recent tickets (last 10 open)
    recent_tickets = [t for t in all_tickets if t.status not in closed_statuses][:10]

    total_breached = sum(p["breached"] for p in by_priority.values())
    total_open = sum(p["open"] for p in by_priority.values())

    return templates.TemplateResponse(
        "staff/analytics/operations.html",
        {
            "request": request,
            "user": user,
            "year": now.year,
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
    users = list(
        (await db.execute(select(User).order_by(User.created_at.desc()).limit(50))).scalars().all()
    )
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    # Aggregate port counts for the admin overview block
    total_ports = await db.scalar(select(func.count(Port.id)))
    active_ports = await db.scalar(select(func.count(Port.id)).where(Port.is_active.is_(True)))
    return templates.TemplateResponse(
        "staff/admin/index.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "vessels": vessels,
            "total_ports": total_ports or 0,
            "active_ports": active_ports or 0,
        },
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
    show: str = "all",  # 'all' | 'active' | 'inactive'
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    per_page = 50
    stmt = select(Port)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where((func.lower(Port.name).like(like)) | (func.lower(Port.locode).like(like)))
    if country:
        stmt = stmt.where(Port.country == country.upper())
    if source:
        stmt = stmt.where(Port.source == source)
    if show == "active":
        stmt = stmt.where(Port.is_active.is_(True))
    elif show == "inactive":
        stmt = stmt.where(Port.is_active.is_(False))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(Port.country, Port.locode).limit(per_page).offset((page - 1) * per_page)
    ports = list((await db.execute(stmt)).scalars().all())

    return templates.TemplateResponse(
        "staff/admin/ports.html",
        {
            "request": request,
            "user": user,
            "ports": ports,
            "page": page,
            "per_page": per_page,
            "total": total,
            "filters": {
                "q": q or "",
                "country": country or "",
                "source": source or "",
                "show": show,
            },
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
        db,
        action="port_toggle",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="admin",
        entity_type="port",
        entity_id=port.id,
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
    config = (
        await db.execute(select(PortConfig).where(PortConfig.port_id == port_id))
    ).scalar_one_or_none()
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
    config = (
        await db.execute(select(PortConfig).where(PortConfig.port_id == port_id))
    ).scalar_one_or_none()
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
        db,
        action="create" if is_create else "update",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="admin",
        entity_type="port_config",
        entity_id=config.id,
        entity_label=port.locode,
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
        db,
        action="ports_upload",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="admin",
        entity_type="port_batch",
        detail=f"source={source} parsed={len(rows)} inserted={ins} updated={upd}",
    )
    return RedirectResponse(url="/admin/ports?show=all", status_code=303)
