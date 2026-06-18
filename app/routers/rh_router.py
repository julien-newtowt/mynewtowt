"""RH — module Ressources humaines.

ARC (lots SIRH du cahier des charges) — routeur dédié du SIRH, prélude à
la montée en charge progressive (cf. ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md``).

Couvre à ce stade :
- **Congés marins** (stub historique repris du ``modules_router``).
- **Collaborateurs sédentaires** (lot L1) : dossier, CRUD, import fichier.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.crew import CrewLeave, CrewMember
from app.models.employee import EMPLOYEE_STATUSES, Employee
from app.models.user import User
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.hr_import import parse_employees_csv
from app.templating import templates

router = APIRouter(tags=["rh"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ────────────────────────────────────────────────────────────────────
#                          Congés (marins)
# ────────────────────────────────────────────────────────────────────


@router.get("/rh", response_class=HTMLResponse)
async def rh_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    members = list(
        (await db.execute(select(CrewMember).where(CrewMember.is_active.is_(True)))).scalars().all()
    )
    leaves = list(
        (await db.execute(select(CrewLeave).order_by(CrewLeave.created_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    pending = [lv for lv in leaves if lv.status == "requested"]
    return templates.TemplateResponse(
        "staff/rh/index.html",
        {
            "request": request,
            "user": user,
            "members": members,
            "leaves": leaves,
            "pending": pending,
        },
    )


@router.post("/rh/leave")
async def rh_create_leave(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    f = await request.form()
    leave = CrewLeave(
        crew_member_id=int(f["crew_member_id"]),
        kind=f["kind"],
        start_date=date.fromisoformat(f["start_date"]),
        end_date=date.fromisoformat(f["end_date"]),
        status="requested",
        reason=f.get("reason") or None,
    )
    db.add(leave)
    await db.flush()
    return RedirectResponse(url="/rh", status_code=303)


@router.post("/rh/leave/{leave_id}/decide")
async def rh_decide_leave(
    leave_id: int,
    decision: str = Form(...),  # 'approved' | 'rejected'
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    leave = await db.get(CrewLeave, leave_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Not found")
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"decision must be 'approved' or 'rejected', got {decision!r}",
        )
    leave.status = decision
    leave.decided_by_id = user.id
    leave.decided_at = datetime.now(UTC)
    await db.flush()
    return RedirectResponse(url="/rh", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                  Collaborateurs sédentaires (L1)
# ────────────────────────────────────────────────────────────────────


async def _form_choices(db: AsyncSession, *, exclude_id: int | None = None) -> dict:
    """Listes déroulantes des formulaires (managers, comptes staff, marins)."""
    mgr_q = select(Employee).order_by(Employee.last_name, Employee.first_name)
    if exclude_id:
        mgr_q = mgr_q.where(Employee.id != exclude_id)
    managers = list((await db.execute(mgr_q)).scalars().all())
    users = list(
        (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.username)))
        .scalars()
        .all()
    )
    crew = list(
        (
            await db.execute(
                select(CrewMember)
                .where(CrewMember.is_active.is_(True))
                .order_by(CrewMember.full_name)
            )
        )
        .scalars()
        .all()
    )
    return {"managers": managers, "users": users, "crew": crew, "statuses": EMPLOYEE_STATUSES}


@router.get("/rh/employees", response_class=HTMLResponse)
async def employees_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
    status: str | None = None,
    department: str | None = None,
    q: str | None = None,
) -> HTMLResponse:
    query = select(Employee)
    if status in EMPLOYEE_STATUSES:
        query = query.where(Employee.status == status)
    if department:
        query = query.where(Employee.department == department)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Employee.first_name.ilike(like),
                Employee.last_name.ilike(like),
                Employee.matricule.ilike(like),
                Employee.job_title.ilike(like),
            )
        )
    employees = list(
        (await db.execute(query.order_by(Employee.last_name, Employee.first_name)))
        .scalars()
        .all()
    )

    # Stats globales (indépendantes des filtres).
    total = (await db.execute(select(func.count(Employee.id)))).scalar_one()
    active = (
        await db.execute(
            select(func.count(Employee.id)).where(Employee.status == "active")
        )
    ).scalar_one()
    departments = [
        d
        for (d,) in (
            await db.execute(
                select(Employee.department)
                .where(Employee.department.is_not(None))
                .distinct()
                .order_by(Employee.department)
            )
        ).all()
    ]
    return templates.TemplateResponse(
        "staff/rh/employees.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "stats": {"total": total, "active": active, "left": total - active},
            "departments": departments,
            "f_status": status,
            "f_department": department,
            "f_q": q,
            "statuses": EMPLOYEE_STATUSES,
        },
    )


@router.get("/rh/employees/new", response_class=HTMLResponse)
async def employee_new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/rh/employee_form.html",
        {
            "request": request,
            "user": user,
            "employee": None,
            **(await _form_choices(db)),
        },
    )


def _employee_from_form(f) -> dict:
    """Extrait/normalise les champs Employee depuis un form (create/update)."""

    def s(key: str) -> str | None:
        val = (f.get(key) or "").strip()
        return val or None

    def d(key: str) -> date | None:
        val = (f.get(key) or "").strip()
        return date.fromisoformat(val) if val else None

    def num(key: str) -> Decimal:
        val = (f.get(key) or "").strip().replace(",", ".")
        return Decimal(val) if val else Decimal("0")

    def fk(key: str) -> int | None:
        val = (f.get(key) or "").strip()
        return int(val) if val else None

    status = s("status") or "active"
    if status not in EMPLOYEE_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    return {
        "matricule": s("matricule"),
        "first_name": s("first_name"),
        "last_name": s("last_name"),
        "email_pro": s("email_pro"),
        "phone_pro": s("phone_pro"),
        "birth_date": d("birth_date"),
        "job_title": s("job_title"),
        "department": s("department"),
        "manager_id": fk("manager_id"),
        "work_location": s("work_location"),
        "entry_date": d("entry_date"),
        "exit_date": d("exit_date"),
        "status": status,
        "cp_balance": num("cp_balance"),
        "rtt_balance": num("rtt_balance"),
        "user_id": fk("user_id"),
        "crew_member_id": fk("crew_member_id"),
        "silae_id": s("silae_id"),
    }


@router.post("/rh/employees")
async def employee_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    f = await request.form()
    data = _employee_from_form(f)
    if not data["matricule"] or not data["first_name"] or not data["last_name"]:
        raise HTTPException(status_code=400, detail="matricule, prénom et nom obligatoires")
    exists = (
        await db.execute(select(Employee.id).where(Employee.matricule == data["matricule"]))
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="matricule déjà utilisé")
    emp = Employee(**data)
    db.add(emp)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="employee",
        entity_id=emp.id,
        entity_label=emp.full_name,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{emp.id}", status_code=303)


@router.get("/rh/employees/{employee_id}", response_class=HTMLResponse)
async def employee_detail(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    manager = await db.get(Employee, emp.manager_id) if emp.manager_id else None
    account = await db.get(User, emp.user_id) if emp.user_id else None
    return templates.TemplateResponse(
        "staff/rh/employee_detail.html",
        {
            "request": request,
            "user": user,
            "employee": emp,
            "manager": manager,
            "account": account,
        },
    )


@router.get("/rh/employees/{employee_id}/edit", response_class=HTMLResponse)
async def employee_edit_form(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> HTMLResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "staff/rh/employee_form.html",
        {
            "request": request,
            "user": user,
            "employee": emp,
            **(await _form_choices(db, exclude_id=employee_id)),
        },
    )


@router.post("/rh/employees/{employee_id}/edit")
async def employee_update(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    f = await request.form()
    data = _employee_from_form(f)
    if not data["matricule"] or not data["first_name"] or not data["last_name"]:
        raise HTTPException(status_code=400, detail="matricule, prénom et nom obligatoires")
    if data["matricule"] != emp.matricule:
        clash = (
            await db.execute(
                select(Employee.id).where(
                    Employee.matricule == data["matricule"], Employee.id != employee_id
                )
            )
        ).first()
        if clash:
            raise HTTPException(status_code=400, detail="matricule déjà utilisé")
    if data["manager_id"] == employee_id:
        raise HTTPException(status_code=400, detail="un collaborateur ne peut être son propre manager")
    for key, value in data.items():
        setattr(emp, key, value)
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="employee",
        entity_id=emp.id,
        entity_label=emp.full_name,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{emp.id}", status_code=303)


@router.post("/rh/employees/{employee_id}/delete")
async def employee_delete(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "S")),
) -> RedirectResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    label = emp.full_name
    await db.delete(emp)
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="employee",
        entity_id=employee_id,
        entity_label=label,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/rh/employees", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                     Import fichier (reprise L1)
# ────────────────────────────────────────────────────────────────────


@router.get("/rh/import", response_class=HTMLResponse)
async def employees_import_form(
    request: Request,
    user=Depends(require_permission("rh", "M")),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/rh/import.html",
        {"request": request, "user": user, "result": None, "committed": None},
    )


@router.post("/rh/import", response_class=HTMLResponse)
async def employees_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
    file: UploadFile | None = None,
    dry_run: str | None = Form(None),
) -> HTMLResponse:
    raw = await file.read() if file else b""
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")

    result = parse_employees_csv(content)

    # Doublons par rapport à l'existant en base → signalés, non importés.
    existing = {
        m for (m,) in (await db.execute(select(Employee.matricule))).all()
    }
    importable = [r for r in result.rows if r["matricule"] not in existing]
    already = [r for r in result.rows if r["matricule"] in existing]

    is_dry = dry_run is not None  # case cochée = simulation
    committed = None
    if not is_dry and importable:
        for row in importable:
            db.add(Employee(**row))
        await db.flush()
        await activity_record(
            db,
            action="import",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="rh",
            entity_type="employee",
            entity_id=None,
            entity_label=f"import {len(importable)} collaborateur(s)",
            ip_address=_client_ip(request),
        )
        committed = len(importable)

    return templates.TemplateResponse(
        "staff/rh/import.html",
        {
            "request": request,
            "user": user,
            "result": result,
            "importable": importable,
            "already": already,
            "is_dry": is_dry,
            "committed": committed,
        },
    )
