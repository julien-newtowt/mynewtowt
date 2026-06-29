"""Outil public de génération de devis (COM-01/COM-02).

Accessible SANS identification : le prospect choisit une route (ou arrive
depuis une fiche traversée), décrit ses palettes et obtient un devis
calculé sur la grille tarifaire applicable — grille du client s'il est
connecté et relié à un client commercial, grille par défaut de la route
sinon. Le devis est persisté (référence DEV-…), consultable et
téléchargeable en PDF via une URL non listée.
"""

from __future__ import annotations

import secrets as _secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CLIENT_COOKIE, AuthError, get_current_client
from app.config import settings
from app.database import get_db
from app.models.commercial import PALETTE_COEFFICIENTS
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import QuoteView
from app.models.vessel import Vessel
from app.services import rate_limit
from app.services.activity import record as activity_record
from app.services.quoting import (
    QuotingError,
    compute_grid_quote,
    create_quote,
    find_quote,
    resolve_grid,
)
from app.templating import templates

router = APIRouter(tags=["devis"])

_MAX_ITEM_ROWS = 30
_RATE_LIMIT_SCOPE = "quote_public"
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_WINDOW_MIN = 30


async def optional_client(
    session_cookie: Annotated[str | None, Cookie(alias=CLIENT_COOKIE)] = None,
    db: AsyncSession = Depends(get_db),
):
    """Client authentifié si présent, sinon None — jamais d'exception."""
    if not session_cookie:
        return None
    try:
        return await get_current_client(session_cookie=session_cookie, db=db)
    except AuthError:
        return None


@router.get("/devis", response_class=HTMLResponse)
async def devis_form(
    request: Request,
    leg: str | None = None,
    db: AsyncSession = Depends(get_db),
    client=Depends(optional_client),
) -> HTMLResponse:
    context = await _form_context(request, db, client=client, leg_code=leg)
    return templates.TemplateResponse("public/devis_form.html", context)


@router.post("/devis", response_class=HTMLResponse)
async def devis_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client=Depends(optional_client),
):
    form = await request.form()

    # Honeypot anti-spam — même mécanique que /contact.
    if (form.get("website") or "").strip():
        return RedirectResponse(url="/devis", status_code=303)

    ip = _client_ip(request) or "unknown"
    if await rate_limit.exceeded(
        db,
        scope=_RATE_LIMIT_SCOPE,
        identifier=ip,
        max_attempts=_RATE_LIMIT_MAX,
        window_minutes=_RATE_LIMIT_WINDOW_MIN,
    ):
        raise HTTPException(status_code=429, detail="Trop de demandes — réessayez plus tard.")
    await rate_limit.record(db, scope=_RATE_LIMIT_SCOPE, identifier=ip)

    leg_code = (form.get("leg_code") or "").strip() or None
    pol = (form.get("pol") or "").strip().upper()
    pod = (form.get("pod") or "").strip().upper()
    contact_name = (form.get("contact_name") or "").strip() or None
    contact_email = (form.get("contact_email") or "").strip() or None
    contact_company = (form.get("contact_company") or "").strip() or None
    hazardous = form.get("hazardous") == "on"

    leg_obj: Leg | None = None
    if leg_code:
        leg_obj = (
            await db.execute(select(Leg).where(Leg.leg_code == leg_code))
        ).scalar_one_or_none()
        if leg_obj is not None:
            pol_port = await db.get(Port, leg_obj.departure_port_id)
            pod_port = await db.get(Port, leg_obj.arrival_port_id)
            pol = pol_port.locode if pol_port else pol
            pod = pod_port.locode if pod_port else pod

    items: list[tuple[str, int]] = []
    for i in range(_MAX_ITEM_ROWS):
        fmt = (form.get(f"items-{i}-format") or "").strip()
        raw_count = (form.get(f"items-{i}-count") or "").strip()
        if not fmt or not raw_count:
            continue
        try:
            count = int(raw_count)
        except ValueError:
            continue
        if count > 0 and fmt in PALETTE_COEFFICIENTS:
            items.append((fmt, count))

    tonnage_t: Decimal | None = None
    raw_tonnage = (form.get("tonnage_t") or "").strip().replace(",", ".")
    if raw_tonnage:
        try:
            tonnage_t = Decimal(raw_tonnage)
            if tonnage_t < 0:
                tonnage_t = None
        except InvalidOperation:
            tonnage_t = None

    error: str | None = None
    if not pol or not pod:
        error = "Sélectionnez un port de départ et un port d'arrivée."
    elif pol == pod:
        error = "Les ports de départ et d'arrivée doivent être différents."
    elif not items:
        error = "Indiquez au moins une ligne de palettes (format + quantité)."
    elif contact_email and form.get("consent") != "on":
        error = "Merci d'accepter la politique de confidentialité pour être recontacté."

    if error:
        context = await _form_context(
            request, db, client=client, leg_code=leg_code, error=error, values=dict(form)
        )
        return templates.TemplateResponse("public/devis_form.html", context, status_code=422)

    commercial_client_id = getattr(client, "commercial_client_id", None) if client else None
    on_date = (leg_obj.etd.date() if leg_obj is not None and leg_obj.etd else None) or datetime.now(
        UTC
    ).date()

    try:
        grid, route = await resolve_grid(
            db,
            pol_locode=pol,
            pod_locode=pod,
            on_date=on_date,
            commercial_client_id=commercial_client_id,
        )
        computed = compute_grid_quote(
            grid, route, items=items, tonnage_t=tonnage_t, hazardous=hazardous
        )
    except QuotingError as e:
        context = await _form_context(
            request, db, client=client, leg_code=leg_code, error=str(e), values=dict(form)
        )
        return templates.TemplateResponse("public/devis_form.html", context, status_code=422)

    quote = await create_quote(
        db,
        computed=computed,
        pol_locode=pol,
        pod_locode=pod,
        leg=leg_obj,
        client_account=client,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_company=contact_company,
        palettes_total=sum(c for _f, c in items),
        tonnage_t=tonnage_t,
        hazardous=hazardous,
        items=items,
        lang=getattr(request.state, "lang", "fr") or "fr",
    )

    await activity_record(
        db,
        action="quote_created",
        user_name=(client.email if client else contact_email) or "anonyme",
        module="commercial",
        entity_type="quote",
        entity_id=quote.id,
        entity_label=quote.reference,
        detail=f"{pol}→{pod} · {quote.palettes_total} pal. · {quote.total_eur} EUR",
        ip_address=_client_ip(request),
    )

    # Analytics tunnel : devis généré.
    from app.services import analytics

    await analytics.record(
        db,
        "quote_generated",
        reference=quote.reference,
        lang=getattr(request.state, "lang", "fr"),
        channel="client" if client else "public",
        detail=f"{pol}->{pod}",
    )

    # Lead commercial (best-effort) : tout devis invité avec email est un lead.
    if contact_email and client is None:
        try:
            from app.services.leads import push_lead

            await push_lead(
                db,
                name=contact_name or contact_email,
                email=contact_email,
                company=contact_company,
                message=f"Devis {quote.reference} — {pol}→{pod}, "
                f"{quote.palettes_total} palettes, total {quote.total_eur} EUR",
                source="devis",
                leg_code=leg_code,
                details={
                    "pol": pol,
                    "pod": pod,
                    "palettes": str(quote.palettes_total),
                    "tonnage_t": str(tonnage_t) if tonnage_t is not None else None,
                    "hazardous": "Oui" if hazardous else "Non",
                    "quote_reference": quote.reference,
                    "quote_total_eur": str(quote.total_eur),
                },
            )
        except Exception:
            pass

    return RedirectResponse(url=f"/devis/{quote.reference}", status_code=303)


