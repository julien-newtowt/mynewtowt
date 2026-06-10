"""Client authentication — login, register, logout, change password.

Separate cookie / serializer from staff (`towt_client_session`).

Hardenings V3.1 :
  - Login : rate-limit persistant (`rate_limit_attempts`), 10 tentatives /
    10 min / IP → 429.
  - Anti-énumération : message d'erreur unique + bcrypt fictif sur email
    inexistant pour égaliser le temps de réponse.
  - Pas de PII en clair dans les logs (email hashé côté ``activity.record``).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    CLIENT_COOKIE,
    CLIENT_MFA_PENDING_COOKIE,
    cookie_kwargs_for_client,
    cookie_kwargs_for_client_mfa_pending,
    create_client_mfa_pending,
    create_client_session,
    decode_client_mfa_pending,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.client_account import ClientAccount
from app.services import device_detection, mfa, rate_limit, security_alerts
from app.services.activity import record as activity_record
from app.templating import templates


# Hash bcrypt fictif (vrai bcrypt cost=12) utilisé pour égaliser le temps
# quand l'email n'existe pas — précalculé pour le password aléatoire
# "newtowt_decoy_password_2026" (valeur jamais utilisée en clair).
# Évite de calculer un hash au module-load (incompatibilité bcrypt v4 +
# passlib < 1.8 sur certains envs).
_DUMMY_HASH = "$2b$12$O/jKlBtKnLgWqyXmEjPq8eYDQ.UQ0Ahnt0LeG6h2XdNJgI4r5kSDS"

# Message d'erreur unique (anti-énum)
_LOGIN_ERR = "Identifiants incorrects ou compte non vérifié."

router = APIRouter(tags=["client-auth"])


@router.get("/me/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "client/login.html", {"request": request, "error": None}
    )


@router.post("/me/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request) or "unknown"
    email_clean = email.strip().lower()

    # Rate-limit par IP (10/10 min). On vérifie AVANT le lookup DB pour ne
    # pas exposer un canal de timing par cache miss.
    if await rate_limit.exceeded(
        db, scope="client_login_ip", identifier=ip,
        max_attempts=10, window_minutes=10,
    ):
        return templates.TemplateResponse(
            "client/login.html",
            {"request": request, "error": "Trop de tentatives — patientez 10 minutes."},
            status_code=429,
        )

    user = (
        await db.execute(
            select(ClientAccount).where(ClientAccount.email == email_clean)
        )
    ).scalar_one_or_none()

    # Anti-énum : on calcule TOUJOURS un verify_password, même si l'email
    # est inconnu (avec un hash fictif). Le temps de réponse est égalisé.
    if user is not None:
        ok = verify_password(password, user.hashed_password)
        verified = user.is_verified
    else:
        verify_password(password, _DUMMY_HASH)  # constant-time decoy
        ok = False
        verified = False

    if not ok or not verified:
        await rate_limit.record(db, scope="client_login_ip", identifier=ip)
        await activity_record(
            db,
            action="client_login_fail",
            module="booking",
            entity_type="client_account",
            entity_label=email_clean,  # automatiquement scrubbé par activity.record
            ip_address=ip,
        )
        # Petit jitter constant pour brouiller l'inférence
        await asyncio.sleep(0.05)
        return templates.TemplateResponse(
            "client/login.html",
            {"request": request, "error": _LOGIN_ERR},
            status_code=400,
        )

    # Si MFA activé sur ce compte : ne pas poser le cookie session tout
    # de suite. On signe un token court (5min) "MFA pending" et on
    # redirige vers /me/login/mfa pour la phase challenge.
    if getattr(user, "mfa_enabled", False) and user.mfa_secret:
        await activity_record(
            db, action="client_login_password_ok_mfa_required",
            user_name=user.email, module="booking",
            entity_type="client_account", entity_id=user.id, ip_address=ip,
        )
        pending = create_client_mfa_pending(user.id)
        redirect = RedirectResponse(url="/me/login/mfa", status_code=303)
        redirect.set_cookie(value=pending, **cookie_kwargs_for_client_mfa_pending(request))
        return redirect

    user.last_login_at = datetime.now(timezone.utc)
    await activity_record(
        db,
        action="client_login",
        user_name=user.email,
        module="booking",
        entity_type="client_account",
        entity_id=user.id,
        ip_address=ip,
    )
    # Détection nouveau device — alerte email si jamais vu
    ua = request.headers.get("user-agent")
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
    redirect = RedirectResponse(url="/me", status_code=303)
    redirect.set_cookie(value=token, **cookie_kwargs_for_client(request))
    return redirect


# ─────────────────────────────────────────────────────────────────────
#                       MFA challenge (post-password)
# ─────────────────────────────────────────────────────────────────────


@router.get("/me/login/mfa", response_class=HTMLResponse)
async def mfa_challenge_form(
    request: Request,
    mfa_pending: str | None = None,  # cookie injected via dependency below
) -> HTMLResponse:
    pending_cookie = request.cookies.get(CLIENT_MFA_PENDING_COOKIE)
    if not pending_cookie or decode_client_mfa_pending(pending_cookie) is None:
        return RedirectResponse(url="/me/login", status_code=303)
    return templates.TemplateResponse(
        "client/mfa_challenge.html", {"request": request, "error": None},
    )


@router.post("/me/login/mfa", response_class=HTMLResponse)
async def mfa_challenge_submit(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request) or "unknown"
    pending_cookie = request.cookies.get(CLIENT_MFA_PENDING_COOKIE)
    client_id = decode_client_mfa_pending(pending_cookie or "")
    if client_id is None:
        return RedirectResponse(url="/me/login", status_code=303)

    # Rate-limit dédié pour le challenge MFA (5/5min — anti-bruteforce).
    if await rate_limit.exceeded(
        db, scope="client_mfa_ip", identifier=ip,
        max_attempts=5, window_minutes=5,
    ):
        return templates.TemplateResponse(
            "client/mfa_challenge.html",
            {"request": request, "error": "Trop de tentatives — patientez 5 minutes."},
            status_code=429,
        )

    user = await db.get(ClientAccount, client_id)
    if user is None or not user.mfa_enabled or not user.mfa_secret:
        # Edge case : MFA désactivé entre le password OK et le challenge.
        return RedirectResponse(url="/me/login", status_code=303)

    # 1. Tente d'abord un code TOTP standard 6 chiffres
    totp_ok = mfa.verify_totp(user.mfa_secret, code)
    # 2. Sinon tente un code de récupération (format xxxx-xxxx-xxxx)
    recovery_ok = False
    if not totp_ok:
        recovery_ok = await mfa.consume_recovery_code(
            db, owner_type="client", owner_id=user.id, code=code,
        )

    if not totp_ok and not recovery_ok:
        await rate_limit.record(db, scope="client_mfa_ip", identifier=ip)
        await activity_record(
            db, action="client_mfa_fail", user_name=user.email,
            module="booking", entity_type="client_account",
            entity_id=user.id, ip_address=ip,
        )
        await asyncio.sleep(0.05)
        return templates.TemplateResponse(
            "client/mfa_challenge.html",
            {"request": request, "error": "Code TOTP invalide."},
            status_code=400,
        )

    user.last_login_at = datetime.now(timezone.utc)
    await activity_record(
        db, action="client_login", user_name=user.email,
        module="booking", entity_type="client_account",
        entity_id=user.id,
        detail="mfa_ok" if totp_ok else "mfa_recovery_code_used",
        ip_address=ip,
    )
    ua = request.headers.get("user-agent")
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
    redirect = RedirectResponse(url="/me", status_code=303)
    redirect.set_cookie(value=token, **cookie_kwargs_for_client(request))
    redirect.delete_cookie(CLIENT_MFA_PENDING_COOKIE, path="/")
    return redirect


@router.get("/me/register", response_class=HTMLResponse)
async def register_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "client/register.html", {"request": request, "error": None}
    )


@router.post("/me/register", response_class=HTMLResponse)
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    company_name: str = Form(...),
    contact_name: str = Form(""),
    country: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    email_clean = email.strip().lower()
    if len(password) < 12:
        return templates.TemplateResponse(
            "client/register.html",
            {
                "request": request,
                "error": "Le mot de passe doit contenir au moins 12 caractères.",
            },
            status_code=400,
        )
    existing = (
        await db.execute(
            select(ClientAccount).where(ClientAccount.email == email_clean)
        )
    ).scalar_one_or_none()
    if existing:
        return templates.TemplateResponse(
            "client/register.html",
            {"request": request, "error": "Un compte existe déjà avec cet email."},
            status_code=400,
        )

    client = ClientAccount(
        email=email_clean,
        hashed_password=hash_password(password),
        company_name=company_name.strip(),
        contact_name=contact_name.strip() or None,
        country=(country.strip().upper() or None),
        # V3.0: instant verification for ease of testing; production should
        # require email-link verification before is_verified=True.
        is_verified=True,
        segment="occasional",
    )
    db.add(client)
    await db.flush()

    await activity_record(
        db,
        action="client_register",
        user_name=client.email,
        module="booking",
        entity_type="client_account",
        entity_id=client.id,
        entity_label=client.company_name,
        ip_address=_client_ip(request),
    )

    token = create_client_session(client.id)
    redirect = RedirectResponse(url="/me", status_code=303)
    redirect.set_cookie(value=token, **cookie_kwargs_for_client(request))
    return redirect


@router.get("/me/logout")
async def logout(request: Request) -> RedirectResponse:
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.delete_cookie(CLIENT_COOKIE, path="/")
    return redirect


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
