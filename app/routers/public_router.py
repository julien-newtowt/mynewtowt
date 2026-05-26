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

from app.config import settings
from app.database import get_db
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.capacity import BookingClosed, get_available_capacity, NotBookable
from app.templating import templates

router = APIRouter(tags=["public"])


@router.get("/fleet", response_class=HTMLResponse)
async def fleet_tracker(
    request: Request, db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Carte publique de la flotte — dernières positions de chaque navire."""
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    last_positions: dict[int, VesselPosition | None] = {}
    for v in vessels:
        p = (await db.execute(
            select(VesselPosition).where(VesselPosition.vessel_id == v.id)
            .order_by(VesselPosition.recorded_at.desc()).limit(1)
        )).scalar_one_or_none()
        last_positions[v.id] = p
    return templates.TemplateResponse(
        "public/fleet.html",
        {
            "request": request,
            "vessels": vessels,
            "last_positions": last_positions,
            "maptiler_token": settings.map_token,
        },
    )


@router.get("/lang/{lang}")
async def set_language(lang: str, request: Request):
    """Set the UI language cookie (FR/EN/...). Redirects back to the referer.

    GET volontaire : changement de langue idempotent, pas une mutation
    sensible. Évite la contrainte CSRF du double-submit cookie qui n'est
    pas encore posée au premier hit anonyme.
    """
    from fastapi.responses import RedirectResponse
    from app.config import settings as _s
    from app.i18n import SUPPORTED, DEFAULT

    target = request.headers.get("referer") or "/"
    # Anti open-redirect : refuse les URLs absolues hors SITE_URL
    if target.startswith(("http://", "https://")) and not target.startswith(_s.site_url):
        target = "/"

    if lang not in SUPPORTED:
        lang = DEFAULT
    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie(
        "towt_lang", lang, max_age=365 * 86400, httponly=False, samesite="lax", path="/",
    )
    return resp


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

    # Config portuaire (agent, docs, restrictions) pour les blocs port.
    from app.models.finance import PortConfig
    pol_config = (await db.execute(
        select(PortConfig).where(PortConfig.port_id == leg.departure_port_id)
    )).scalar_one_or_none()
    pod_config = (await db.execute(
        select(PortConfig).where(PortConfig.port_id == leg.arrival_port_id)
    )).scalar_one_or_none()

    # Distance orthodromique (NM) + durée — affichées dans le hero.
    from app.services.ports import haversine_nm
    distance_nm = None
    if pol and pod and pol.latitude is not None and pod.latitude is not None:
        distance_nm = round(
            haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude)
            * float(leg.elongation_coef or 1.0)
        )
    duration_days = None
    if leg.etd and leg.eta:
        duration_days = round((leg.eta - leg.etd).total_seconds() / 86400.0, 1)

    # Date de clôture des réservations : explicite ou ETD − 48 h.
    from datetime import timedelta
    cut_off_at = leg.booking_close_at or (leg.etd - timedelta(hours=48))

    # Estimation CO₂ per tonne pour l'éco-calculateur.
    from decimal import Decimal
    from app.services import co2 as co2_svc
    co2_est = None
    if distance_nm:
        try:
            co2_est = co2_svc.estimate(
                distance_nm=Decimal(str(distance_nm)),
                tonnage_t=Decimal("1"),
            )
        except Exception:
            pass

    return templates.TemplateResponse(
        "public/route_detail.html",
        {
            "request": request,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "pol_config": pol_config,
            "pod_config": pod_config,
            "distance_nm": distance_nm,
            "duration_days": duration_days,
            "capacity": capacity,
            "cut_off_at": cut_off_at,
            "co2_est": co2_est,
            "map_token": settings.map_token,
        },
    )


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about.html", {"request": request})


@router.get("/about/anemos", response_class=HTMLResponse)
async def about_anemos(request: Request) -> HTMLResponse:
    """Méthodologie Label Anemos (anciennement /about/co2)."""
    return templates.TemplateResponse("public/about_anemos.html", {"request": request})


@router.get("/about/co2")
async def about_co2_redirect_legacy():
    """Backward-compat : anciens liens /about/co2 → 301 /about/anemos."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/about/anemos", status_code=301)


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
            # leg ouvert à la liste publique mais non bookable / window fermée / config incomplète
            available = 0
            capacity_total = leg.public_capacity_palettes or 0
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
            capacity_total = leg.public_capacity_palettes or 0
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
