"""Vitrine éditoriale publique — pages de conviction + demande de cotation.

Routes publiques (aucune authentification) :
- ``/flotte``      : « Notre flotte » (classe TSC 80, capacités, cales).
- ``/impact``      : environnement maîtrisé à bord, LACOE©, décarbonation.
- ``/navigation``  : courants, propulsion vélique, routes.
- ``/contact``     : coordonnées + formulaire de demande de cotation (GET/POST).
- ``/contact/merci``: accusé de réception.

Le formulaire ne réalise **aucun paiement** : il valide, journalise et
prépare le relais vers l'équipe commerciale (extranet de réservation).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.i18n import get_lang_from_request
from app.services import blog as blog_svc
from app.services import contact as contact_svc
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


@router.get("/navigation", response_class=HTMLResponse)
async def navigation(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/navigation.html", {"request": request})


@router.get("/recrutement", response_class=HTMLResponse)
async def recrutement(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/recrutement.html", {"request": request})


@router.get("/presse", response_class=HTMLResponse)
async def presse(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("public/presse.html", {"request": request})


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
