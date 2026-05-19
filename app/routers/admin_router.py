"""Admin enriched — users CRUD, OPEX, insurance, activity log viewer, maintenance.

Mounted under /admin/* (distinct from /admin/ports already in modules_router
and admin_dashboard in modules_router /admin landing).

Reprises de la V3.0.0 :
- CRUD utilisateurs avec must_change_password.
- Paramètres OPEX (numeric key→value).
- Contrats d'assurance.
- Mode maintenance (toggle file marker).
- Viewer activity_logs (filtre + pagination simple).
- Mon compte + change password.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    get_current_staff, hash_password, verify_password,
)
from app.database import get_db
from app.models.activity_log import ActivityLog
from app.models.finance import OpexParameter
from app.models.insurance import INSURANCE_KINDS, InsuranceContract
from app.models.user import User
from app.permissions import ROLES, require_permission
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin-enriched"])

MAINTENANCE_MARKER = Path("/tmp/.maintenance")


# ────────────────────────────────────────────── Users CRUD
@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    users = list((await db.execute(
        select(User).order_by(User.username)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/admin/users.html",
        {"request": request, "user": user, "users": users, "roles": ROLES},
    )


@router.post("/users")
async def users_create(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    full_name: str | None = Form(None),
    role: str = Form(...),
    password: str = Form(...),
    must_change_password: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="invalid role")
    existing = (await db.execute(
        select(User).where((User.username == username) | (User.email == email))
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="utilisateur déjà existant")
    new_user = User(
        username=username.strip(), email=email.strip(),
        full_name=(full_name or "").strip() or None,
        hashed_password=hash_password(password),
        role=role,
        must_change_password=must_change_password,
    )
    db.add(new_user)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="user",
        entity_id=new_user.id, entity_label=new_user.username,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle")
async def users_toggle_active(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="impossible de se désactiver soi-même")
    target.is_active = not target.is_active
    await db.flush()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="user",
        entity_id=target.id, entity_label=target.username,
        detail=("activated" if target.is_active else "deactivated"),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/reset-password")
async def users_reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    target.hashed_password = hash_password(new_password)
    target.must_change_password = True
    await db.flush()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="user",
        entity_id=target.id, entity_label=target.username,
        detail="password reset",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ────────────────────────────────────────────── OPEX parameters
@router.get("/opex", response_class=HTMLResponse)
async def opex_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    params = list((await db.execute(
        select(OpexParameter).order_by(OpexParameter.category, OpexParameter.parameter_name)
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/admin/opex.html",
        {"request": request, "user": user, "params": params},
    )


@router.post("/opex")
async def opex_upsert(
    request: Request,
    parameter_name: str = Form(...),
    parameter_value: float = Form(...),
    unit: str | None = Form(None),
    category: str | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    existing = (await db.execute(
        select(OpexParameter).where(OpexParameter.parameter_name == parameter_name)
    )).scalar_one_or_none()
    if existing is None:
        p = OpexParameter(
            parameter_name=parameter_name, parameter_value=parameter_value,
            unit=unit, category=category, description=description,
        )
        db.add(p)
    else:
        existing.parameter_value = parameter_value
        existing.unit = unit
        existing.category = category
        existing.description = description
    await db.flush()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="opex_parameter",
        entity_id=None, entity_label=parameter_name,
        detail=f"value={parameter_value}", ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/opex", status_code=303)


# ────────────────────────────────────────────── Insurance contracts
@router.get("/insurance", response_class=HTMLResponse)
async def insurance_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    contracts = list((await db.execute(
        select(InsuranceContract).order_by(InsuranceContract.valid_to.desc())
    )).scalars().all())
    return templates.TemplateResponse(
        "staff/admin/insurance.html",
        {"request": request, "user": user, "contracts": contracts, "kinds": INSURANCE_KINDS},
    )


@router.post("/insurance")
async def insurance_create(
    request: Request,
    kind: str = Form(...),
    reference: str = Form(...),
    insurer: str = Form(...),
    broker: str | None = Form(None),
    valid_from: str = Form(...),
    valid_to: str = Form(...),
    premium_eur: float | None = Form(None),
    deductible_eur: float | None = Form(None),
    coverage_amount_eur: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    if kind not in INSURANCE_KINDS:
        raise HTTPException(status_code=400, detail="invalid kind")
    from datetime import date as _date
    c = InsuranceContract(
        kind=kind, reference=reference, insurer=insurer, broker=broker,
        valid_from=_date.fromisoformat(valid_from),
        valid_to=_date.fromisoformat(valid_to),
        premium_eur=premium_eur, deductible_eur=deductible_eur,
        coverage_amount_eur=coverage_amount_eur, notes=notes,
    )
    db.add(c)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="insurance_contract",
        entity_id=c.id, entity_label=reference,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/insurance", status_code=303)


# ────────────────────────────────────────────── Maintenance mode
@router.get("/maintenance", response_class=HTMLResponse)
async def maintenance_status(
    request: Request,
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/admin/maintenance.html",
        {"request": request, "user": user, "enabled": MAINTENANCE_MARKER.exists()},
    )


@router.post("/maintenance/enable")
async def maintenance_enable(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    MAINTENANCE_MARKER.touch()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="maintenance",
        entity_label="enabled", ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/maintenance", status_code=303)


@router.post("/maintenance/disable")
async def maintenance_disable(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    if MAINTENANCE_MARKER.exists():
        MAINTENANCE_MARKER.unlink()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="maintenance",
        entity_label="disabled", ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/maintenance", status_code=303)


# ────────────────────────────────────────────── Activity log viewer
@router.get("/activity-logs", response_class=HTMLResponse)
async def activity_logs_view(
    request: Request,
    module: str | None = None,
    action: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc())
    if module:
        stmt = stmt.where(ActivityLog.module == module)
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    stmt = stmt.limit(max(10, min(limit, 500)))
    logs = list((await db.execute(stmt)).scalars().all())

    # Aggregate counts for filter chips
    modules_count: dict[str, int] = {}
    for l in logs:
        modules_count[l.module or "—"] = modules_count.get(l.module or "—", 0) + 1

    return templates.TemplateResponse(
        "staff/admin/activity_logs.html",
        {
            "request": request, "user": user, "logs": logs,
            "modules_count": modules_count,
            "filter_module": module, "filter_action": action,
        },
    )


# ────────────────────────────────────────────── My account
@router.get("/my-account", response_class=HTMLResponse)
async def my_account(
    request: Request,
    user=Depends(get_current_staff),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/admin/my_account.html",
        {"request": request, "user": user},
    )


@router.get("/my-account/change-password", response_class=HTMLResponse)
async def change_password_form(
    request: Request,
    user=Depends(get_current_staff),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/admin/change_password.html",
        {"request": request, "user": user},
    )


@router.post("/my-account/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    if not verify_password(current_password, user.hashed_password):
        return templates.TemplateResponse(
            "staff/admin/change_password.html",
            {"request": request, "user": user,
             "error": "Mot de passe actuel incorrect."},
            status_code=400,
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "staff/admin/change_password.html",
            {"request": request, "user": user,
             "error": "Les deux nouveaux mots de passe diffèrent."},
            status_code=400,
        )
    if len(new_password) < 12:
        return templates.TemplateResponse(
            "staff/admin/change_password.html",
            {"request": request, "user": user,
             "error": "Mot de passe trop court (12 caractères minimum)."},
            status_code=400,
        )
    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    await db.flush()
    await activity_record(
        db, action="update", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="admin", entity_type="user",
        entity_id=user.id, entity_label=user.username,
        detail="password changed", ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/dashboard", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