@router.get("/devis/{reference}.pdf")
async def devis_pdf(
    reference: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    quote = await find_quote(db, reference)
    if quote is None:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    pol, pod = await _ports_by_locode(db, quote.pol_locode, quote.pod_locode)
    leg = await db.get(Leg, quote.leg_id) if quote.leg_id else None
    vessel = await db.get(Vessel, leg.vessel_id) if leg is not None else None

    from app.services import analytics

    await analytics.record(
        db, "quote_pdf_download", reference=quote.reference, lang=quote.lang
    )

    from weasyprint import HTML  # import tardif — dépendances natives lourdes

    from app.config import settings
    from app.templating import brand_for_lang

    tpl = templates.get_template("pdf/quote.html")
    html = tpl.render(
        quote=quote,
        pol=pol,
        pod=pod,
        leg=leg,
        vessel=vessel,
        brand=brand_for_lang(quote.lang),
        site_url=settings.site_url,
        issued_at=datetime.now(UTC),
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{quote.reference}.pdf"'},
    )


@router.get("/devis/{reference}", response_class=HTMLResponse)
async def devis_detail(
    request: Request,
    reference: str,
    db: AsyncSession = Depends(get_db),
    client=Depends(optional_client),
) -> HTMLResponse:
    quote = await find_quote(db, reference)
    if quote is None:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    pol, pod = await _ports_by_locode(db, quote.pol_locode, quote.pod_locode)
    leg = await db.get(Leg, quote.leg_id) if quote.leg_id else None

    # Historise la consultation du lien par le client (visible côté commercial).
    db.add(
        QuoteView(
            quote_id=quote.id,
            viewer="client" if client is not None else "guest",
            ip_address=_client_ip(request),
        )
    )
    await db.flush()

    # CO₂ évité estimé sur le lot (réassurance + dataviz équivalences).
    co2_avoided_kg = await _quote_co2_avoided_kg(db, quote, pol, pod, leg)

    resp = templates.TemplateResponse(
        "public/devis_result.html",
        {
            "request": request,
            "quote": quote,
            "pol": pol,
            "pod": pod,
            "leg": leg,
            "client": client,
            "co2_avoided_kg": co2_avoided_kg,
        },
    )
    # Mémorise le devis pour pré-remplir la réservation (survit au mur de
    # connexion : le wizard /booking lit ce cookie). Durée ~7 jours pour
    # laisser le temps de la décision d'achat.
    resp.set_cookie(
        "towt_pending_quote",
        quote.reference,
        max_age=604800,
        httponly=True,
        samesite="lax",
    )
    return resp


async def _quote_co2_avoided_kg(db, quote, pol, pod, leg) -> float:
    """CO₂ évité estimé (kg) pour le lot du devis. Best-effort → 0.0 si inconnu."""
    import contextlib

    with contextlib.suppress(Exception):
        from app.services import co2 as co2_service
        from app.services.ports import haversine_nm

        distance_nm = None
        if leg is not None and getattr(leg, "distance_nm", None):
            distance_nm = Decimal(str(leg.distance_nm))
        elif (
            pol is not None
            and pod is not None
            and pol.latitude is not None
            and pod.latitude is not None
        ):
            distance_nm = Decimal(
                str(haversine_nm(pol.latitude, pol.longitude, pod.latitude, pod.longitude))
            )
        if distance_nm is None or distance_nm <= 0:
            return 0.0
        # Tonnage : valeur saisie, sinon estimation 500 kg/palette (cohérent
        # avec l'estimateur du wizard).
        tonnage = quote.tonnage_t
        if not tonnage:
            tonnage = (Decimal(quote.palettes_total or 0) * Decimal("500")) / Decimal("1000")
        if tonnage <= 0:
            return 0.0
        factors = await co2_service.get_factors(db)
        est = co2_service.estimate(
            distance_nm=distance_nm, tonnage_t=tonnage, factors=factors
        )
        return float(est.avoided_co2_kg)
    return 0.0


@router.post("/api/quotes/followup")
async def quotes_followup_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Cron externe (Power Automate) — relance J+1 sur devis non convertis.

    Auth par X-API-Token. Retourne 503 si ``QUOTE_FOLLOWUP_API_TOKEN`` absent.
    """
    expected = settings.quote_followup_api_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QUOTE_FOLLOWUP_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    from app.services import quote_followup

    result = await quote_followup.send_followups(db)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _form_context(
    request: Request,
    db: AsyncSession,
    *,
    client,
    leg_code: str | None,
    error: str | None = None,
    values: dict | None = None,
) -> dict:
    leg_obj: Leg | None = None
    leg_pol: Port | None = None
    leg_pod: Port | None = None
    vessel: Vessel | None = None
    if leg_code:
        leg_obj = (
            await db.execute(select(Leg).where(Leg.leg_code == leg_code))
        ).scalar_one_or_none()
        if leg_obj is not None:
            leg_pol = await db.get(Port, leg_obj.departure_port_id)
            leg_pod = await db.get(Port, leg_obj.arrival_port_id)
            vessel = await db.get(Vessel, leg_obj.vessel_id)

    pol_ports, pod_ports = await _route_ports(db)
    # Reconstitue les lots saisis (pour repopuler après erreur) à partir des
    # valeurs soumises ; tolère les trous d'index laissés par la suppression
    # d'un lot côté JS. Au moins un lot vide par défaut.
    item_rows: list[dict] = []
    if values:
        for i in range(_MAX_ITEM_ROWS):
            fmt = (values.get(f"items-{i}-format") or "").strip()
            cnt = (values.get(f"items-{i}-count") or "").strip()
            if fmt or cnt:
                item_rows.append({"format": fmt, "count": cnt})
    if not item_rows:
        item_rows = [{"format": "", "count": ""}]

    return {
        "request": request,
        "client": client,
        "leg": leg_obj,
        "leg_pol": leg_pol,
        "leg_pod": leg_pod,
        "vessel": vessel,
        "pol_ports": pol_ports,
        "pod_ports": pod_ports,
        "pallet_formats": list(PALETTE_COEFFICIENTS.keys()),
        "item_rows": item_rows,
        "error": error,
        "values": values or {},
    }


async def _route_ports(db: AsyncSession) -> tuple[list[Port], list[Port]]:
    """Ports effectivement desservis (présents au planning), POL et POD."""
    pol_ids = select(Leg.departure_port_id).distinct()
    pod_ids = select(Leg.arrival_port_id).distinct()
    pols = (
        (await db.execute(select(Port).where(Port.id.in_(pol_ids)).order_by(Port.name)))
        .scalars()
        .all()
    )
    pods = (
        (await db.execute(select(Port).where(Port.id.in_(pod_ids)).order_by(Port.name)))
        .scalars()
        .all()
    )
    return list(pols), list(pods)


async def _ports_by_locode(
    db: AsyncSession, pol_locode: str, pod_locode: str
) -> tuple[Port | None, Port | None]:
    pol = (await db.execute(select(Port).where(Port.locode == pol_locode))).scalar_one_or_none()
    pod = (await db.execute(select(Port).where(Port.locode == pod_locode))).scalar_one_or_none()
    return pol, pod


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
