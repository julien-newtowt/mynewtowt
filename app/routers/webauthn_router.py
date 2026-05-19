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
from app.services import webauthn_service as wa
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
    return RedirectResponse(url="/admin/my-account/webauthn", status_code=303)
