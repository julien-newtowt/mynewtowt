"""WebAuthn / Passkey routes — management (register/list/delete) + login challenge.

Deux contextes parallèles : ``/me/account/webauthn/*`` pour les clients
et ``/admin/my-account/webauthn/*`` pour le staff. Les routes de login
challenge sont ``/me/login/webauthn/*`` (client) et ``/login/webauthn/*``
(staff).

Toutes les routes JSON acceptent le CSRF token en header ``x-csrf-token``
(le JS bridge ``app/static/js/webauthn.js`` le pose automatiquement).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    STAFF_COOKIE,
    STAFF_MFA_PENDING_COOKIE,
    CLIENT_COOKIE,
    CLIENT_MFA_PENDING_COOKIE,
    cookie_kwargs_for_client,
    cookie_kwargs_for_staff,
    create_client_session,
    create_staff_session,
    decode_client_mfa_pending,
    decode_staff_mfa_pending,
    get_current_client,
    get_current_staff,
)
from app.config import settings
from app.database import get_db
from app.models.client_account import ClientAccount
from app.models.user import User
from app.models.webauthn_credential import WebAuthnCredential
from app.services import device_detection, security_alerts, webauthn_service as wa
from app.services.activity import record as activity_record
from app.templating import templates


router = APIRouter(tags=["webauthn"])


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)


def _expected_origin(request: Request) -> str:
    """Origin attendue pour les attestations WebAuthn.

    Préfère SITE_URL config si défini (cas prod derrière reverse proxy
    qui réécrit le Host), sinon reconstruit depuis la requête.
    """
    if settings.site_url and settings.site_url.startswith(("http://", "https://")):
        return settings.site_url.rstrip("/")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}"


# ─────────────────────────────────────────────────────────────────────
#                          MANAGEMENT — CLIENT
# ─────────────────────────────────────────────────────────────────────


@router.get("/me/account/webauthn", response_class=HTMLResponse)
async def client_webauthn_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
) -> HTMLResponse:
    creds = list((await db.execute(
        select(WebAuthnCredential)
        .where(WebAuthnCredential.owner_type == "client")
        .where(WebAuthnCredential.owner_id == client.id)
        .order_by(WebAuthnCredential.created_at.desc())
    )).scalars().all())
    return templates.TemplateResponse(
        "client/webauthn_list.html",
        {"request": request, "client": client, "credentials": creds},
    )


@router.post("/me/account/webauthn/register/options")
async def client_webauthn_register_options(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
):
    options_json, challenge = await wa.begin_registration(
        db,
        owner_type="client",
        owner_id=client.id,
        user_name=client.email,
        user_display_name=client.contact_name or client.email,
    )
    token = wa.sign_challenge(challenge, owner_type="client", owner_id=client.id)
    resp = Response(content=options_json, media_type="application/json")
    resp.set_cookie(
        value=token,
        **wa.cookie_kwargs_for_challenge(
            wa.CHALLENGE_COOKIE_REG, secure=request.url.scheme == "https",
        ),
    )
    return resp


@router.post("/me/account/webauthn/register/verify")
async def client_webauthn_register_verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
):
    body = await request.json()
    cookie_tok = request.cookies.get(wa.CHALLENGE_COOKIE_REG)
    payload = wa.read_challenge(cookie_tok or "")
    if not payload or payload.get("ot") != "client" or payload.get("oi") != client.id:
        raise HTTPException(status_code=400, detail="Challenge invalide ou expiré")
    challenge = wa.b64url_decode(payload["ch"])
    name = (body.get("name") or "").strip()
    try:
        cred = await wa.complete_registration(
            db,
            owner_type="client", owner_id=client.id,
            challenge=challenge,
            credential_json=json.dumps(body["credential"]),
            expected_origin=_expected_origin(request),
            name=name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_record(
        db, action="client_webauthn_register",
        user_name=client.email, module="booking",
        entity_type="webauthn_credential", entity_id=cred.id,
        entity_label=name or "(sans nom)",
        ip_address=_client_ip(request),
    )
    await security_alerts.notify_passkey_added(
        to_email=client.email,
        recipient_name=client.contact_name or client.company_name or client.email,
        passkey_label=name or None,
        ip=_client_ip(request), ua=request.headers.get("user-agent"),
    )
    resp = JSONResponse(
        {"ok": True, "credential_id": cred.id, "name": cred.name}
    )
    resp.delete_cookie(wa.CHALLENGE_COOKIE_REG, path="/")
    return resp


@router.post("/me/account/webauthn/{cred_id}/delete")
async def client_webauthn_delete(
    cred_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    client=Depends(get_current_client),
):
    cred = await db.get(WebAuthnCredential, cred_id)
    if cred is None or cred.owner_type != "client" or cred.owner_id != client.id:
        raise HTTPException(status_code=404)
    label = cred.name or cred.credential_id[:12]
    await db.delete(cred)
    await db.flush()
    await activity_record(
        db, action="client_webauthn_delete",
        user_name=client.email, module="booking",
        entity_type="webauthn_credential", entity_id=cred_id,
        entity_label=label,
        ip_address=_client_ip(request),
    )
    await security_alerts.notify_passkey_deleted(
        to_email=client.email,
        recipient_name=client.contact_name or client.company_name or client.email,
        passkey_label=label,
        ip=_client_ip(request), ua=request.headers.get("user-agent"),
    )
    return RedirectResponse(url="/me/account/webauthn", status_code=303)


# ─────────────────────────────────────────────────────────────────────
#                          MANAGEMENT — STAFF
# ─────────────────────────────────────────────────────────────────────


@router.get("/admin/my-account/webauthn", response_class=HTMLResponse)
async def staff_webauthn_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> HTMLResponse:
    creds = list((await db.execute(
        select(WebAuthnCredential)
        .where(WebAuthnCredential.owner_type == "staff")
        .where(WebAuthnCredential.owner_id == user.id)
        .order_by(WebAuthnCredential.created_at.desc())
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/admin/webauthn_list.html",
        {"request": request, "user": user, "credentials": creds},
    )


@router.post("/admin/my-account/webauthn/register/options")
async def staff_webauthn_register_options(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    options_json, challenge = await wa.begin_registration(
        db,
        owner_type="staff",
        owner_id=user.id,
        user_name=user.username,
        user_display_name=user.full_name or user.username,
    )
    token = wa.sign_challenge(challenge, owner_type="staff", owner_id=user.id)
    resp = Response(content=options_json, media_type="application/json")
    resp.set_cookie(
        value=token,
        **wa.cookie_kwargs_for_challenge(
            wa.CHALLENGE_COOKIE_REG, secure=request.url.scheme == "https",
        ),
    )
    return resp


@router.post("/admin/my-account/webauthn/register/verify")
async def staff_webauthn_register_verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    body = await request.json()
    cookie_tok = request.cookies.get(wa.CHALLENGE_COOKIE_REG)
    payload = wa.read_challenge(cookie_tok or "")
    if not payload or payload.get("ot") != "staff" or payload.get("oi") != user.id:
        raise HTTPException(status_code=400, detail="Challenge invalide ou expiré")
    challenge = wa.b64url_decode(payload["ch"])
    name = (body.get("name") or "").strip()
    try:
        cred = await wa.complete_registration(
            db,
            owner_type="staff", owner_id=user.id,
            challenge=challenge,
            credential_json=json.dumps(body["credential"]),
            expected_origin=_expected_origin(request),
            name=name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_record(
        db, action="staff_webauthn_register",
        user_id=user.id, user_name=user.username, user_role=user.role,
        module="admin", entity_type="webauthn_credential",
        entity_id=cred.id, entity_label=name or "(sans nom)",
        ip_address=_client_ip(request),
    )
    if user.email:
        await security_alerts.notify_passkey_added(
            to_email=user.email,
            recipient_name=user.full_name or user.username,
            passkey_label=name or None,
            ip=_client_ip(request), ua=request.headers.get("user-agent"),
        )
    resp = JSONResponse(
        {"ok": True, "credential_id": cred.id, "name": cred.name}
    )
    resp.delete_cookie(wa.CHALLENGE_COOKIE_REG, path="/")
    return resp


@router.post("/admin/my-account/webauthn/{cred_id}/delete")
async def staff_webauthn_delete(
    cred_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    cred = await db.get(WebAuthnCredential, cred_id)
    if cred is None or cred.owner_type != "staff" or cred.owner_id != user.id:
        raise HTTPException(status_code=404)
    label = cred.name or cred.credential_id[:12]
    await db.delete(cred)
    await db.flush()
    await activity_record(
        db, action="staff_webauthn_delete",
        user_id=user.id, user_name=user.username, user_role=user.role,
        module="admin", entity_type="webauthn_credential",
        entity_id=cred_id, entity_label=label,
        ip_address=_client_ip(request),
    )
    if user.email:
        await security_alerts.notify_passkey_deleted(
            to_email=user.email,
            recipient_name=user.full_name or user.username,
            passkey_label=label,
            ip=_client_ip(request), ua=request.headers.get("user-agent"),
        )
    return RedirectResponse(url="/admin/my-account/webauthn", status_code=303)


# ─────────────────────────────────────────────────────────────────────
#                  LOGIN — passwordless passkey (CLIENT)
# ─────────────────────────────────────────────────────────────────────


def _parse_user_handle(handle_b64url: str) -> tuple[str, int] | None:
    """``client:42`` → ("client", 42). Renvoie None si malformé."""
    try:
        raw = wa.b64url_decode(handle_b64url).decode("utf-8")
        ot, oi = raw.split(":", 1)
        return (ot, int(oi))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None


@router.post("/me/login/webauthn/options")
async def client_login_webauthn_options(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Options de challenge pour login client passwordless (resident keys)."""
    options_json, challenge = await wa.begin_authentication(
        db, owner_type=None, owner_id=None,
    )
    # owner_id=None car passwordless — résolu via userHandle au verify
    token = wa.sign_challenge(challenge, owner_type="client_login", owner_id=None)
    resp = Response(content=options_json, media_type="application/json")
    resp.set_cookie(
        value=token,
        **wa.cookie_kwargs_for_challenge(
            wa.CHALLENGE_COOKIE_AUTH, secure=request.url.scheme == "https",
        ),
    )
    return resp


