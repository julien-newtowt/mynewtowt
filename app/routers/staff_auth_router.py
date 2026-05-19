"""Staff authentication — login & logout."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    STAFF_COOKIE,
    cookie_kwargs_for_staff,
    create_staff_session,
    verify_password,
)
from app.database import get_db
from app.models.user import User
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

    user.last_login_at = datetime.now(timezone.utc)
    await activity_record(
        db,
        action="login",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        ip_address=_client_ip(request),
    )

    token = create_staff_session(user.id)
    redirect_to = "/admin/my-account/change-password" if user.must_change_password else "/dashboard"
    redirect = RedirectResponse(url=redirect_to, status_code=303)
    redirect.set_cookie(value=token, **cookie_kwargs_for_staff(request))
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
