"""Vitrine éditoriale publique — pages de conviction + demande de cotation.

Routes publiques (aucune authentification) :
- ``/flotte``      : « Notre flotte » (classe TSC 80, capacités, cales).
- ``/impact``      : environnement maîtrisé à bord, surveillance qualité, décarbonation.
- ``/preuves``     : méthode, vérification (EU MRV / THETIS-MRV), registre des certificats.
- ``/verify``      : vérification publique d'un certificat Anemos (sans PII, rate-limitée).
- ``/navigation``  : courants, propulsion vélique, routes.
- ``/contact``     : coordonnées + formulaire de demande de cotation (GET/POST).
- ``/contact/merci``: accusé de réception.

Le formulaire ne réalise **aucun paiement** : il valide, journalise et
prépare le relais vers l'équipe commerciale (extranet de réservation).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.i18n import get_lang_from_request
from app.models.anemos_certificate import AnemosCertificate
from app.services import blog as blog_svc
from app.services import contact as contact_svc
from app.services import rate_limit
from app.services.activity import record as activity_record
from app.services.leads import push_lead
from app.templating import templates

router = APIRouter(tags=["vitrine"])


@router.get("/flotte", response_class=HTMLResponse)
async def fleet_capabilities(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/flotte.html", {"request": request})


@router.get("/impact", response_class=HTMLResponse)
async def impact(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/impact.html", {"request": request})


@router.get("/preuves", response_class=HTMLResponse)
async def preuves(request: Request) -> HTMLResponse:
    """Page de preuve opposable (méthode / vérification / registre) — ENV-04.

    Actif commercial permanent : répond aux 4 questions d'un auditeur Scope 3
    (d'où vient le chiffre, qui l'a vérifié, mesuré ou théorique, où le
    retrouver). Statique — aucune donnée DB.
    """
    return templates.TemplateResponse("public/preuves.html", {"request": request})


# Lookup d'un certificat Anemos par référence — public, sans PII, rate-limité.
_VERIFY_RATE_SCOPE = "anemos_verify"


def _applied_factor(cert: AnemosCertificate) -> float | None:
    """Facteur NEWTOWT effectivement appliqué (g CO₂/t·km), dérivé du certificat.

    Reproductible à partir des valeurs persistées : émissions / (t × km) × 1000.
    Renvoie ``None`` si la base de calcul est nulle.
    """
    try:
        tonnage = Decimal(cert.tonnage_transported_t or 0)
        distance_km = Decimal(cert.distance_nm or 0) * Decimal("1.852")
        tkm = tonnage * distance_km
        if tkm <= 0:
            return None
        return float(Decimal(cert.co2_emitted_kg or 0) * Decimal("1000") / tkm)
    except Exception:
        return None


async def _lookup_certificate(
    db: AsyncSession, ref: str
) -> AnemosCertificate | None:
    """Résout une référence saisie en certificat (tolérant casse / préfixe)."""
    candidates = [ref]
    upper = ref.upper()
    if upper not in candidates:
        candidates.append(upper)
    if not upper.startswith("ANEMOS-"):
        candidates.append(f"ANEMOS-{upper}")
    for candidate in candidates:
        cert = (
            await db.execute(
                select(AnemosCertificate).where(AnemosCertificate.reference == candidate)
            )
        ).scalar_one_or_none()
        if cert is not None:
            return cert
    return None


@router.get("/verify", response_class=HTMLResponse)
async def verify_certificate(
    request: Request,
    ref: str | None = Query(default=None, max_length=40),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Vérification publique d'un certificat Anemos — sans PII, rate-limitée.

    Affiche uniquement les métriques physiques (tonnage, distance, CO₂ évité,
    méthode, date, facteur appliqué). Aucune donnée nominative (client,
    cargaison, adresses) n'est exposée. Référence introuvable → message neutre.
    """
    ctx: dict = {"request": request, "ref": ref, "searched": False, "certificate": None}
    if not ref or not ref.strip():
        return templates.TemplateResponse("public/verify.html", ctx)

    ctx["searched"] = True
    ip = request.client.host if request.client else ""
    if await rate_limit.exceeded(
        db, scope=_VERIFY_RATE_SCOPE, identifier=ip, max_attempts=30, window_minutes=10
    ):
        ctx["rate_limited"] = True
        return templates.TemplateResponse("public/verify.html", ctx, status_code=429)
    await rate_limit.record(db, scope=_VERIFY_RATE_SCOPE, identifier=ip)

    cert = await _lookup_certificate(db, ref.strip())
    if cert is not None:
        ctx["certificate"] = cert
        ctx["applied_factor"] = _applied_factor(cert)
    return templates.TemplateResponse("public/verify.html", ctx)


