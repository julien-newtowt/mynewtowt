"""Estimation tarifaire côté extranet client (``/me/estimations``).

Réservé aux clients **authentifiés**, et strictement borné à **leurs** grilles
actives. Trois garde-fous, dans cet ordre :

1. L'identité commerciale est résolue **côté serveur** depuis la session
   (``client.commercial_client_id``). Aucun paramètre de requête, de formulaire
   ou de cookie n'influence la grille retenue — c'est la faille qu'il ne faut
   pas rejouer côté client.
2. La route demandée doit être couverte par une grille active du client. Sans ce
   contrôle, la résolution retomberait sur la grille par défaut et le client
   lirait un tarif public en croyant lire le sien.
3. Débit limité par compte **et** par IP : une estimation reste une consultation
   de prix négociés.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client
from app.database import get_db
from app.models.commercial import PALETTE_COEFFICIENTS, Client
from app.models.leg import Leg
from app.models.quote import Quote
from app.services import rate_limit
from app.services.activity import record as activity_record
from app.services.estimation import (
    EstimationError,
    assert_route_is_covered,
    list_for_client_account,
    notify_assigned_salesperson,
    routes_for_client,
    upcoming_legs_for_route,
)
from app.services.quoting import (
    QuotingError,
    compute_grid_quote,
    create_quote,
    find_quote,
    resolve_grid,
)
from app.templating import templates

router = APIRouter(tags=["estimation"])

_RATE_SCOPE = "estimate_client"
_RATE_MAX = 30
_RATE_WINDOW_MIN = 60
_MAX_PALETTES = 5000


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def _guard(db: AsyncSession, request: Request, client) -> None:
    """Limite le débit par compte **et** par IP."""
    for identifier in (f"account:{client.id}", f"ip:{_client_ip(request) or 'unknown'}"):
        if await rate_limit.exceeded(
            db,
            scope=_RATE_SCOPE,
            identifier=identifier,
            max_attempts=_RATE_MAX,
            window_minutes=_RATE_WINDOW_MIN,
        ):
            raise HTTPException(
                status_code=429,
                detail="Trop d'estimations demandées — réessayez dans une heure.",
            )
    await rate_limit.record(db, scope=_RATE_SCOPE, identifier=f"account:{client.id}")


@router.get("/me/estimations", response_class=HTMLResponse)
async def estimations_index(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Formulaire d'estimation + historique des estimations du compte."""
    routes = await routes_for_client(db, client.commercial_client_id)
    quotes = await list_for_client_account(db, client)
    commercial = None
    if client.commercial_client_id:
        commercial_client = await db.get(Client, client.commercial_client_id)
        if commercial_client is not None:
            commercial = commercial_client.assigned_user
    return templates.TemplateResponse(
        "client/estimations.html",
        {
            "request": request,
            "client": client,
            "routes": routes,
            "quotes": quotes,
            "commercial": commercial,
            "palette_formats": sorted(PALETTE_COEFFICIENTS),
            "error": None,
        },
    )


