"""Vitrine éditoriale publique — pages de conviction + demande de cotation.

Routes publiques (aucune authentification) :
- ``/flotte``      : « Notre flotte » (classe TSC 80, capacités, cales).
- ``/impact``      : environnement maîtrisé à bord, surveillance qualité, décarbonation.
- ``/preuves``     : méthode, vérification (EU MRV / THETIS-MRV), registre des certificats.
- ``/preuves/methodologie.pdf``          : méthodologie Anemos (PDF réel, facteurs courants).
- ``/preuves/rapport-annuel-exemple.pdf``: spécimen du rapport CO₂ annuel (données fictives).
- ``/verify``      : vérification publique d'un certificat Anemos (sans PII, rate-limitée).
- ``/navigation``  : courants, propulsion vélique, routes.
- ``/contact``     : coordonnées + formulaire de demande de cotation (GET/POST).
- ``/contact/merci``: accusé de réception.

Le formulaire ne réalise **aucun paiement** : il valide, journalise et
prépare le relais vers l'équipe commerciale (extranet de réservation).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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


# ── Téléchargements /preuves : méthodologie + spécimen (fin des liens factices,
# ENV-04/ECGT). PDF coûteux (WeasyPrint) → cache mémoire par clé de contenu,
# rate-limit IP en amont.
_PREUVES_PDF_RATE_SCOPE = "preuves_pdf"
_PREUVES_PDF_RATE_MAX = 20
_PREUVES_PDF_RATE_WINDOW_MIN = 10
_PREUVES_PDF_CACHE: dict[tuple, bytes] = {}

# Année de référence du spécimen de rapport annuel (données fictives, figées).
_SPECIMEN_YEAR = 2025


async def _preuves_pdf_rate_limit(request: Request, db: AsyncSession) -> None:
    ip = request.client.host if request.client else ""
    if await rate_limit.exceeded(
        db,
        scope=_PREUVES_PDF_RATE_SCOPE,
        identifier=ip,
        max_attempts=_PREUVES_PDF_RATE_MAX,
        window_minutes=_PREUVES_PDF_RATE_WINDOW_MIN,
    ):
        raise HTTPException(
            status_code=429, detail="Trop de requêtes — patientez quelques minutes."
        )
    await rate_limit.record(db, scope=_PREUVES_PDF_RATE_SCOPE, identifier=ip)


@router.get("/preuves/methodologie.pdf")
async def preuves_methodology_pdf(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Méthodologie Anemos — PDF public réel (fr/en), facteurs courants imprimés.

    Le document imprime les facteurs **versionnés en base** au moment de la
    génération (jamais des constantes marketing) : la clé de cache inclut les
    valeurs et la version des facteurs, un changement dans /admin/co2 produit
    donc un nouveau document.
    """
    await _preuves_pdf_rate_limit(request, db)
    from app.services import co2 as co2_svc
    from app.services import pdf_generator

    lang = "en" if getattr(request.state, "lang", "fr") == "en" else "fr"
    factors = await co2_svc.get_factors(db)
    key = (
        "methodologie",
        lang,
        str(factors.towt_ef_g_tkm),
        str(factors.conventional_ef_g_tkm),
        factors.source_version,
        pdf_generator.METHODOLOGY_DOC_VERSION,
    )
    pdf = _PREUVES_PDF_CACHE.get(key)
    if pdf is None:
        doc = pdf_generator.render_methodology(factors=factors, lang=lang)
        pdf = doc.pdf
        _PREUVES_PDF_CACHE[key] = pdf
    filename = f"NEWTOWT_Methodologie_Anemos_v{pdf_generator.METHODOLOGY_DOC_VERSION}_{lang}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "public, max-age=3600",
        },
    )


def _specimen_report() -> dict:
    """Rapport annuel fictif, déterministe et cohérent avec la formule publiée.

    Les émissions sont recalculées depuis les facteurs de référence (1,5 /
    13,7 g CO₂/t·km) pour que le spécimen soit exactement reproductible par un
    lecteur qui applique la méthodologie.
    """
    from datetime import UTC, datetime

    from app.services.co2 import CONV_CO2_EF_G_PER_TKM, NM_TO_KM, TOWT_CO2_EF_G_PER_TKM

    base_rows = [
        (
            "ANEMOS-EXEMPLE-0001",
            "BK-EXEMPLE-0001",
            "1ABRFR5",
            datetime(2025, 4, 18, tzinfo=UTC),
            Decimal("18.4"),
            Decimal("5150"),
            "declared",
        ),
        (
            "ANEMOS-EXEMPLE-0002",
            "BK-EXEMPLE-0002",
            "2ABRFR5",
            datetime(2025, 7, 9, tzinfo=UTC),
            Decimal("22.0"),
            Decimal("5150"),
            "declared",
        ),
        (
            "ANEMOS-EXEMPLE-0003",
            "BK-EXEMPLE-0003",
            "3ABRFR5",
            datetime(2025, 11, 2, tzinfo=UTC),
            Decimal("9.6"),
            Decimal("5150"),
            "theoretical",
        ),
    ]
    shipments = []
    tot_tonnage = Decimal("0")
    tot_distance = Decimal("0")
    tot_avoided = Decimal("0")
    tot_emitted = Decimal("0")
    tot_conventional = Decimal("0")
    declared = 0
    for ref, booking_ref, leg_code, issued_at, tonnage_t, distance_nm, method in base_rows:
        distance_km = distance_nm * NM_TO_KM
        emitted = (TOWT_CO2_EF_G_PER_TKM * tonnage_t * distance_km / 1000).quantize(Decimal("1"))
        conventional = (CONV_CO2_EF_G_PER_TKM * tonnage_t * distance_km / 1000).quantize(
            Decimal("1")
        )
        avoided = conventional - emitted
        if method == "declared":
            declared += 1
        shipments.append(
            {
                "reference": ref,
                "booking_ref": booking_ref,
                "leg_code": leg_code,
                "issued_at": issued_at,
                "tonnage_t": tonnage_t,
                "distance_nm": distance_nm,
                "co2_avoided_kg": avoided,
                "method": method,
            }
        )
        tot_tonnage += tonnage_t
        tot_distance += distance_nm
        tot_avoided += avoided
        tot_emitted += emitted
        tot_conventional += conventional
    return {
        "year": _SPECIMEN_YEAR,
        "shipments": shipments,
        "shipment_count": len(shipments),
        "declared_count": declared,
        "total_tonnage_t": tot_tonnage,
        "total_distance_nm": tot_distance,
        "total_avoided_kg": tot_avoided,
        "total_emitted_kg": tot_emitted,
        "total_conventional_kg": tot_conventional,
    }