@router.get("/verify/{cert_ref}", response_class=HTMLResponse)
async def verify_certificate_by_ref(
    request: Request,
    cert_ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Lien direct (QR) ``/verify/{ref}`` — même rendu que ``/verify?ref=``."""
    return await verify_certificate(request=request, ref=cert_ref, db=db)


@router.get("/navigation", response_class=HTMLResponse)
async def navigation(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/navigation.html", {"request": request})


@router.get("/recrutement", response_class=HTMLResponse)
async def recrutement(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/recrutement.html", {"request": request})


@router.get("/presse", response_class=HTMLResponse)
async def presse(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/presse.html", {"request": request})


@router.get("/passagers", response_class=HTMLResponse)
async def passagers(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/passagers.html", {"request": request})


@router.get("/actualites", response_class=HTMLResponse)
async def actualites(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Actualités — billets de catégorie ``actualite`` (pont depuis LinkedIn)."""
    posts = await blog_svc.list_published(db, category="actualite")
    return templates.TemplateResponse(
        "public/actualites.html", {"request": request, "posts": posts}
    )


@router.get("/carnet", response_class=HTMLResponse)
async def carnet_index(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Carnet de construction — liste des jalons (du plus récent au plus ancien)."""
    posts = await blog_svc.list_published(db, category="carnet")
    return templates.TemplateResponse("public/carnet.html", {"request": request, "posts": posts})


@router.get("/carnet/{slug}", response_class=HTMLResponse)
async def carnet_post(
    request: Request, slug: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    post = await blog_svc.get_published_by_slug(db, slug)
    if post is None:
        return templates.TemplateResponse("public/404.html", {"request": request}, status_code=404)
    return templates.TemplateResponse("public/carnet_post.html", {"request": request, "post": post})


@router.get("/contact", response_class=HTMLResponse)
async def contact_form(
    request: Request,
    from_: str | None = None,
    to: str | None = None,
) -> HTMLResponse:
    """Affiche le formulaire de demande de cotation (pré-rempli si query)."""
    return templates.TemplateResponse(
        "public/contact.html",
        {
            "request": request,
            "values": {"pol": from_ or "", "pod": to or ""},
            "errors": {},
        },
    )


@router.post("/contact", response_class=HTMLResponse)
async def contact_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(""),
    email: str = Form(""),
    company: str = Form(""),
    phone: str = Form(""),
    pol: str = Form(""),
    pod: str = Form(""),
    cargo_nature: str = Form(""),
    volume_weight: str = Form(""),
    desired_dates: str = Form(""),
    message: str = Form(""),
    consent: str | None = Form(None),
    website: str = Form(""),  # honeypot anti-spam (doit rester vide)
):
    lang = get_lang_from_request(request)

    # Anti-spam non bloquant : on accuse réception sans persister.
    if contact_svc.is_spam(website):
        return RedirectResponse(url="/contact/merci", status_code=303)

    try:
        payload = contact_svc.validate_contact_payload(
            name=name,
            email=email,
            consent=bool(consent),
            company=company,
            phone=phone,
            pol=pol,
            pod=pod,
            cargo_nature=cargo_nature,
            volume_weight=volume_weight,
            desired_dates=desired_dates,
            message=message,
            lang=lang,
        )
    except contact_svc.ContactValidationError as exc:
        return templates.TemplateResponse(
            "public/contact.html",
            {
                "request": request,
                "values": {
                    "name": name,
                    "email": email,
                    "company": company,
                    "phone": phone,
                    "pol": pol,
                    "pod": pod,
                    "cargo_nature": cargo_nature,
                    "volume_weight": volume_weight,
                    "desired_dates": desired_dates,
                    "message": message,
                },
                "errors": exc.errors,
            },
            status_code=422,
        )

    entry = await contact_svc.create_contact_request(db, payload)
    await activity_record(
        db,
        action="contact_request_created",
        module="commercial",
        entity_type="contact_request",
        entity_id=entry.id,
        entity_label=payload.email,
        detail=f"{payload.pol or '?'} -> {payload.pod or '?'}",
        ip_address=request.client.host if request.client else None,
    )
    # COM-04 — relais best-effort du lead vers l'équipe commerciale
    # (Pipedrive + notification in-app + email). Ne lève jamais.
    await push_lead(
        db,
        name=payload.name,
        email=payload.email,
        company=payload.company,
        phone=payload.phone,
        message=payload.message,
        source="contact",
        details={
            "pol": payload.pol,
            "pod": payload.pod,
            "cargo_nature": payload.cargo_nature,
            "volume_weight": payload.volume_weight,
            "desired_dates": payload.desired_dates,
        },
    )
    return RedirectResponse(url="/contact/merci", status_code=303)


@router.get("/contact/merci", response_class=HTMLResponse)
async def contact_thanks(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/contact_merci.html", {"request": request})
