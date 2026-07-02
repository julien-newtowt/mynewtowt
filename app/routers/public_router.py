"""Public-facing routes: landing, route search, leg detail, about pages.

No authentication required. The router is designed for prospects /
unauthenticated clients and exposes only data flagged `is_bookable=True`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_optional_client
from app.config import settings
from app.database import get_db
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.templating import templates

router = APIRouter(tags=["public"])


@router.get("/fleet", response_class=HTMLResponse)
async def fleet_tracker(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Carte publique de la flotte — dernières positions de chaque navire."""
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

    from app.i18n import DEFAULT, SUPPORTED

    target = request.headers.get("referer") or "/"
    # Anti open-redirect : pour toute URL absolue, on ne conserve que le chemin
    # (path + query) pour rester sur le même serveur quel que soit SITE_URL.
    if target.startswith(("http://", "https://")):
        from urllib.parse import urlparse as _urlparse

        _p = _urlparse(target)
        target = (_p.path or "/") + (("?" + _p.query) if _p.query else "")

    if lang not in SUPPORTED:
        lang = DEFAULT
    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie(
        "towt_lang",
        lang,
        max_age=365 * 86400,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return resp


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    upcoming = await _next_bookable_legs(db, limit=6)
    from app.services import analytics, service_reliability, social_proof

    await analytics.record(
        db, "landing_view", lang=getattr(request.state, "lang", "fr"), channel="public"
    )
    # Preuve sociale : compteurs réels (cache 10 min) + presse publiée ;
    # témoignages/logos ne s'affichent que si contenu + accord fournis.
    counters = await social_proof.counters(db)
    # Taux de service (ponctualité) — affiché seulement au-dessus d'un
    # échantillon minimal (pas de « 100 % sur 1 traversée »).
    reliability = await service_reliability.overall(db)
    return templates.TemplateResponse(
        "public/landing.html",
        {
            "request": request,
            "upcoming_legs": upcoming,
            "counters": counters,
            "reliability": reliability,
            "press_mentions": social_proof.PRESS_MENTIONS,
            "testimonials": social_proof.TESTIMONIALS,
            "client_logos": social_proof.CLIENT_LOGOS,
        },
    )


@router.get("/routes", response_class=HTMLResponse)
async def routes_search(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    # Dates reçues en str (jamais en date typée) : un champ vide ``""`` soumis
    # par le formulaire ne doit pas déclencher de 422 — on parse en tolérant.
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    parsed_from = _parse_date_param(from_date)
    parsed_to = _parse_date_param(to_date)
    results = await _search_legs(
        db, from_country=from_, to_country=to, from_date=parsed_from, to_date=parsed_to
    )
    return templates.TemplateResponse(
        "public/routes.html",
        {
            "request": request,
            "legs": results,
            "filters": {
                "from": from_ or "",
                "to": to or "",
                "from_date": parsed_from.date().isoformat() if parsed_from else "",
                "to_date": parsed_to.date().isoformat() if parsed_to else "",
            },
        },
    )


def _parse_date_param(value: str | None) -> datetime | None:
    """Parse une date de filtre tolérante : vide/invalide → None (pas de 422).

    Accepte ``YYYY-MM-DD`` (``<input type=date>``) ou un datetime ISO complet.
    """
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.combine(date.fromisoformat(raw), datetime.min.time())
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@router.get("/routes/{leg_code}", response_class=HTMLResponse)
async def route_detail(
    request: Request,
    leg_code: str,
    db: AsyncSession = Depends(get_db),
    client=Depends(get_optional_client),
) -> HTMLResponse:
    stmt = (
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.leg_code == leg_code)
        .where(Leg.is_bookable.is_(True))
    )
    row = (await db.execute(stmt)).first()
    if not row:
        return templates.TemplateResponse("public/404.html", {"request": request}, status_code=404)
    leg, vessel = row
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)

    from app.services import analytics

    await analytics.record(
        db,
        "route_view",
        reference=leg.leg_code,
        lang=getattr(request.state, "lang", "fr"),
        channel="client" if client else "public",
    )

    # Config portuaire (agent, docs, restrictions) pour les blocs port.
    from app.models.finance import PortConfig

    pol_config = (
        await db.execute(select(PortConfig).where(PortConfig.port_id == leg.departure_port_id))
    ).scalar_one_or_none()
    pod_config = (
        await db.execute(select(PortConfig).where(PortConfig.port_id == leg.arrival_port_id))
    ).scalar_one_or_none()

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
    cut_off_at = leg.booking_close_at or (leg.etd - timedelta(hours=48))

    # Ponctualité historique de la ligne (même POL → POD) — affichée si
    # l'échantillon est suffisant (actif de confiance, P10).
    from app.services import service_reliability

    reliability = await service_reliability.for_route(
        db, leg.departure_port_id, leg.arrival_port_id
    )

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
            "cut_off_at": cut_off_at,
            "reliability": reliability,
            "map_token": settings.map_token,
            "client": client,
        },
    )


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/about.html", {"request": request})


