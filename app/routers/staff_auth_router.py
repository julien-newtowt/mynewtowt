"""Staff authentication — login (incl. MFA challenge), logout."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    STAFF_COOKIE,
    STAFF_MFA_PENDING_COOKIE,
    cookie_kwargs_for_staff,
    cookie_kwargs_for_staff_mfa_pending,
    create_staff_mfa_pending,
    create_staff_session,
    decode_staff_mfa_pending,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.services import device_detection, mfa, rate_limit, security_alerts
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(tags=["staff-auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/login.html", {"request": request, "error": None}
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(
            select(User).where(User.username == username.strip(), User.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        await activity_record(
            db,
            action="login_fail",
            user_name=username,
            ip_address=_client_ip(request),
        )
        return templates.TemplateResponse(
            "staff/login.html",
            {"request": request, "error": "Identifiants incorrects."},
            status_code=400,
        )

    # MFA challenge si activé sur ce compte staff
    if getattr(user, "mfa_enabled", False) and user.mfa_secret:
        await activity_record(
            db, action="login_password_ok_mfa_required",
            user_id=user.id, user_name=user.username, user_role=user.role,
            ip_address=_client_ip(request),
        )
        pending = create_staff_mfa_pending(user.id)
        redirect = RedirectResponse(url="/login/mfa", status_code=303)
        redirect.set_cookie(value=pending, **cookie_kwargs_for_staff_mfa_pending(request))
        return redirect

    user.last_login_at = datetime.now(timezone.utc)
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")
    await activity_record(
        db,
        action="login",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
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

    token = create_staff_session(user.id)
    redirect_to = "/admin/my-account/change-password" if user.must_change_password else "/dashboard"
    redirect = RedirectResponse(url=redirect_to, status_code=303)
    # role-aware cookie expiry — marins/manager_maritime obtiennent 14j
    # de session (cf. STAFF_SESSION_MINUTES_BY_ROLE dans app/auth.py).
    redirect.set_cookie(value=token, **cookie_kwargs_for_staff(request, role=user.role))
    return redirect


# ─────────────────────────────────────────────────────────────────────
#                       MFA challenge staff
# ─────────────────────────────────────────────────────────────────────


@router.get("/login/mfa", response_class=HTMLResponse)
async def staff_mfa_challenge_form(request: Request) -> HTMLResponse:
    pending = request.cookies.get(STAFF_MFA_PENDING_COOKIE)
    if not pending or decode_staff_mfa_pending(pending) is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "staff/login_mfa.html", {"request": request, "error": None},
    )


@router.post("/login/mfa", response_class=HTMLResponse)
async def staff_mfa_challenge_submit(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    ip = _client_ip(request) or "unknown"
    pending = request.cookies.get(STAFF_MFA_PENDING_COOKIE)
    user_id = decode_staff_mfa_pending(pending or "")
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)

    if await rate_limit.exceeded(
        db, scope="staff_mfa_ip", identifier=ip,
        max_attempts=5, window_minutes=5,
    ):
        return templates.TemplateResponse(
            "staff/login_mfa.html",
            {"request": request, "error": "Trop de tentatives — patientez 5 minutes."},
            status_code=429,
        )

    user = await db.get(User, user_id)
    if user is None or not user.is_active or not user.mfa_enabled or not user.mfa_secret:
        return RedirectResponse(url="/login", status_code=303)

    totp_ok = mfa.verify_totp(user.mfa_secret, code)
    recovery_ok = False
    if not totp_ok:
        recovery_ok = await mfa.consume_recovery_code(
            db, owner_type="staff", owner_id=user.id, code=code,
        )

    if not totp_ok and not recovery_ok:
        await rate_limit.record(db, scope="staff_mfa_ip", identifier=ip)
        await activity_record(
            db, action="staff_mfa_fail", user_id=user.id,
            user_name=user.username, user_role=user.role, ip_address=ip,
        )
        await asyncio.sleep(0.05)
        return templates.TemplateResponse(
            "staff/login_mfa.html",
            {"request": request, "error": "Code TOTP invalide."},
            status_code=400,
        )

    user.last_login_at = datetime.now(timezone.utc)
    ua = request.headers.get("user-agent")
    await activity_record(
        db, action="login", user_id=user.id, user_name=user.username,
        user_role=user.role,
        detail="mfa_ok" if totp_ok else "mfa_recovery_code_used",
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
    token = create_staff_session(user.id)
    redirect_to = "/admin/my-account/change-password" if user.must_change_password else "/dashboard"
    redirect = RedirectResponse(url=redirect_to, status_code=303)
    redirect.set_cookie(value=token, **cookie_kwargs_for_staff(request, role=user.role))
    redirect.delete_cookie(STAFF_MFA_PENDING_COOKIE, path="/")
    return redirect


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    redirect = RedirectResponse(url="/login", status_code=303)
    redirect.delete_cookie(STAFF_COOKIE, path="/")
    return redirect


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