@router.get("/preuves/rapport-annuel-exemple.pdf")
async def preuves_sample_annual_report_pdf(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Spécimen du rapport CO₂ annuel client — données fictives marquées SPÉCIMEN.

    Même moteur et même template que le vrai rapport
    (``/me/anemos/report/{year}.pdf``) : l'acheteur RSE voit exactement le
    document qu'il recevra.
    """
    await _preuves_pdf_rate_limit(request, db)
    key = ("rapport-exemple", _SPECIMEN_YEAR)
    pdf = _PREUVES_PDF_CACHE.get(key)
    if pdf is None:
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from weasyprint import HTML  # import tardif — deps natives lourdes

        from app.config import settings
        from app.templating import brand_for_lang

        client_stub = SimpleNamespace(
            company_name="Torréfaction Exemple SAS",
            vat_number="FR00 000 000 000",
            country="FR",
        )
        tpl = templates.get_template("pdf/anemos_annual_report.html")
        html = tpl.render(
            report=_specimen_report(),
            client=client_stub,
            site_url=settings.site_url,
            issued_at=datetime.now(UTC),
            specimen=True,
            # Rendu hors-requête : le context processor n'injecte pas ``brand``.
            brand=brand_for_lang("fr"),
        )
        pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
        _PREUVES_PDF_CACHE[key] = pdf
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="NEWTOWT_Rapport_CO2_annuel_SPECIMEN.pdf"',
            "Cache-Control": "public, max-age=3600",
        },
    )


# ── Kit presse réel (P5 — fin des placeholders de /presse) ──────────────────
_PRESS_LOGO_FILES = (
    "logo_NEWTOWT_web.png",
    "logo_NEWTOWT_web_dark.png",
    "logo_NEWTOWT_web_white.png",
    "logo_NEWTOWT_email.png",
    "logo_NEWTOWT_email_white.png",
)

_PRESS_LOGO_README = """NEWTOWT — pack logos presse
============================

Fichiers :
- logo_NEWTOWT_web.png         : usage écran, fond clair
- logo_NEWTOWT_web_dark.png    : usage écran, variante sombre
- logo_NEWTOWT_web_white.png   : usage écran, fond foncé / photo
- logo_NEWTOWT_email.png       : usage e-mail / petits formats
- logo_NEWTOWT_email_white.png : usage e-mail, fond foncé

Règles d'usage : ne pas déformer, recolorer ni détourer le logo ;
préfixe « NEW » en cuivre, wordmark teal (charte « Nouvelle Étoile »).
Crédit photo & demandes HD : voir /presse — contact média sur le site.
"""


@router.get("/presse/logos.zip")
async def presse_logos_zip(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Pack logos officiel (ZIP construit depuis les assets réels du site)."""
    await _preuves_pdf_rate_limit(request, db)
    key = ("presse-logos",)
    blob = _PREUVES_PDF_CACHE.get(key)
    if blob is None:
        import io
        import zipfile
        from pathlib import Path

        static_img = Path(__file__).resolve().parent.parent / "static" / "img"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("NEWTOWT_logos/LISEZMOI.txt", _PRESS_LOGO_README)
            for name in _PRESS_LOGO_FILES:
                path = static_img / name
                if path.exists():
                    zf.write(path, arcname=f"NEWTOWT_logos/{name}")
        blob = buf.getvalue()
        _PREUVES_PDF_CACHE[key] = blob
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="NEWTOWT_logos.zip"',
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/presse/dossier.pdf")
async def presse_dossier_pdf(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Dossier de presse — PDF réel généré depuis les faits vérifiés du site.

    Inclut les compteurs cumulés calculés en base (mêmes chiffres que la
    landing) : le dossier reste juste sans maintenance manuelle.
    """
    await _preuves_pdf_rate_limit(request, db)
    from app.services import social_proof

    counters = await social_proof.counters(db)
    key = (
        "dossier-presse",
        counters.pallets,
        counters.co2_avoided_kg,
        counters.crossings,
    )
    pdf = _PREUVES_PDF_CACHE.get(key)
    if pdf is None:
        from datetime import UTC, datetime

        from weasyprint import HTML  # import tardif — deps natives lourdes

        from app.config import settings
        from app.templating import brand_for_lang

        tpl = templates.get_template("pdf/dossier_presse.html")
        html = tpl.render(
            counters=counters,
            site_url=settings.site_url,
            issued_at=datetime.now(UTC),
            # Rendu hors-requête : le context processor n'injecte pas ``brand``.
            brand=brand_for_lang("fr"),
        )
        pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
        _PREUVES_PDF_CACHE[key] = pdf
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="NEWTOWT_dossier_de_presse.pdf"',
            "Cache-Control": "public, max-age=3600",
        },
    )


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


async def _lookup_certificate(db: AsyncSession, ref: str) -> AnemosCertificate | None:
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