@router.get("/about/anemos", response_class=HTMLResponse)
async def about_anemos(request: Request) -> HTMLResponse:
    """Méthodologie Certification Anemos (anciennement /about/co2)."""
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


@router.get("/solutions/cafe", response_class=HTMLResponse)
async def solutions_cafe(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Page verticale « Café » du kit B2B2C : récits d'origine (Colombie,
    Guatemala, Mexique) + dataviz interactive du CO₂ évité. Indexable (cf.
    sitemap / llms.txt). Les récits sont des gabarits rendus avec des valeurs
    d'exemple ; l'ERP injecte les valeurs réelles depuis le certificat."""
    from app.services import analytics
    from app.services.mfa import qr_data_uri

    await analytics.record(
        db,
        "solutions_view",
        lang=getattr(request.state, "lang", "fr"),
        channel="public",
        detail=analytics.detail_with_utm(request, "cafe"),
    )
    # QR réel (scannable) vers l'outil public de vérification des certificats —
    # cohérent avec « CO₂ évité, vérifiable en scannant le code ».
    verify_url = f"{settings.site_url.rstrip('/')}/verify"
    return templates.TemplateResponse(
        "public/solutions_cafe.html",
        {
            "request": request,
            "co2eq_verify_url": verify_url,
            "co2eq_qr": qr_data_uri(verify_url),
        },
    )


@router.get("/solutions/cacao", response_class=HTMLResponse)
async def solutions_cacao(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Page verticale « Cacao » du kit B2B2C : récits d'origine (Équateur,
    Pérou, République dominicaine) + dataviz du CO₂ évité. Verticale sœur de
    /solutions/cafe (café-cacao) — indexable (sitemap / llms.txt). Les récits
    sont des gabarits rendus avec des valeurs d'exemple ; l'ERP injecte les
    valeurs réelles depuis le certificat."""
    from app.services import analytics
    from app.services.mfa import qr_data_uri

    await analytics.record(
        db,
        "solutions_view",
        lang=getattr(request.state, "lang", "fr"),
        channel="public",
        detail=analytics.detail_with_utm(request, "cacao"),
    )
    verify_url = f"{settings.site_url.rstrip('/')}/verify"
    return templates.TemplateResponse(
        "public/solutions_cacao.html",
        {
            "request": request,
            "co2eq_verify_url": verify_url,
            "co2eq_qr": qr_data_uri(verify_url),
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _next_bookable_legs(db: AsyncSession, *, limit: int = 6) -> list[dict[str, Any]]:
    from decimal import Decimal

    from app.services import co2 as co2_service
    from app.services.ports import haversine_nm

    # Poids de référence d'une palette pour la vignette « CO₂ évité / palette »
    # affichée sur les cartes de leg (storytelling landing — pas un devis).
    PALLET_WEIGHT_T = Decimal("0.8")

    factors = await co2_service.get_factors(db)
    now = datetime.now(UTC)
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
        co2_per_pallet_kg: int | None = None
        if (
            pol is not None
            and pod is not None
            and pol.latitude is not None
            and pol.longitude is not None
            and pod.latitude is not None
            and pod.longitude is not None
        ):
            distance_nm = Decimal(
                str(haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude))
            )
            estimate = co2_service.estimate(
                distance_nm=distance_nm, tonnage_t=PALLET_WEIGHT_T, factors=factors
            )
            co2_per_pallet_kg = int(estimate.avoided_co2_kg.to_integral_value())
        out.append(
            {
                "leg_id": leg.id,
                "leg_code": leg.leg_code,
                "vessel_name": vessel.name,
                "pol": pol,
                "pod": pod,
                "etd": leg.etd,
                "eta": leg.eta,
                "co2_per_pallet_kg": co2_per_pallet_kg,
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
    now = datetime.now(UTC)
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
        legs.append(
            {
                "leg_id": leg.id,
                "leg_code": leg.leg_code,
                "vessel_name": vessel.name,
                "pol": pol,
                "pod": pod,
                "etd": leg.etd,
                "eta": leg.eta,
            }
        )
    return legs
