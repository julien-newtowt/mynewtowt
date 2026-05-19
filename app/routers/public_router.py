"""Public-facing routes: landing, route search, leg detail, about pages.

No authentication required. The router is designed for prospects /
unauthenticated clients and exposes only data flagged `is_bookable=True`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.capacity import get_available_capacity, NotBookable
from app.templating import templates

router = APIRouter(tags=["public"])


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    upcoming = await _next_bookable_legs(db, limit=6)
    return templates.TemplateResponse(
        "public/landing.html",
        {"request": request, "upcoming_legs": upcoming},
    )


@router.get("/routes", response_class=HTMLResponse)
async def routes_search(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    results = await _search_legs(db, from_country=from_, to_country=to,
                                 from_date=from_date, to_date=to_date)
    return templates.TemplateResponse(
        "public/routes.html",
        {
            "request": request,
            "legs": results,
            "filters": {
                "from": from_ or "",
                "to": to or "",
                "from_date": from_date.isoformat() if from_date else "",
                "to_date": to_date.isoformat() if to_date else "",
            },
        },
    )


@router.get("/routes/{leg_code}", response_class=HTMLResponse)
async def route_detail(
    request: Request, leg_code: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    stmt = (
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.leg_code == leg_code)
        .where(Leg.is_bookable.is_(True))
    )
    row = (await db.execute(stmt)).first()
    if not row:
        return templates.TemplateResponse(
            "public/404.html", {"request": request}, status_code=404
        )
    leg, vessel = row
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)

    try:
        capacity = await get_available_capacity(db, leg.id)
    except NotBookable:
        capacity = None

    return templates.TemplateResponse(
        "public/route_detail.html",
        {
            "request": request,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "capacity": capacity,
        },
    )


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about.html", {"request": request})


@router.get("/about/co2", response_class=HTMLResponse)
async def about_co2(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about_co2.html", {"request": request})


@router.get("/about/legal", response_class=HTMLResponse)
async def about_legal(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about_legal.html", {"request": request})


@router.get("/about/privacy", response_class=HTMLResponse)
async def about_privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about_privacy.html", {"request": request})


@router.get("/about/terms", response_class=HTMLResponse)
async def about_terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about_terms.html", {"request": request})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _next_bookable_legs(db: AsyncSession, *, limit: int = 6) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.is_bookable.is_(True))
        .where(Leg.etd > now)
        .order_by(Leg.etd.asc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[dict[str, Any]] = []
    for leg, vessel in rows:
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        try:
            capacity = await get_available_capacity(db, leg.id)
            available = capacity.available_palettes
            capacity_total = capacity.capacity_palettes
        except Exception:
            available = 0
            capacity_total = 0
        out.append(
            {
                "leg_id": leg.id,
                "leg_code": leg.leg_code,
                "vessel_name": vessel.name,
                "pol": pol,
                "pod": pod,
                "etd": leg.etd,
                "eta": leg.eta,
                "available_palettes": available,
                "capacity_palettes": capacity_total,
                "price_per_palette": leg.public_price_per_palette_eur,
            }
        )
    return out


async def _search_legs(
    db: AsyncSession,
    *,
    from_country: str | None,
    to_country: str | None,
    from_date: datetime | None,
    to_date: datetime | None,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.is_bookable.is_(True))
        .where(Leg.etd > now)
    )
    if from_date:
        stmt = stmt.where(Leg.etd >= from_date)
    if to_date:
        stmt = stmt.where(Leg.etd <= to_date)
    stmt = stmt.order_by(Leg.etd.asc()).limit(50)

    rows = (await db.execute(stmt)).all()
    legs: list[dict[str, Any]] = []
    for leg, vessel in rows:
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        if from_country and pol and pol.country.upper() != from_country.upper():
            continue
        if to_country and pod and pod.country.upper() != to_country.upper():
            continue
        try:
            cap = await get_available_capacity(db, leg.id)
            available = cap.available_palettes
            capacity_total = cap.capacity_palettes
        except Exception:
            available = 0
            capacity_total = 0
        legs.append(
            {
                "leg_id": leg.id,
                "leg_code": leg.leg_code,
                "vessel_name": vessel.name,
                "pol": pol,
                "pod": pod,
                "etd": leg.etd,
                "eta": leg.eta,
                "available_palettes": available,
                "capacity_palettes": capacity_total,
                "price_per_palette": leg.public_price_per_palette_eur,
            }
        )
    return legs
