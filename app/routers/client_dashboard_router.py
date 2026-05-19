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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthRequired, get_current_client
from app.database import get_db
from app.models.booking import Booking
from app.models.client_invoice import ClientInvoice
from app.models.co2_certificate import CO2Certificate
from app.services import mfa, security_alerts
from app.services.activity import record as activity_record
from app.services.booking import find_by_reference, list_for_client
from app.templating import templates

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
        select(func.coalesce(func.sum(CO2Certificate.co2_avoided_kg), 0))
        .where(CO2Certificate.client_account_id == client.id)
    )
    return templates.TemplateResponse(
        "client/dashboard.html",
        {
            "request": request,
            "client": client,
            "bookings": bookings,
            "active_count": active_count,
            "co2_avoided_kg": float(co2_avoided or 0),
        },
    )


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
    return templates.TemplateResponse(
        "client/booking_detail.html",
        {"request": request, "client": client, "booking": booking},
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


@router.get("/me/co2", response_class=HTMLResponse)
async def co2(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    res = await db.execute(
        select(CO2Certificate)
        .where(CO2Certificate.client_account_id == client.id)
        .order_by(CO2Certificate.issued_at.desc())
    )
    return templates.TemplateResponse(
        "client/co2.html",
        {"request": request, "client": client, "certificates": list(res.scalars().all())},
    )


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