@router.get("/me/estimations/legs", response_class=HTMLResponse)
async def estimation_legs(
    request: Request,
    pol: str = "",
    pod: str = "",
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Partiel HTMX : voyages à venir sur la route choisie.

    La route est **revalidée** contre les grilles du client : ce partiel est une
    URL comme une autre, et sans contrôle il révélerait quels voyages desservent
    une route qui n'est pas couverte pour ce compte.
    """
    pol, pod = pol.strip().upper(), pod.strip().upper()
    try:
        await assert_route_is_covered(
            db,
            commercial_client_id=client.commercial_client_id,
            pol_locode=pol,
            pod_locode=pod,
        )
    except EstimationError:
        legs: list[Leg] = []
    else:
        legs = await upcoming_legs_for_route(db, pol, pod)
    return templates.TemplateResponse(
        "client/_estimation_legs.html", {"request": request, "legs": legs}
    )


@router.post("/me/estimations", response_class=HTMLResponse)
async def estimation_submit(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Calcule une estimation sur la grille du client et notifie son commercial."""
    await _guard(db, request, client)
    form = await request.form()

    pol = (form.get("pol_locode") or "").strip().upper()
    pod = (form.get("pod_locode") or "").strip().upper()
    palette_format = (form.get("palette_format") or "EPAL").strip()
    raw_palettes = (form.get("palettes") or "").strip()
    raw_leg = (form.get("leg_id") or "").strip()
    hazardous = form.get("hazardous") == "on"

    error: str | None = None
    palettes = 0
    try:
        palettes = int(raw_palettes)
    except ValueError:
        error = "Indiquez un nombre de palettes."
    else:
        if palettes <= 0 or palettes > _MAX_PALETTES:
            error = f"Le nombre de palettes doit être compris entre 1 et {_MAX_PALETTES}."
    if palette_format not in PALETTE_COEFFICIENTS:
        error = error or "Format de palette inconnu."

    leg: Leg | None = None
    if raw_leg and not error:
        try:
            leg = await db.get(Leg, int(raw_leg))
        except ValueError:
            leg = None

    tonnage: Decimal | None = None
    raw_tonnage = (form.get("tonnage_t") or "").strip().replace(",", ".")
    if raw_tonnage:
        try:
            value = Decimal(raw_tonnage)
            tonnage = value if value >= 0 else None
        except InvalidOperation:
            tonnage = None

    on_date = (leg.etd.date() if leg is not None and leg.etd else None) or datetime.now(UTC).date()

    if not error:
        try:
            await assert_route_is_covered(
                db,
                commercial_client_id=client.commercial_client_id,
                pol_locode=pol,
                pod_locode=pod,
                on_date=on_date,
            )
        except EstimationError as exc:
            error = str(exc)

    if error:
        routes = await routes_for_client(db, client.commercial_client_id)
        return templates.TemplateResponse(
            "client/estimations.html",
            {
                "request": request,
                "client": client,
                "routes": routes,
                "quotes": await list_for_client_account(db, client),
                "commercial": None,
                "palette_formats": sorted(PALETTE_COEFFICIENTS),
                "error": error,
            },
            status_code=422,
        )

    try:
        grid, route = await resolve_grid(
            db,
            pol_locode=pol,
            pod_locode=pod,
            on_date=on_date,
            commercial_client_id=client.commercial_client_id,
        )
        computed = compute_grid_quote(
            grid,
            route,
            items=[(palette_format, palettes)],
            tonnage_t=tonnage,
            hazardous=hazardous,
        )
    except QuotingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    quote = await create_quote(
        db,
        computed=computed,
        pol_locode=pol,
        pod_locode=pod,
        leg=leg,
        client_account=client,
        contact_name=client.contact_name,
        contact_email=client.email,
        contact_company=client.company_name,
        palettes_total=palettes,
        tonnage_t=tonnage,
        hazardous=hazardous,
        items=[(palette_format, palettes)],
        lang=getattr(request.state, "lang", "fr") or "fr",
    )
    quote.origin = "extranet"
    quote.commercial_client_id = client.commercial_client_id
    await db.flush()

    await notify_assigned_salesperson(db, quote)
    await activity_record(
        db,
        action="estimate_generated",
        user_name=client.email,
        module="commercial",
        entity_type="quote",
        entity_id=quote.id,
        entity_label=quote.reference,
        detail=f"{pol}→{pod} · {palettes} pal.",
        ip_address=_client_ip(request),
    )

    return templates.TemplateResponse(
        "client/estimation_result.html",
        {"request": request, "client": client, "quote": quote, "leg": leg},
        # Prix négociés : ne jamais mettre en cache intermédiaire.
        headers={"Cache-Control": "no-store"},
    )


@router.get("/me/estimations/{reference}", response_class=HTMLResponse)
async def estimation_detail(
    reference: str,
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Estimation du compte connecté — 404 si elle appartient à quelqu'un d'autre.

    404 et non 403 : confirmer l'existence d'une référence qu'on n'a pas le droit
    de lire renseignerait déjà un tiers.
    """
    quote = await find_quote(db, reference)
    if quote is None or quote.client_account_id != client.id:
        raise HTTPException(status_code=404, detail="Estimation introuvable")
    leg = await db.get(Leg, quote.leg_id) if quote.leg_id else None
    return templates.TemplateResponse(
        "client/estimation_result.html",
        {"request": request, "client": client, "quote": quote, "leg": leg},
        headers={"Cache-Control": "no-store"},
    )


async def _quotes_count(db: AsyncSession, client_account_id: int) -> int:
    from sqlalchemy import func

    return int(
        (
            await db.scalar(
                select(func.count(Quote.id)).where(Quote.client_account_id == client_account_id)
            )
        )
        or 0
    )
