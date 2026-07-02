"""Client dashboard — once authenticated, the personal space.

Routes :
- /me              dashboard summary
- /me/bookings     list of bookings
- /me/bookings/{ref} detail
- /me/invoices     legacy 301 → /me/documents (facturation hors plateforme)
- /me/co2          CO2 certificates
- /me/account      profile + security (incl. MFA setup/verify/disable)
"""

from __future__ import annotations

import base64
import mimetypes
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client
from app.config import settings
from app.database import get_db
from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.leg import Leg
from app.models.notification import Notification
from app.models.packing_list import PackingListDocument
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import coffee_stories, messaging, mfa, notifications, safe_files, security_alerts
from app.services import documents as documents_svc
from app.services import hold_conditions as hold_conditions_svc
from app.services.activity import record as activity_record
from app.services.booking import find_by_reference, list_for_client
from app.services.vessel_position import get_latest_position
from app.templating import templates

# Ordre des étapes de voyage pour la timeline de suivi.
_VOYAGE_STEPS = ("submitted", "confirmed", "loaded", "at_sea", "discharged", "delivered")

# Statuts où la traversée a commencé : conditions de cale consultables,
# Carnet de Bord générable, page publique de voyage publiable.
_VOYAGE_STARTED = ("loaded", "at_sea", "discharged", "delivered")

router = APIRouter(tags=["client-dashboard"])