@router.post("/me/login/webauthn/verify")
async def client_login_webauthn_verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    cookie_tok = request.cookies.get(wa.CHALLENGE_COOKIE_AUTH)
    payload = wa.read_challenge(cookie_tok or "")
    if not payload or payload.get("ot") != "client_login":
        raise HTTPException(status_code=400, detail="Challenge invalide ou expiré")
    challenge = wa.b64url_decode(payload["ch"])

    credential = body.get("credential") or {}
    user_handle_b64 = (credential.get("response") or {}).get("userHandle")
    if not user_handle_b64:
        raise HTTPException(status_code=400, detail="userHandle manquant — passkey non discoverable")
    parsed = _parse_user_handle(user_handle_b64)
    if parsed is None or parsed[0] != "client":
        raise HTTPException(status_code=400, detail="userHandle invalide")
    owner_type, owner_id = parsed

    user = await db.get(ClientAccount, owner_id)
    if user is None or not user.is_verified:
        raise HTTPException(status_code=400, detail="Compte introuvable")

    try:
        cred = await wa.complete_authentication(
            db,
            challenge=challenge,
            credential_json=json.dumps(credential),
            expected_origin=_expected_origin(request),
        )
    except ValueError as e:
        await activity_record(
            db, action="client_webauthn_login_fail",
            user_name=user.email, module="booking",
            entity_type="client_account", entity_id=user.id,
            detail=str(e)[:200], ip_address=_client_ip(request),
        )
        raise HTTPException(status_code=400, detail=str(e))

    # Vérifie que le credential matched appartient bien au user du handle
    if cred.owner_type != "client" or cred.owner_id != owner_id:
        raise HTTPException(status_code=400, detail="Credential / user handle mismatch")

    user.last_login_at = datetime.now(timezone.utc)
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")
    await activity_record(
        db, action="client_login",
        user_name=user.email, module="booking",
        entity_type="client_account", entity_id=user.id,
        detail=f"webauthn cred={cred.id}",
        ip_address=ip,
    )
    _, is_new = await device_detection.see_device(
        db, owner_type="client", owner_id=user.id, ua=ua, ip=ip,
    )
    if is_new:
        await security_alerts.notify_new_device_login(
            to_email=user.email,
            recipient_name=user.contact_name or user.company_name or user.email,
            ip=ip, ua=ua,
        )

    token = create_client_session(user.id)
    redirect = JSONResponse({"ok": True, "redirect": "/me"})
    redirect.set_cookie(value=token, **cookie_kwargs_for_client(request))
    redirect.delete_cookie(wa.CHALLENGE_COOKIE_AUTH, path="/")
    return redirect


