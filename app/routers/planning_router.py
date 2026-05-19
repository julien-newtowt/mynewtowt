"""Planning module — Gantt view, leg CRUD, public share by token.

Auth: staff with `planning` permission (C/M/S per matrix).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.planning import (
    InvalidLegDates,
    PlanningError,
    create_leg,
    create_share,
    delete_leg,
    detect_port_conflicts,
    list_legs_in_window,
    list_shares,
    lookup_share,
    revoke_share,
    update_leg,
)
from app.templating import templates

router = APIRouter(prefix="/planning", tags=["planning"])

GANTT_WINDOW_DAYS = 90


# ---------------------------------------------------------------------------
# Gantt index (staff)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_class=HTMLResponse,
)
async def gantt_index(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=7)
    window_end = now + timedelta(days=GANTT_WINDOW_DAYS)

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    legs = await list_legs_in_window(
        db,
        date_from=window_start,
        date_to=window_end,
        vessel_id=vessel_id,
    )

    # Pre-load port labels (avoid N+1)
    port_ids = {leg.departure_port_id for leg in legs} | {leg.arrival_port_id for leg in legs}
    ports = {
        p.id: p
        for p in (await db.execute(select(Port).where(Port.id.in_(port_ids) if port_ids else select(Port.id)))).scalars().all()
    } if port_ids else {}

    conflicts = detect_port_conflicts(legs)
    conflict_ids: set[int] = set()
    for a, b in conflicts:
        conflict_ids.add(a); conflict_ids.add(b)

    # Build Gantt rows (one per vessel) with positioned bars
    gantt_rows = _build_gantt_rows(
        vessels=vessels,
        legs=legs,
        window_start=window_start,
        window_end=window_end,
        ports=ports,
        conflict_ids=conflict_ids,
    )

    return templates.TemplateResponse(
        "staff/planning/index.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "legs": legs,
            "ports": ports,
            "gantt_rows": gantt_rows,
            "filter_vessel_id": vessel_id,
            "window_start": window_start,
            "window_end": window_end,
            "conflict_count": len(conflicts),
        },
    )


# ---------------------------------------------------------------------------
# Leg create / edit
# ---------------------------------------------------------------------------


@router.get(
    "/legs/new",
    response_class=HTMLResponse,
)
async def new_leg_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/leg_form.html",
        {
            "request": request,
            "user": user,
            "leg": None,
            "vessels": vessels,
            "ports": ports,
            "error": None,
        },
    )


@router.get("/legs/new-from-map", response_class=HTMLResponse)
async def new_leg_from_map(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    """Interactive map: click a port marker → snap → prefill form."""
    from app.config import settings

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/leg_from_map.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "maptiler_token": settings.map_token,
        },
    )


@router.post("/legs/new", response_class=HTMLResponse)
async def create_leg_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    form = await request.form()
    try:
        leg = await create_leg(
            db,
            vessel_id=int(form["vessel_id"]),
            departure_port_id=int(form["departure_port_id"]),
            arrival_port_id=int(form["arrival_port_id"]),
            etd=_parse_dt(form.get("etd")),
            eta=_parse_dt(form.get("eta")),
            is_bookable=form.get("is_bookable") == "on",
            public_capacity_palettes=_maybe_int(form.get("public_capacity_palettes")),
            # public_price_per_palette_eur géré par /commercial (grilles tarifaires)
            booking_close_at=_parse_dt(form.get("booking_close_at"), allow_empty=True),
            transit_speed_kn=_maybe_float(form.get("transit_speed_kn")),
            elongation_coef=_maybe_float(form.get("elongation_coef")),
            port_stay_planned_hours=_maybe_int(form.get("port_stay_planned_hours")),
        )
    except (InvalidLegDates, PlanningError, KeyError, ValueError) as e:
        vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
        ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
        return templates.TemplateResponse(
            "staff/planning/leg_form.html",
            {
                "request": request,
                "user": user,
                "leg": None,
                "vessels": vessels,
                "ports": ports,
                "error": f"Création impossible : {e}",
                "form": dict(form),
            },
            status_code=400,
        )

    await activity_record(
        db,
        action="leg_create",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
    )
    return RedirectResponse(url=f"/planning/legs/{leg.id}", status_code=303)


@router.get(
    "/legs/{leg_id}",
    response_class=HTMLResponse,
)
async def leg_detail(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    leg = await _get_leg_or_404(db, leg_id)
    vessel = await db.get(Vessel, leg.vessel_id)
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    return templates.TemplateResponse(
        "staff/planning/leg_detail.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
        },
    )


@router.get(
    "/legs/{leg_id}/edit",
    response_class=HTMLResponse,
)
async def edit_leg_form(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    leg = await _get_leg_or_404(db, leg_id)
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/leg_form.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessels": vessels,
            "ports": ports,
            "error": None,
        },
    )


@router.post("/legs/{leg_id}/edit", response_class=HTMLResponse)
async def update_leg_action(
    request: Request,
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> HTMLResponse:
    leg = await _get_leg_or_404(db, leg_id)
    form = await request.form()
    cascade = form.get("cascade") == "on"
    try:
        report = await update_leg(
            db,
            leg,
            vessel_id=_maybe_int(form.get("vessel_id")),
            etd=_parse_dt(form.get("etd"), allow_empty=True),
            eta=_parse_dt(form.get("eta"), allow_empty=True),
            departure_port_id=_maybe_int(form.get("departure_port_id")),
            arrival_port_id=_maybe_int(form.get("arrival_port_id")),
            is_bookable=(form.get("is_bookable") == "on"),
            public_capacity_palettes=_maybe_int(form.get("public_capacity_palettes")),
            # public_price_per_palette_eur géré par /commercial (grilles tarifaires)
            booking_close_at=_parse_dt(form.get("booking_close_at"), allow_empty=True),
            transit_speed_kn=_maybe_float(form.get("transit_speed_kn")),
            elongation_coef=_maybe_float(form.get("elongation_coef")),
            port_stay_planned_hours=_maybe_int(form.get("port_stay_planned_hours")),
            cascade=cascade,
        )
    except (InvalidLegDates, PlanningError) as e:
        vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
        ports = list((await db.execute(select(Port).order_by(Port.locode))).scalars().all())
        return templates.TemplateResponse(
            "staff/planning/leg_form.html",
            {
                "request": request,
                "user": user,
                "leg": leg,
                "vessels": vessels,
                "ports": ports,
                "error": str(e),
            },
            status_code=400,
        )
    await activity_record(
        db,
        action="leg_update",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
        detail=(
            f"cascade delta={report.delta_hours:.1f}h "
            f"impacted={len(report.impacted_leg_ids)}" if report else None
        ),
    )
    return RedirectResponse(url=f"/planning/legs/{leg.id}", status_code=303)


@router.post("/legs/{leg_id}/delete")
async def delete_leg_action(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "S")),
) -> RedirectResponse:
    leg = await _get_leg_or_404(db, leg_id)
    try:
        await delete_leg(db, leg)
    except PlanningError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await activity_record(
        db,
        action="leg_delete",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="leg",
        entity_id=leg.id,
        entity_label=leg.leg_code,
    )
    return RedirectResponse(url="/planning", status_code=303)


# ---------------------------------------------------------------------------
# Public share management (staff)
# ---------------------------------------------------------------------------


@router.get(
    "/shares",
    response_class=HTMLResponse,
)
async def shares_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    shares = await list_shares(db)
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    return templates.TemplateResponse(
        "staff/planning/shares.html",
        {"request": request, "user": user, "shares": shares, "vessels": vessels},
    )


@router.post("/shares")
async def shares_create(
    request: Request,
    label: str = Form(""),
    vessel_id: str = Form(""),
    only_bookable: str = Form(""),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    await create_share(
        db,
        label=label.strip() or None,
        vessel_id=int(vessel_id) if vessel_id else None,
        only_bookable=(only_bookable == "on"),
        description=description.strip() or None,
        expires_at=None,
        created_by_id=user.id,
    )
    return RedirectResponse(url="/planning/shares", status_code=303)


@router.post("/shares/{share_id}/revoke")
async def share_revoke(
    share_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    from app.models.planning_share import PlanningShare

    share = await db.get(PlanningShare, share_id)
    if not share:
        raise HTTPException(status_code=404, detail="Not found")
    await revoke_share(db, share)
    return RedirectResponse(url="/planning/shares", status_code=303)


# ---------------------------------------------------------------------------
# Public planning view (token, no auth)
# ---------------------------------------------------------------------------


@router.get("/share/{token}", response_class=HTMLResponse)
async def public_share(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    share = await lookup_share(db, token)
    if not share:
        return templates.TemplateResponse(
            "public/404.html",
            {"request": request},
            status_code=404,
        )

    # Bump access counter
    share.access_count += 1
    share.last_access_at = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)
    legs = await list_legs_in_window(
        db,
        date_from=now - timedelta(days=7),
        date_to=now + timedelta(days=GANTT_WINDOW_DAYS),
        vessel_id=share.vessel_id,
    )
    if share.only_bookable:
        legs = [leg for leg in legs if leg.is_bookable]

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    port_ids = {leg.departure_port_id for leg in legs} | {leg.arrival_port_id for leg in legs}
    ports = (
        {p.id: p for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()}
        if port_ids else {}
    )
    gantt_rows = _build_gantt_rows(
        vessels=vessels,
        legs=legs,
        window_start=now - timedelta(days=7),
        window_end=now + timedelta(days=GANTT_WINDOW_DAYS),
        ports=ports,
        conflict_ids=set(),
    )

    return templates.TemplateResponse(
        "public/planning_share.html",
        {
            "request": request,
            "share": share,
            "vessels": vessels,
            "legs": legs,
            "ports": ports,
            "gantt_rows": gantt_rows,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_gantt_rows(
    *,
    vessels: list[Vessel],
    legs: list[Leg],
    window_start: datetime,
    window_end: datetime,
    ports: dict[int, Port],
    conflict_ids: set[int],
) -> list[dict]:
    total_seconds = (window_end - window_start).total_seconds()
    rows: list[dict] = []
    by_vessel: dict[int, list[Leg]] = {}
    for leg in legs:
        by_vessel.setdefault(leg.vessel_id, []).append(leg)

    for vessel in vessels:
        bars: list[dict] = []
        for leg in by_vessel.get(vessel.id, []):
            start = max(leg.etd, window_start)
            end = min(leg.eta, window_end)
            if end <= start:
                continue
            left_pct = ((start - window_start).total_seconds() / total_seconds) * 100
            width_pct = ((end - start).total_seconds() / total_seconds) * 100
            pol = ports.get(leg.departure_port_id)
            pod = ports.get(leg.arrival_port_id)
            bars.append({
                "leg_id": leg.id,
                "leg_code": leg.leg_code,
                "status": leg.status,
                "left_pct": round(left_pct, 3),
                "width_pct": round(max(width_pct, 1.0), 3),
                "pol_locode": pol.locode if pol else "",
                "pod_locode": pod.locode if pod else "",
                "etd": leg.etd,
                "eta": leg.eta,
                "in_conflict": leg.id in conflict_ids,
                "is_bookable": leg.is_bookable,
            })
        rows.append({"vessel": vessel, "bars": bars})
    return rows


async def _get_leg_or_404(db: AsyncSession, leg_id: int) -> Leg:
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not found")
    return leg


def _parse_dt(value, allow_empty: bool = False) -> datetime:
    if value is None or value == "":
        if allow_empty:
            return None  # type: ignore[return-value]
        raise InvalidLegDates("Date required")
    # HTML <input type="datetime-local"> yields "2026-06-04T08:00"
    s = str(value).replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise InvalidLegDates(f"Invalid date format: {value}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _maybe_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


