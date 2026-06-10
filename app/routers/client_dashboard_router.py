"""Client dashboard — once authenticated, the personal space.

Routes :
- /me              dashboard summary
- /me/bookings     list of bookings
- /me/bookings/{ref} detail
- /me/invoices     list of invoices
- /me/co2          CO2 certificates
- /me/account      profile + security (incl. MFA setup/verify/disable)
"""
from __future__ import annotations

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client
from app.config import settings
from app.database import get_db
from app.models.booking import Booking
from app.models.client_invoice import ClientInvoice
from app.models.anemos_certificate import AnemosCertificate
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
        select(func.coalesce(func.sum(AnemosCertificate.co2_avoided_kg), 0))
        .where(AnemosCertificate.client_account_id == client.id)
    )
    notif_unread = await notifications.count_unread(db, client_id=client.id)
    return templates.TemplateResponse(
        "client/dashboard.html",
        {
            "request": request,
            "client": client,
            "bookings": bookings,
            "active_count": active_count,
            "co2_avoided_kg": float(co2_avoided or 0),
            "notif_unread": notif_unread,
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
    return templates.TemplateResponse(
        "client/booking_detail.html",
        {"request": request, "client": client, "booking": booking, "messages": messages},
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
            db, booking_id=booking.id, sender="client",
            sender_name=client.company_name or client.email, body=body,
        )
        await notifications.notify_new_booking_message(
            db, booking_reference=booking.reference, booking_id=booking.id,
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
        vessels_json.append({
            "name": vessel.name,
            "code": vessel.code,
            "lat": position.latitude,
            "lon": position.longitude,
            "sog": float(position.sog_kn or 0),
            "cog": float(position.cog_deg or 0),
            "recorded_at": position.recorded_at.isoformat(),
        })

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
async def invoices(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    res = await db.execute(
        select(ClientInvoice)
        .where(ClientInvoice.client_account_id == client.id)
        .order_by(ClientInvoice.issued_at.desc())
    )
    return templates.TemplateResponse(
        "client/invoices.html",
        {"request": request, "client": client, "invoices": list(res.scalars().all())},
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
            content, file.filename or "document", subdir=f"bookings/{booking.id}",
        )
    except safe_files.UploadRejected as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    db.add(PackingListDocument(
        booking_id=booking.id, kind=kind, label=file.filename,
        file_path=rel_path, file_mime=mime, uploaded_by=client.email,
    ))
    await db.flush()
    await activity_record(
        db, action="client_doc_upload", user_name=client.email,
        module="cargo", entity_type="booking", entity_id=booking.id,
        entity_label=booking.reference, detail=kind,
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing")
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
        select(AnemosCertificate, Booking.reference)
        .join(Booking, Booking.id == AnemosCertificate.booking_id, isouter=True)
        .where(AnemosCertificate.client_account_id == client.id)
        .order_by(AnemosCertificate.issued_at.desc())
    )
    certificates = []
    for cert, booking_ref in res.all():
        cert.booking_ref = booking_ref
        certificates.append(cert)
    return templates.TemplateResponse(
        "client/anemos.html",
        {"request": request, "client": client, "certificates": certificates},
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
        {"request": request, "client": client,
         "qr_data_uri": qr, "otpauth_uri": uri, "secret": secret,
         "error": None},
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
            {"request": request, "client": client,
             "qr_data_uri": mfa.qr_data_uri(uri),
             "otpauth_uri": uri, "secret": client.mfa_secret,
             "error": "Code incorrect — réessayez."},
            status_code=400,
        )
    client.mfa_enabled = True
    await db.flush()
    # Génère 10 codes de récupération à afficher UNE seule fois
    recovery_codes = await mfa.generate_recovery_codes(
        db, owner_type="client", owner_id=client.id,
    )
    await activity_record(
        db, action="client_mfa_enabled", user_name=client.email,
        module="booking", entity_type="client_account",
        entity_id=client.id,
        ip_address=request.headers.get("x-forwarded-for")
                   or (request.client.host if request.client else None),
    )
    # On affiche les codes inline plutôt que par redirect (les redirect
    # 303 ne laissent pas passer de state — et on veut absolument que
    # l'utilisateur voie ces codes une fois).
    return templates.TemplateResponse(
        "client/mfa_recovery_codes.html",
        {"request": request, "client": client, "codes": recovery_codes,
         "is_regeneration": False},
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
            {"request": request, "client": client,
             "qr_data_uri": None, "otpauth_uri": None, "secret": None,
             "error": "Code TOTP incorrect — codes non régénérés."},
            status_code=400,
        )
    new_codes = await mfa.generate_recovery_codes(
        db, owner_type="client", owner_id=client.id,
    )
    await activity_record(
        db, action="client_mfa_codes_regen", user_name=client.email,
        module="booking", entity_type="client_account",
        entity_id=client.id,
        ip_address=request.headers.get("x-forwarded-for")
                   or (request.client.host if request.client else None),
    )
    return templates.TemplateResponse(
        "client/mfa_recovery_codes.html",
        {"request": request, "client": client, "codes": new_codes,
         "is_regeneration": True},
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
            {"request": request, "client": client,
             "qr_data_uri": None, "otpauth_uri": None, "secret": None,
             "error": "Code TOTP incorrect — MFA non désactivé."},
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
        db, action="client_mfa_disabled", user_name=client.email,
        module="booking", entity_type="client_account",
        entity_id=client.id, ip_address=ip,
    )
    await security_alerts.notify_mfa_disabled(
        to_email=client.email,
        recipient_name=client.contact_name or client.company_name or client.email,
        ip=ip, ua=ua,
    )
    return RedirectResponse(url="/me/account?mfa=disabled", status_code=303)