# ─────────────────────────────────────────────────────────────────────
#                  LOGIN — passwordless passkey (STAFF)
# ─────────────────────────────────────────────────────────────────────


@router.post("/login/webauthn/options")
async def staff_login_webauthn_options(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    options_json, challenge = await wa.begin_authentication(
        db, owner_type=None, owner_id=None,
    )
    token = wa.sign_challenge(challenge, owner_type="staff_login", owner_id=None)
    resp = Response(content=options_json, media_type="application/json")
    resp.set_cookie(
        value=token,
        **wa.cookie_kwargs_for_challenge(
            wa.CHALLENGE_COOKIE_AUTH, secure=request.url.scheme == "https",
        ),
    )
    return resp


@router.post("/login/webauthn/verify")
async def staff_login_webauthn_verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    cookie_tok = request.cookies.get(wa.CHALLENGE_COOKIE_AUTH)
    payload = wa.read_challenge(cookie_tok or "")
    if not payload or payload.get("ot") != "staff_login":
        raise HTTPException(status_code=400, detail="Challenge invalide ou expiré")
    challenge = wa.b64url_decode(payload["ch"])

    credential = body.get("credential") or {}
    user_handle_b64 = (credential.get("response") or {}).get("userHandle")
    if not user_handle_b64:
        raise HTTPException(status_code=400, detail="userHandle manquant — passkey non discoverable")
    parsed = _parse_user_handle(user_handle_b64)
    if parsed is None or parsed[0] != "staff":
        raise HTTPException(status_code=400, detail="userHandle invalide")
    owner_type, owner_id = parsed

    user = await db.get(User, owner_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Compte introuvable")

    try:
        cred = await wa.complete_authentication(
            db,
            challenge=challenge,
            credential_json=json.dumps(credential),
            expected_origin=_expected_origin(request),
        )
    except ValueError as e:
        await activity_record(
            db, action="staff_webauthn_login_fail",
            user_id=user.id, user_name=user.username, user_role=user.role,
            detail=str(e)[:200], ip_address=_client_ip(request),
        )
        raise HTTPException(status_code=400, detail=str(e))

    if cred.owner_type != "staff" or cred.owner_id != owner_id:
        raise HTTPException(status_code=400, detail="Credential / user handle mismatch")

    user.last_login_at = datetime.now(timezone.utc)
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")
    await activity_record(
        db, action="login",
        user_id=user.id, user_name=user.username, user_role=user.role,
        detail=f"webauthn cred={cred.id}",
        ip_address=ip,
    )
    _, is_new = await device_detection.see_device(
        db, owner_type="staff", owner_id=user.id, ua=ua, ip=ip,
    )
    if is_new and user.email:
        await security_alerts.notify_new_device_login(
            to_email=user.email,
            recipient_name=user.full_name or user.username,
            ip=ip, ua=ua,
        )

    redirect_to = "/admin/my-account/change-password" if user.must_change_password else "/dashboard"
    token = create_staff_session(user.id)
    resp = JSONResponse({"ok": True, "redirect": redirect_to})
    resp.set_cookie(value=token, **cookie_kwargs_for_staff(request, role=user.role))
    resp.delete_cookie(wa.CHALLENGE_COOKIE_AUTH, path="/")
    return resp
