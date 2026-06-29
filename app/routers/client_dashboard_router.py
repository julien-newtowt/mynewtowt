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
from app.services import documents as documents_svc
from app.services import messaging, mfa, notifications, safe_files, security_alerts
from app.services.activity import record as activity_record
from app.services.booking import find_by_reference, list_for_client
from app.services.vessel_position import get_latest_position
from app.templating import templates

# Ordre des étapes de voyage pour la timeline de suivi.
_VOYAGE_STEPS = ("submitted", "confirmed", "loaded", "at_sea", "discharged", "delivered")

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
    """Page des Labels Anemos (anciennement "Certificats CO₂")."""
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

    tpl = templates.get_template("pdf/anemos_annual_report.html")
    html = tpl.render(
        report=report,
        client=client,
        site_url=settings.site_url,
        issued_at=datetime.now(UTC),
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