@router.get("/me", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    bookings = await list_for_client(db, client.id, limit=20)
    active_count = sum(
        1
        for b in bookings
        if b.status in ("submitted", "confirmed", "loaded", "at_sea", "discharged")
    )
    co2_avoided = await db.scalar(
        select(func.coalesce(func.sum(AnemosCertificate.co2_avoided_kg), 0)).where(
            AnemosCertificate.client_account_id == client.id
        )
    )
    notif_unread = await notifications.count_unread(db, client_id=client.id)
    # Alertes proactives affichées dès la connexion (retard / décalage ETA…).
    all_notifs = await notifications.list_for(db, client_id=client.id, limit=20)
    alert_items = [n for n in all_notifs if not n.is_read][:5]
    return templates.TemplateResponse(
        "client/dashboard.html",
        {
            "request": request,
            "client": client,
            "bookings": bookings,
            "active_count": active_count,
            "co2_avoided_kg": float(co2_avoided or 0),
            "notif_unread": notif_unread,
            "alert_items": alert_items,
        },
    )


@router.get("/me/notifications", response_class=HTMLResponse)
async def notifications_list(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    items = await notifications.list_for(db, client_id=client.id, limit=100)
    return templates.TemplateResponse(
        "client/notifications.html",
        {"request": request, "client": client, "notifications": items},
    )


@router.post("/me/notifications/{notif_id}/read")
async def notification_mark_read(
    notif_id: int,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    notif = await db.get(Notification, notif_id)
    if notif is not None and notif.target_client_id == client.id:
        await notifications.mark_read(db, notif)
    return RedirectResponse(url="/me/notifications", status_code=303)


@router.get("/me/bookings", response_class=HTMLResponse)
async def bookings_list(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    bookings = await list_for_client(db, client.id, limit=200)
    return templates.TemplateResponse(
        "client/bookings_list.html",
        {"request": request, "client": client, "bookings": bookings},
    )


@router.get("/me/bookings/{ref}", response_class=HTMLResponse)
async def booking_detail(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    messages = await messaging.list_for_booking(db, booking.id)
    await messaging.mark_thread_read(db, booking.id, reader="client")
    # Repérage à bord — uniquement les positions des lots de ce booking
    # (jamais l'occupation globale du navire — confidentialité inter-clients).
    from sqlalchemy import select

    from app.models.packing_list import PackingList
    from app.models.stowage import BLOCKS, DECKS, HOLDS
    from app.services.stowage import locate_for_packing_list

    pl_id = (
        await db.execute(select(PackingList.id).where(PackingList.booking_id == booking.id))
    ).scalar_one_or_none()
    positions = await locate_for_packing_list(db, pl_id) if pl_id else []
    # Conditions de transport (T°/H% de cale, relevés à bord) — la promesse
    # « surveillées en continu » devient consultable dès que le voyage a
    # commencé. Même agrégat que le portail expéditeur et la page publique.
    conditions = None
    if booking.status in _VOYAGE_STARTED:
        conditions = await hold_conditions_svc.for_leg(db, booking.leg_id)
    voyage_url = f"{settings.site_url.rstrip('/')}/voyage/{booking.reference}"
    return templates.TemplateResponse(
        "client/booking_detail.html",
        {
            "request": request,
            "client": client,
            "booking": booking,
            "messages": messages,
            "positions": positions,
            "target_zones": {p["zone"] for p in positions},
            "decks": DECKS,
            "holds": HOLDS,
            "blocks": BLOCKS,
            "conditions": conditions,
            "carnet_available": booking.status in _VOYAGE_STARTED,
            "voyage_url": voyage_url,
        },
    )


@router.post("/me/bookings/{ref}/messages")
async def post_message(
    ref: str,
    body: str = Form(...),
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.strip():
        await messaging.post(
            db,
            booking_id=booking.id,
            sender="client",
            sender_name=client.company_name or client.email,
            body=body,
        )
        await notifications.notify_new_booking_message(
            db,
            booking_reference=booking.reference,
            booking_id=booking.id,
        )
    return RedirectResponse(url=f"/me/bookings/{ref}#messages", status_code=303)


@router.post("/me/bookings/{ref}/voyage-public")
async def booking_voyage_public_toggle(
    ref: str,
    enabled: str = Form(""),
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Opt-in / opt-out de la page publique de voyage ``/voyage/{ref}``.

    C'est la destination du QR B2B2C imprimé sur le paquet : jamais publiée
    sans ce consentement explicite, dépubliable à tout moment. Tracé dans
    l'audit trail (donnée rendue publique / retirée).
    """
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    booking.voyage_public = enabled == "on"
    await db.flush()
    await activity_record(
        db,
        action="client_voyage_public_on" if booking.voyage_public else "client_voyage_public_off",
        user_name=client.email,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
    )
    return RedirectResponse(url=f"/me/bookings/{ref}?voyage_saved=1", status_code=303)


@router.get("/me/bookings/{ref}/carnet.pdf")
async def booking_carnet_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Carnet de Bord de la traversée (PDF personnalisé pour ce client).

    Dossier de preuve narratif : trace GPS, conditions de cale, performance
    environnementale, photos. Disponible dès que le voyage a commencé.
    """
    from app.services.carnet_bord import generate_carnet_bord_pdf

    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if booking.status not in _VOYAGE_STARTED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # client_view=True : masque la ventilation cargo des co-chargeurs du leg
    # (confidentialité inter-clients — cf. revue sécurité).
    pdf_bytes = await generate_carnet_bord_pdf(
        db, booking.leg_id, client_account_id=client.id, client_view=True
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="CarnetBord_{booking.reference}.pdf"'},
    )


@router.get("/me/messages", response_class=HTMLResponse)
async def messages_overview(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    bookings = await list_for_client(db, client.id, limit=200)
    threads = []
    for b in bookings:
        msgs = await messaging.list_for_booking(db, b.id)
        if msgs:
            unread = sum(1 for m in msgs if m.sender == "staff" and not m.is_read)
            threads.append({"booking": b, "last": msgs[-1], "count": len(msgs), "unread": unread})
    return templates.TemplateResponse(
        "client/messages.html",
        {"request": request, "client": client, "threads": threads},
    )


@router.get("/me/track/{ref}", response_class=HTMLResponse)
async def track(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Suivi de traversée — position live du navire + timeline de statut."""
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    leg = await db.get(Leg, booking.leg_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None
    position = await get_latest_position(db, vessel.id) if vessel else None

    # Timeline : chaque étape avec son horodatage, état done/current.
    current_idx = _VOYAGE_STEPS.index(booking.status) if booking.status in _VOYAGE_STEPS else -1
    timeline = [
        {
            "key": key,
            "at": getattr(booking, f"{key}_at", None),
            "done": current_idx >= idx >= 0,
            "current": key == booking.status,
        }
        for idx, key in enumerate(_VOYAGE_STEPS)
    ]

    # Données carte (réutilise le même format que fleet-map.js).
    vessels_json: list[dict] = []
    if position is not None and vessel is not None:
        vessels_json.append(
            {
                "name": vessel.name,
                "code": vessel.code,
                "lat": position.latitude,
                "lon": position.longitude,
                "sog": float(position.sog_kn or 0),
                "cog": float(position.cog_deg or 0),
                "recorded_at": position.recorded_at.isoformat(),
            }
        )

    # Centre la carte sur le milieu de la route si coords connues.
    map_center = [-30, 40]
    if pol and pod and pol.latitude is not None and pod.latitude is not None:
        map_center = [
            round((pol.longitude + pod.longitude) / 2, 3),
            round((pol.latitude + pod.latitude) / 2, 3),
        ]

    return templates.TemplateResponse(
        "client/track.html",
        {
            "request": request,
            "client": client,
            "booking": booking,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "position": position,
            "timeline": timeline,
            "vessels_json": vessels_json,
            "map_center": map_center,
            "maptiler_token": settings.map_token,
        },
    )


@router.get("/me/invoices", response_class=HTMLResponse)
async def invoices_page(
    request: Request,
    client=Depends(get_current_client),
) -> HTMLResponse:
    """EVO-01 / arbitrage A5 — la facturation est émise par la comptabilité
    hors plateforme (virement bancaire). Page explicite remplaçant l'ancien
    301 silencieux vers /me/documents. Le modèle ClientInvoice reste inactif
    (non listé) ; le service ``invoicing`` n'est conservé que pour le calcul
    des montants (booking/Anemos)."""
    return templates.TemplateResponse(
        "client/invoices.html",
        {"request": request, "client": client},
    )


@router.get("/me/documents", response_class=HTMLResponse)
async def documents_hub(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    groups = await documents_svc.list_for_client(db, client.id)
    return templates.TemplateResponse(
        "client/documents.html",
        {"request": request, "client": client, "groups": groups},
    )


_CLIENT_DOC_KINDS = ("customs", "msds", "other")


@router.post("/me/bookings/{ref}/documents")
async def upload_document(
    ref: str,
    kind: str = Form("other"),
    file: UploadFile = File(...),
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if kind not in _CLIENT_DOC_KINDS:
        kind = "other"
    content = await file.read()
    try:
        rel_path, mime = safe_files.save_upload(
            content,
            file.filename or "document",
            subdir=f"bookings/{booking.id}",
        )
    except safe_files.UploadRejected as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    db.add(
        PackingListDocument(
            booking_id=booking.id,
            kind=kind,
            label=file.filename,
            file_path=rel_path,
            file_mime=mime,
            uploaded_by=client.email,
        )
    )
    await db.flush()
    await activity_record(
        db,
        action="client_doc_upload",
        user_name=client.email,
        module="cargo",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
        detail=kind,
    )
    return RedirectResponse(url="/me/documents", status_code=303)


@router.get("/me/bookings/{ref}/documents/{doc_id}")
async def download_document(
    ref: str,
    doc_id: int,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    doc = await db.get(PackingListDocument, doc_id)
    if not doc or doc.booking_id != booking.id or not doc.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        path = safe_files.resolve_path(doc.file_path)
    except (safe_files.UploadRejected, FileNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing") from None
    return Response(
        content=path.read_bytes(),
        media_type=doc.file_mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc.label or path.name}"'},
    )


@router.get("/me/anemos", response_class=HTMLResponse)
async def anemos(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Page des certificats Anemos (anciennement "Certificats CO₂")."""
    res = await db.execute(
        select(AnemosCertificate, Booking)
        .join(Booking, Booking.id == AnemosCertificate.booking_id, isouter=True)
        .where(AnemosCertificate.client_account_id == client.id)
        .order_by(AnemosCertificate.issued_at.desc())
    )
    from app.models.leg import Leg
    from app.services.booking_timeline import build_shipment_timeline

    rows = res.all()
    # Pré-charge les legs des bookings (anti N+1) pour les ATD/ATA de la timeline.
    leg_ids = {b.leg_id for _c, b in rows if b is not None and b.leg_id}
    legs = (
        {
            li.id: li
            for li in (await db.execute(select(Leg).where(Leg.id.in_(leg_ids)))).scalars().all()
        }
        if leg_ids
        else {}
    )
    certificates = []
    for cert, booking in rows:
        cert.booking_ref = booking.reference if booking is not None else None
        leg = legs.get(booking.leg_id) if booking is not None else None
        cert.timeline = build_shipment_timeline(booking, leg) if booking is not None else []
        certificates.append(cert)
    from app.services import anemos as anemos_svc

    report_years = await anemos_svc.available_report_years(db, client.id)
    return templates.TemplateResponse(
        "client/anemos.html",
        {
            "request": request,
            "client": client,
            "certificates": certificates,
            "report_years": report_years,
        },
    )


@router.get("/me/anemos/report/{year}.pdf")
async def anemos_annual_report_pdf(
    year: int,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rapport RSE annuel consolidé (ENV-06) — PDF téléchargeable."""
    from app.services import anemos as anemos_svc

    report = await anemos_svc.annual_report(db, client_account_id=client.id, year=year)
    if report["shipment_count"] == 0:
        raise HTTPException(status_code=404, detail="Aucune expédition pour cette année")

    from weasyprint import HTML  # import tardif — deps natives lourdes

    from app.templating import brand_for_lang

    tpl = templates.get_template("pdf/anemos_annual_report.html")
    html = tpl.render(
        report=report,
        client=client,
        site_url=settings.site_url,
        issued_at=datetime.now(UTC),
        # Rendu hors-requête : le context processor n'injecte pas ``brand``
        # (le pied de page @page de pdf/_base.html en dépend) — défaut latent
        # corrigé à l'occasion de P3.
        brand=brand_for_lang("fr"),
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="rapport-co2-{client.id}-{year}.pdf"'},
    )


@router.get("/me/anemos/report/{year}.csv")
async def anemos_annual_report_csv(
    year: int,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rapport RSE annuel (ENV-06) — export CSV pour intégration Bilan Carbone®."""
    import csv
    import io

    from app.services import anemos as anemos_svc

    report = await anemos_svc.annual_report(db, client_account_id=client.id, year=year)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "reference",
            "booking",
            "leg",
            "issued_at",
            "tonnage_t",
            "distance_nm",
            "co2_avoided_kg",
            "method",
        ]
    )
    for s in report["shipments"]:
        w.writerow(
            [
                s["reference"],
                s["booking_ref"] or "",
                s["leg_code"] or "",
                s["issued_at"].date() if s["issued_at"] else "",
                s["tonnage_t"],
                s["distance_nm"],
                s["co2_avoided_kg"],
                s["method"] or "",
            ]
        )
    w.writerow([])
    w.writerow(
        [
            "TOTAL",
            "",
            "",
            year,
            report["total_tonnage_t"],
            report["total_distance_nm"],
            report["total_avoided_kg"],
            "",
        ]
    )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="rapport-co2-{client.id}-{year}.csv"'
        },
    )


@router.get("/me/co2")
async def co2_redirect_legacy() -> RedirectResponse:
    """Backward-compat : anciens bookmarks /me/co2 → 301 /me/anemos."""
    return RedirectResponse(url="/me/anemos", status_code=301)


@router.get("/me/account", response_class=HTMLResponse)
async def account(
    request: Request,
    client=Depends(get_current_client),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "client/account.html",
        {"request": request, "client": client},
    )


# ─────────────────────────────────────────────────────────────────────
#                    MFA TOTP — setup / verify / disable
# ─────────────────────────────────────────────────────────────────────


@router.get("/me/account/mfa", response_class=HTMLResponse)
async def mfa_setup_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
) -> HTMLResponse:
    """Page de configuration MFA — affiche QR + secret si non encore activé."""
    qr = None
    uri = None
    secret = None
    if not client.mfa_enabled:
        # Si pas de secret, on en génère un (mais on ne marque pas
        # mfa_enabled=True tant que l'utilisateur n'a pas validé un 1er
        # code → anti-lock-out).
        if not client.mfa_secret:
            client.mfa_secret = mfa.generate_secret()
            await db.flush()
        secret = client.mfa_secret
        uri = mfa.provisioning_uri(secret, client.email)
        qr = mfa.qr_data_uri(uri)
    return templates.TemplateResponse(
        "client/mfa_setup.html",
        {
            "request": request,
            "client": client,
            "qr_data_uri": qr,
            "otpauth_uri": uri,
            "secret": secret,
            "error": None,
        },
    )


@router.post("/me/account/mfa/verify", response_class=HTMLResponse)
async def mfa_verify(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
):
    """Vérifie le 1er code TOTP — si OK, active mfa_enabled."""
    if client.mfa_enabled:
        return RedirectResponse(url="/me/account/mfa", status_code=303)
    if not client.mfa_secret:
        return RedirectResponse(url="/me/account/mfa", status_code=303)
    if not mfa.verify_totp(client.mfa_secret, code):
        # Réaffiche le QR pour ré-essayer (le secret n'a pas changé).
        uri = mfa.provisioning_uri(client.mfa_secret, client.email)
        return templates.TemplateResponse(
            "client/mfa_setup.html",
            {
                "request": request,
                "client": client,
                "qr_data_uri": mfa.qr_data_uri(uri),
                "otpauth_uri": uri,
                "secret": client.mfa_secret,
                "error": "Code incorrect — réessayez.",
            },
            status_code=400,
        )
    client.mfa_enabled = True
    await db.flush()
    # Génère 10 codes de récupération à afficher UNE seule fois
    recovery_codes = await mfa.generate_recovery_codes(
        db,
        owner_type="client",
        owner_id=client.id,
    )
    await activity_record(
        db,
        action="client_mfa_enabled",
        user_name=client.email,
        module="booking",
        entity_type="client_account",
        entity_id=client.id,
        ip_address=request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else None),
    )
    # On affiche les codes inline plutôt que par redirect (les redirect
    # 303 ne laissent pas passer de state — et on veut absolument que
    # l'utilisateur voie ces codes une fois).
    return templates.TemplateResponse(
        "client/mfa_recovery_codes.html",
        {"request": request, "client": client, "codes": recovery_codes, "is_regeneration": False},
    )


@router.post("/me/account/mfa/regenerate", response_class=HTMLResponse)
async def mfa_regenerate_codes(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
):
    """Régénère les 10 codes de récupération — exige un TOTP valide."""
    if not client.mfa_enabled or not client.mfa_secret:
        return RedirectResponse(url="/me/account/mfa", status_code=303)
    if not mfa.verify_totp(client.mfa_secret, code):
        return templates.TemplateResponse(
            "client/mfa_setup.html",
            {
                "request": request,
                "client": client,
                "qr_data_uri": None,
                "otpauth_uri": None,
                "secret": None,
                "error": "Code TOTP incorrect — codes non régénérés.",
            },
            status_code=400,
        )
    new_codes = await mfa.generate_recovery_codes(
        db,
        owner_type="client",
        owner_id=client.id,
    )
    await activity_record(
        db,
        action="client_mfa_codes_regen",
        user_name=client.email,
        module="booking",
        entity_type="client_account",
        entity_id=client.id,
        ip_address=request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else None),
    )
    return templates.TemplateResponse(
        "client/mfa_recovery_codes.html",
        {"request": request, "client": client, "codes": new_codes, "is_regeneration": True},
    )


@router.post("/me/account/mfa/disable", response_class=HTMLResponse)
async def mfa_disable(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
):
    """Désactive MFA — exige un code TOTP valide (anti-takeover de session)."""
    if not client.mfa_enabled or not client.mfa_secret:
        return RedirectResponse(url="/me/account/mfa", status_code=303)
    if not mfa.verify_totp(client.mfa_secret, code):
        return templates.TemplateResponse(
            "client/mfa_setup.html",
            {
                "request": request,
                "client": client,
                "qr_data_uri": None,
                "otpauth_uri": None,
                "secret": None,
                "error": "Code TOTP incorrect — MFA non désactivé.",
            },
            status_code=400,
        )
    client.mfa_enabled = False
    client.mfa_secret = None
    await db.flush()
    # Purge les codes de récupération restants (ils sont liés à ce secret)
    from sqlalchemy import delete

    from app.models.mfa_recovery_code import MfaRecoveryCode

    await db.execute(
        delete(MfaRecoveryCode)
        .where(MfaRecoveryCode.owner_type == "client")
        .where(MfaRecoveryCode.owner_id == client.id)
    )
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    ua = request.headers.get("user-agent")
    await activity_record(
        db,
        action="client_mfa_disabled",
        user_name=client.email,
        module="booking",
        entity_type="client_account",
        entity_id=client.id,
        ip_address=ip,
    )
    await security_alerts.notify_mfa_disabled(
        to_email=client.email,
        recipient_name=client.contact_name or client.company_name or client.email,
        ip=ip,
        ua=ua,
    )
    return RedirectResponse(url="/me/account?mfa=disabled", status_code=303)


# ─────────────────────────── Vague 3 — kit B2B2C ───────────────────────────
# Espace marque (co-branding) + pack par expédition assemblant le récit
# d'origine, la dataviz CO₂ (vrai CO₂ évité + QR /verify) et le certificat.

_LOGO_MIME_OK = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}


@router.get("/me/brand", response_class=HTMLResponse)
async def brand_space(
    request: Request,
    client=Depends(get_current_client),
) -> HTMLResponse:
    """Espace marque : nom + logo du client, repris pour co-brander le kit."""
    return templates.TemplateResponse(
        "client/brand.html",
        {"request": request, "client": client},
    )


@router.post("/me/brand")
async def brand_save(
    request: Request,
    brand_name: str = Form(""),
    logo: UploadFile | None = File(None),
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    client.brand_name = (brand_name or "").strip()[:120] or None
    if logo is not None and getattr(logo, "filename", ""):
        content = await logo.read()
        try:
            rel_path, mime = safe_files.save_upload(
                content, logo.filename or "logo", subdir=f"brand/{client.id}"
            )
        except safe_files.UploadRejected as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        if mime and mime not in _LOGO_MIME_OK:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Logo : formats acceptés PNG, JPEG, SVG ou WebP.",
            )
        client.brand_logo_path = rel_path
    await db.flush()
    await activity_record(
        db,
        action="client_brand_update",
        user_name=client.email,
        module="booking",
        entity_type="client_account",
        entity_id=client.id,
        entity_label=client.company_name,
    )
    return RedirectResponse(url="/me/brand?saved=1", status_code=303)


@router.post("/me/brand/logo/delete")
async def brand_logo_delete(
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    client.brand_logo_path = None
    await db.flush()
    return RedirectResponse(url="/me/brand?saved=1", status_code=303)


@router.get("/me/brand/logo")
async def brand_logo(
    client=Depends(get_current_client),
) -> Response:
    """Sert le logo de marque du client (pour <img>)."""
    if not client.brand_logo_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No logo")
    try:
        path = safe_files.resolve_path(client.brand_logo_path)
    except (safe_files.UploadRejected, FileNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing") from None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(content=path.read_bytes(), media_type=mime)


@router.get("/me/bookings/{ref}/kit", response_class=HTMLResponse)
async def booking_kit(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Pack par expédition : récit d'origine + dataviz CO₂ + QR + certificat,
    co-brandé avec la marque du client. Le terroir (origine/région/producteur)
    est éditable ici ; le CO₂ reste celui, immuable, du certificat."""
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    cert = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    leg = await db.get(Leg, booking.leg_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    lang = getattr(request.state, "lang", "fr")

    co2_kg = int(cert.co2_avoided_kg) if cert and cert.co2_avoided_kg else None
    verify_url = f"{settings.site_url.rstrip('/')}/verify/{cert.reference}" if cert else None
    # QR du kit : pointe vers la page publique de voyage quand elle est
    # publiée (l'histoire complète), sinon vers la vérification du certificat.
    voyage_url = (
        f"{settings.site_url.rstrip('/')}/voyage/{booking.reference}"
        if booking.voyage_public
        else None
    )
    share_url = voyage_url or verify_url
    qr = mfa.qr_data_uri(share_url) if share_url else None

    origin = (
        booking.coffee_origin if coffee_stories.is_valid_origin(booking.coffee_origin) else None
    )
    story_long = story_short = None
    if origin:
        story_long = coffee_stories.render_story(
            origin,
            lang,
            "long",
            region=booking.coffee_region,
            producer=booking.coffee_producer,
            vessel=vessel.name if vessel else None,
            co2_kg=co2_kg,
        )
        story_short = coffee_stories.render_story(origin, lang, "short", co2_kg=co2_kg)

    return templates.TemplateResponse(
        "client/kit.html",
        {
            "request": request,
            "client": client,
            "booking": booking,
            "cert": cert,
            "vessel": vessel,
            "co2_kg": co2_kg,
            "verify_url": verify_url,
            "voyage_url": voyage_url,
            "share_url": share_url,
            "co2eq_qr": qr,
            "co2eq_verify_url": verify_url,
            "co2eq_value": co2_kg or 250,
            "origin": origin,
            "story_long": story_long,
            "story_short": story_short,
            "origins": coffee_stories.ORIGINS,
            "origin_label": coffee_stories.origin_label,
        },
    )


@router.post("/me/bookings/{ref}/kit")
async def booking_kit_save(
    ref: str,
    coffee_origin: str = Form(""),
    coffee_region: str = Form(""),
    coffee_producer: str = Form(""),
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Enregistre le terroir café (self-service client) sur la réservation."""
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    o = (coffee_origin or "").strip().lower()
    booking.coffee_origin = o if coffee_stories.is_valid_origin(o) else None
    booking.coffee_region = (coffee_region or "").strip()[:120] or None
    booking.coffee_producer = (coffee_producer or "").strip()[:160] or None
    await db.flush()
    # Analytics B2B2C : le client a renseigné/généré le kit co-brandé.
    from app.services import analytics

    await analytics.record(db, "kit_generated", reference=booking.reference, channel="client")
    return RedirectResponse(url=f"/me/bookings/{ref}/kit?saved=1", status_code=303)


def _brand_logo_data_uri(client) -> str | None:
    """Logo de marque du client encodé en data: URI (pour l'embarquer dans le PDF)."""
    if not client.brand_logo_path:
        return None
    try:
        path = safe_files.resolve_path(client.brand_logo_path)
    except (safe_files.UploadRejected, FileNotFoundError):
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


@router.get("/me/bookings/{ref}/kit.pdf")
async def booking_kit_pdf(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Kit B2B2C par expédition (PDF co-brandé téléchargeable)."""
    from app.services import pdf_generator

    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    cert = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    leg = await db.get(Leg, booking.leg_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None
    lang = getattr(request.state, "lang", "fr")

    co2_kg = int(cert.co2_avoided_kg) if cert and cert.co2_avoided_kg else None
    origin = (
        booking.coffee_origin if coffee_stories.is_valid_origin(booking.coffee_origin) else None
    )
    story_long = story_short = None
    if origin:
        story_long = coffee_stories.render_story(
            origin,
            lang,
            "long",
            region=booking.coffee_region,
            producer=booking.coffee_producer,
            vessel=vessel.name if vessel else None,
            co2_kg=co2_kg,
        )
        story_short = coffee_stories.render_story(origin, lang, "short", co2_kg=co2_kg)

    voyage_url = (
        f"{settings.site_url.rstrip('/')}/voyage/{booking.reference}"
        if booking.voyage_public
        else None
    )
    doc = pdf_generator.render_kit(
        booking=booking,
        leg=leg,
        vessel=vessel,
        pol=pol,
        pod=pod,
        client=client,
        cert=cert,
        lang=lang,
        co2_kg=co2_kg,
        story_long=story_long,
        story_short=story_short,
        client_logo_data=_brand_logo_data_uri(client),
        share_url=voyage_url,
    )
    # Analytics B2B2C : téléchargement du kit co-brandé (PDF par expédition).
    from app.services import analytics

    await analytics.record(db, "kit_download", reference=booking.reference, channel="client")
    return Response(
        content=doc.pdf,
        media_type=doc.mime,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )
