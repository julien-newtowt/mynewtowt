"""Client authentication — login, register, logout, change password.

Separate cookie / serializer from staff (`towt_client_session`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    cookie_kwargs_for_client,
    create_client_session,
    hash_password,
    verify_password,
    CLIENT_COOKIE,
)
from app.database import get_db
from app.models.client_account import ClientAccount
from app.services.activity import record as activity_record
from app.templating import templates

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
    email_clean = email.strip().lower()
    user = (
        await db.execute(
            select(ClientAccount).where(ClientAccount.email == email_clean)
        )
    ).scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        await activity_record(
            db,
            action="client_login_fail",
            module="booking",
            entity_type="client_account",
            entity_label=email_clean,
            ip_address=_client_ip(request),
        )
        return templates.TemplateResponse(
            "client/login.html",
            {"request": request, "error": "Identifiants incorrects."},
            status_code=400,
        )
    if not user.is_verified:
        return templates.TemplateResponse(
            "client/login.html",
            {
                "request": request,
                "error": "Compte non vérifié — vérifiez vos emails.",
            },
            status_code=400,
        )

    user.last_login_at = datetime.now(timezone.utc)
    await activity_record(
        db,
        action="client_login",
        user_name=user.email,
        module="booking",
        entity_type="client_account",
        entity_id=user.id,
        ip_address=_client_ip(request),
    )

    token = create_client_session(user.id)
    redirect = RedirectResponse(url="/me", status_code=303)
    redirect.set_cookie(value=token, **cookie_kwargs_for_client(request))
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
