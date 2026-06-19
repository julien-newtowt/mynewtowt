"""RH — module Ressources humaines.

ARC (lots SIRH du cahier des charges) — routeur dédié du SIRH, prélude à
la montée en charge progressive (cf. ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md``).

Couvre à ce stade :
- **Congés marins** (stub historique repris du ``modules_router``).
- **Collaborateurs sédentaires** (lot L1) : dossier, CRUD, import fichier.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_staff
from app.database import get_db
from app.models.crew import CrewLeave, CrewMember
from app.models.employee import EMPLOYEE_STATUSES, Employee
from app.models.employment_contract import (
    CONTRACT_STATUSES,
    CONTRACT_TYPES,
    DEFAULT_CONVENTION,
    FIXED_TERM_TYPES,
    EmploymentContract,
)
from app.models.hr_absence import ABSENCE_KINDS, ABSENCE_STATUSES, HrAbsence
from app.models.user import User
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.hr_absences import count_business_days
from app.services.hr_import import parse_employees_csv
from app.templating import templates

router = APIRouter(tags=["rh"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Conversions de formulaire sûres (HTTP 400 plutôt que 500) ───────────


def _opt_str(value) -> str | None:
    val = (value or "").strip()
    return val or None


def _opt_date(value, label: str) -> date | None:
    val = (value or "").strip()
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{label} : date invalide") from exc


def _opt_decimal(value, label: str) -> Decimal | None:
    val = (value or "").strip().replace(",", ".")
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} : nombre invalide") from exc


def _opt_int(value, label: str) -> int | None:
    val = (value or "").strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{label} : entier invalide") from exc


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
    crew_member_id = _opt_int(f.get("crew_member_id"), "Marin")
    kind = _opt_str(f.get("kind"))
    start_date = _opt_date(f.get("start_date"), "Du")
    end_date = _opt_date(f.get("end_date"), "Au")
    if not crew_member_id or not kind or not start_date or not end_date:
        raise HTTPException(status_code=400, detail="marin, type et dates obligatoires")
    leave = CrewLeave(
        crew_member_id=crew_member_id,
        kind=kind,
        start_date=start_date,
        end_date=end_date,
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
    mgr_q = (
        select(Employee)
        .where(Employee.status == "active")
        .order_by(Employee.last_name, Employee.first_name)
    )
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
    """Extrait/valide les champs Employee depuis un form (create/update).

    Lève ``HTTPException(400)`` sur saisie malformée (date/nombre/entier).
    """
    status = _opt_str(f.get("status")) or "active"
    if status not in EMPLOYEE_STATUSES:
        raise HTTPException(status_code=400, detail="statut invalide")
    return {
        "matricule": _opt_str(f.get("matricule")),
        "first_name": _opt_str(f.get("first_name")),
        "last_name": _opt_str(f.get("last_name")),
        "email_pro": _opt_str(f.get("email_pro")),
        "phone_pro": _opt_str(f.get("phone_pro")),
        "birth_date": _opt_date(f.get("birth_date"), "Date de naissance"),
        "job_title": _opt_str(f.get("job_title")),
        "department": _opt_str(f.get("department")),
        "manager_id": _opt_int(f.get("manager_id"), "Manager"),
        "work_location": _opt_str(f.get("work_location")),
        "entry_date": _opt_date(f.get("entry_date"), "Date d'entrée"),
        "exit_date": _opt_date(f.get("exit_date"), "Date de sortie"),
        "status": status,
        "cp_balance": _opt_decimal(f.get("cp_balance"), "Solde CP") or Decimal("0"),
        "rtt_balance": _opt_decimal(f.get("rtt_balance"), "Solde RTT") or Decimal("0"),
        "user_id": _opt_int(f.get("user_id"), "Compte staff"),
        "crew_member_id": _opt_int(f.get("crew_member_id"), "Fiche marin"),
        "silae_id": _opt_str(f.get("silae_id")),
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
    contracts = list(
        (
            await db.execute(
                select(EmploymentContract)
                .where(EmploymentContract.employee_id == employee_id)
                .order_by(
                    EmploymentContract.start_date.desc(), EmploymentContract.id.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    # Contrats initiaux (non-avenants) proposables comme parent d'un avenant.
    base_contracts = [c for c in contracts if not c.is_amendment]
    return templates.TemplateResponse(
        "staff/rh/employee_detail.html",
        {
            "request": request,
            "user": user,
            "employee": emp,
            "manager": manager,
            "account": account,
            "contracts": contracts,
            "base_contracts": base_contracts,
            "contract_types": CONTRACT_TYPES,
            "contract_statuses": CONTRACT_STATUSES,
            "fixed_term_types": FIXED_TERM_TYPES,
            "default_convention": DEFAULT_CONVENTION,
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
    # Garde-fou : refuser la suppression si des données liées existent
    # (FK NOT NULL sans cascade → sinon IntegrityError/500 en Postgres).
    # Pour un dossier RH actif, passer le statut à « sorti » plutôt que
    # supprimer (conservation/audit).
    deps = (
        await db.execute(
            select(func.count(EmploymentContract.id)).where(
                EmploymentContract.employee_id == employee_id
            )
        )
    ).scalar_one()
    deps += (
        await db.execute(
            select(func.count(HrAbsence.id)).where(HrAbsence.employee_id == employee_id)
        )
    ).scalar_one()
    deps += (
        await db.execute(
            select(func.count(Employee.id)).where(Employee.manager_id == employee_id)
        )
    ).scalar_one()
    if deps:
        return RedirectResponse(url=f"/rh/employees/{employee_id}?err=deps", status_code=303)
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
#                  Contrats & avenants (L2)
# ────────────────────────────────────────────────────────────────────


def _contract_from_form(f) -> dict:
    """Extrait/valide les champs d'un contrat depuis un form (HTTP 400 si KO)."""
    contract_type = _opt_str(f.get("contract_type"))
    if contract_type not in CONTRACT_TYPES:
        raise HTTPException(status_code=400, detail="type de contrat invalide")
    status = _opt_str(f.get("status")) or "active"
    if status not in CONTRACT_STATUSES:
        raise HTTPException(status_code=400, detail="statut de contrat invalide")
    start_date = _opt_date(f.get("start_date"), "Date de début")
    if not start_date:
        raise HTTPException(status_code=400, detail="date de début obligatoire")
    end_date = _opt_date(f.get("end_date"), "Date de fin")
    if contract_type in FIXED_TERM_TYPES and not end_date:
        raise HTTPException(
            status_code=400,
            detail="un contrat à durée déterminée (CDD/alternance/stage) exige une date de fin",
        )
    if end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="la date de fin précède la date de début")
    parent_id = _opt_int(f.get("parent_contract_id"), "Contrat parent")
    return {
        "contract_type": contract_type,
        "parent_contract_id": parent_id,
        "is_amendment": parent_id is not None,
        "convention": _opt_str(f.get("convention")) or DEFAULT_CONVENTION,
        "classification": _opt_str(f.get("classification")),
        "start_date": start_date,
        "end_date": end_date,
        "trial_end_date": _opt_date(f.get("trial_end_date"), "Fin de période d'essai"),
        "weekly_hours": _opt_decimal(f.get("weekly_hours"), "Temps de travail"),
        "gross_monthly": _opt_decimal(f.get("gross_monthly"), "Brut mensuel"),
        "motive": _opt_str(f.get("motive")),
        "status": status,
    }


@router.post("/rh/employees/{employee_id}/contracts")
async def contract_create(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    f = await request.form()
    data = _contract_from_form(f)
    # Un avenant doit référencer un contrat de ce même collaborateur.
    if data["parent_contract_id"] is not None:
        parent = await db.get(EmploymentContract, data["parent_contract_id"])
        if not parent or parent.employee_id != employee_id:
            raise HTTPException(status_code=400, detail="contrat parent invalide")
    contract = EmploymentContract(employee_id=employee_id, **data)
    db.add(contract)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="employment_contract",
        entity_id=contract.id,
        entity_label=f"{emp.full_name} — {contract.contract_type}"
        + (" (avenant)" if contract.is_amendment else ""),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{employee_id}", status_code=303)


@router.get("/rh/contracts/{contract_id}/edit", response_class=HTMLResponse)
async def contract_edit_form(
    contract_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> HTMLResponse:
    contract = await db.get(EmploymentContract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Not found")
    employee = await db.get(Employee, contract.employee_id)
    base_contracts = list(
        (
            await db.execute(
                select(EmploymentContract).where(
                    EmploymentContract.employee_id == contract.employee_id,
                    EmploymentContract.is_amendment.is_(False),
                    EmploymentContract.id != contract_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/rh/contract_form.html",
        {
            "request": request,
            "user": user,
            "contract": contract,
            "employee": employee,
            "base_contracts": base_contracts,
            "contract_types": CONTRACT_TYPES,
            "contract_statuses": CONTRACT_STATUSES,
            "fixed_term_types": FIXED_TERM_TYPES,
            "default_convention": DEFAULT_CONVENTION,
        },
    )


@router.post("/rh/contracts/{contract_id}/edit")
async def contract_update(
    contract_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    contract = await db.get(EmploymentContract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Not found")
    f = await request.form()
    data = _contract_from_form(f)
    if data["parent_contract_id"] is not None:
        if data["parent_contract_id"] == contract_id:
            raise HTTPException(status_code=400, detail="un contrat ne peut être son propre parent")
        parent = await db.get(EmploymentContract, data["parent_contract_id"])
        if not parent or parent.employee_id != contract.employee_id:
            raise HTTPException(status_code=400, detail="contrat parent invalide")
    for key, value in data.items():
        setattr(contract, key, value)
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="employment_contract",
        entity_id=contract.id,
        entity_label=f"contrat #{contract.id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{contract.employee_id}", status_code=303)


@router.post("/rh/contracts/{contract_id}/delete")
async def contract_delete(
    contract_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "S")),
) -> RedirectResponse:
    contract = await db.get(EmploymentContract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="Not found")
    employee_id = contract.employee_id
    # Garde-fou : un contrat initial référencé par des avenants ne peut être
    # supprimé tel quel (FK parent_contract_id → sinon 500 en Postgres).
    amendments = (
        await db.execute(
            select(func.count(EmploymentContract.id)).where(
                EmploymentContract.parent_contract_id == contract_id
            )
        )
    ).scalar_one()
    if amendments:
        return RedirectResponse(
            url=f"/rh/employees/{employee_id}?err=contract_deps", status_code=303
        )
    await db.delete(contract)
    await db.flush()
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="employment_contract",
        entity_id=contract_id,
        entity_label=f"contrat #{contract_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{employee_id}", status_code=303)


@router.get("/rh/contracts/alerts", response_class=HTMLResponse)
async def contract_alerts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    # Contrats actifs porteurs d'une échéance (essai ou terme).
    contracts = list(
        (
            await db.execute(
                select(EmploymentContract).where(
                    EmploymentContract.status == "active",
                    or_(
                        EmploymentContract.end_date.is_not(None),
                        EmploymentContract.trial_end_date.is_not(None),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    alerts = [c for c in contracts if c.has_alert]
    alerts.sort(key=lambda c: (c.end_days_remaining if c.end_days_remaining is not None else 9999))
    emp_ids = {c.employee_id for c in alerts}
    employees = {
        e.id: e
        for e in (
            await db.execute(select(Employee).where(Employee.id.in_(emp_ids)))
        ).scalars().all()
    } if emp_ids else {}
    return templates.TemplateResponse(
        "staff/rh/contract_alerts.html",
        {
            "request": request,
            "user": user,
            "alerts": alerts,
            "employees": employees,
        },
    )


# ────────────────────────────────────────────────────────────────────
#                  Congés & absences sédentaires (L3)
# ────────────────────────────────────────────────────────────────────


def _absence_fields(f) -> dict:
    """Extrait/valide kind, dates, demi-journées, motif + décompte ouvré."""
    kind = (f.get("kind") or "").strip()
    if kind not in ABSENCE_KINDS:
        raise HTTPException(status_code=400, detail="type d'absence invalide")
    start = _opt_date(f.get("start_date"), "Du")
    end = _opt_date(f.get("end_date"), "Au")
    if not start or not end:
        raise HTTPException(status_code=400, detail="dates de début et de fin obligatoires")
    half_start = f.get("half_day_start") is not None
    half_end = f.get("half_day_end") is not None
    try:
        days = count_business_days(
            start, end, half_day_start=half_start, half_day_end=half_end
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "kind": kind,
        "start_date": start,
        "end_date": end,
        "half_day_start": half_start,
        "half_day_end": half_end,
        "business_days": days,
        "reason": (f.get("reason") or "").strip() or None,
    }


@router.get("/rh/absences", response_class=HTMLResponse)
async def absences_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
    status: str | None = None,
    employee_id: int | None = None,
) -> HTMLResponse:
    query = select(HrAbsence)
    if status in ABSENCE_STATUSES:
        query = query.where(HrAbsence.status == status)
    if employee_id:
        query = query.where(HrAbsence.employee_id == employee_id)
    absences = list(
        (await db.execute(query.order_by(HrAbsence.start_date.desc()).limit(200)))
        .scalars()
        .all()
    )
    employees = {
        e.id: e for e in (await db.execute(select(Employee))).scalars().all()
    }
    # File d'attente : requête indépendante des filtres d'historique
    # (sinon un filtre « approuvé » masquerait les demandes en attente).
    pending = list(
        (
            await db.execute(
                select(HrAbsence)
                .where(HrAbsence.status == "requested")
                .order_by(HrAbsence.start_date.desc())
            )
        )
        .scalars()
        .all()
    )
    active_employees = [
        e for e in employees.values() if e.status == "active"
    ]
    active_employees.sort(key=lambda e: (e.last_name, e.first_name))
    return templates.TemplateResponse(
        "staff/rh/absences.html",
        {
            "request": request,
            "user": user,
            "absences": absences,
            "employees": employees,
            "active_employees": active_employees,
            "pending": pending,
            "kinds": ABSENCE_KINDS,
            "statuses": ABSENCE_STATUSES,
            "f_status": status,
            "f_employee_id": employee_id,
        },
    )


@router.post("/rh/absences")
async def absence_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    f = await request.form()
    emp_id = _opt_int(f.get("employee_id"), "Collaborateur")
    emp = await db.get(Employee, emp_id) if emp_id else None
    if not emp:
        raise HTTPException(status_code=400, detail="collaborateur introuvable")
    data = _absence_fields(f)
    # Saisie RH : décision immédiate possible (autorité centrale).
    new_status = (f.get("status") or "approved").strip()
    if new_status not in ("requested", "approved"):
        raise HTTPException(status_code=400, detail="statut initial invalide")
    absence = HrAbsence(
        employee_id=emp_id,
        status=new_status,
        requested_by_id=user.id,
        **data,
    )
    if new_status == "approved":
        absence.decided_by_id = user.id
        absence.decided_at = datetime.now(UTC)
    db.add(absence)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="hr_absence",
        entity_id=absence.id,
        entity_label=f"{emp.full_name} — {absence.kind} ({absence.business_days} j)",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/rh/absences", status_code=303)


@router.post("/rh/absences/{absence_id}/decide")
async def absence_decide(
    absence_id: int,
    request: Request,
    decision: str = Form(...),  # 'approved' | 'rejected'
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    absence = await db.get(HrAbsence, absence_id)
    if not absence:
        raise HTTPException(status_code=404, detail="Not found")
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="décision invalide")
    absence.status = decision
    absence.decided_by_id = user.id
    absence.decided_at = datetime.now(UTC)
    await db.flush()
    await activity_record(
        db,
        action=decision,
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="hr_absence",
        entity_id=absence.id,
        entity_label=f"absence #{absence.id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/rh/absences", status_code=303)


# ────────────────────────────────────────────────────────────────────
#              Self-service collaborateur (L3 — consultation)
# ────────────────────────────────────────────────────────────────────


async def _my_employee(db: AsyncSession, user) -> Employee | None:
    """Fiche du collaborateur connecté (scoping strict par user_id)."""
    return (
        await db.execute(select(Employee).where(Employee.user_id == user.id))
    ).scalar_one_or_none()


async def _populate_topbar_state(request: Request, db: AsyncSession, user) -> None:
    """Alimente l'état topbar (badge notif + flag Agent) pour les pages
    self-service, qui n'utilisent pas ``require_permission`` (lequel le fait
    sur les pages RH). Sans cela, le badge cloche et le widget Agent
    afficheraient des valeurs par défaut figées.
    """
    try:
        from app.services.notifications import count_unread

        request.state.notif_count = await count_unread(
            db, user_id=user.id, user_role=user.role
        )
    except Exception:
        request.state.notif_count = 0
    try:
        from app.services.feature_flags import newtowt_agent_enabled

        request.state.newtowt_agent_enabled = await newtowt_agent_enabled(db)
    except Exception:
        request.state.newtowt_agent_enabled = True


@router.get("/rh/moi", response_class=HTMLResponse)
async def self_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> HTMLResponse:
    await _populate_topbar_state(request, db, user)
    emp = await _my_employee(db, user)
    contracts = []
    if emp:
        contracts = list(
            (
                await db.execute(
                    select(EmploymentContract)
                    .where(
                        EmploymentContract.employee_id == emp.id,
                        EmploymentContract.status == "active",
                    )
                    .order_by(EmploymentContract.start_date.desc())
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        "staff/rh/self_index.html",
        {"request": request, "user": user, "employee": emp, "contracts": contracts},
    )


@router.get("/rh/moi/absences", response_class=HTMLResponse)
async def self_absences(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> HTMLResponse:
    await _populate_topbar_state(request, db, user)
    emp = await _my_employee(db, user)
    absences = []
    if emp:
        absences = list(
            (
                await db.execute(
                    select(HrAbsence)
                    .where(HrAbsence.employee_id == emp.id)
                    .order_by(HrAbsence.start_date.desc())
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        "staff/rh/self_absences.html",
        {
            "request": request,
            "user": user,
            "employee": emp,
            "absences": absences,
            "kinds": ABSENCE_KINDS,
        },
    )


@router.post("/rh/moi/absences")
async def self_absence_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> RedirectResponse:
    emp = await _my_employee(db, user)
    if not emp:
        raise HTTPException(status_code=403, detail="aucune fiche collaborateur liée à ce compte")
    f = await request.form()
    data = _absence_fields(f)
    absence = HrAbsence(
        employee_id=emp.id,
        status="requested",
        requested_by_id=user.id,
        **data,
    )
    db.add(absence)
    await db.flush()
    await activity_record(
        db,
        action="request",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="hr_absence",
        entity_id=absence.id,
        entity_label=f"demande {absence.kind} ({absence.business_days} j)",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/rh/moi/absences", status_code=303)


@router.post("/rh/moi/absences/{absence_id}/cancel")
async def self_absence_cancel(
    absence_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> RedirectResponse:
    emp = await _my_employee(db, user)
    absence = await db.get(HrAbsence, absence_id)
    # Scoping : on n'annule que SA propre demande encore en attente.
    if not emp or not absence or absence.employee_id != emp.id:
        raise HTTPException(status_code=404, detail="Not found")
    if absence.status != "requested":
        raise HTTPException(status_code=400, detail="seules les demandes en attente sont annulables")
    absence.status = "cancelled"
    await db.flush()
    await activity_record(
        db,
        action="cancel",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="hr_absence",
        entity_id=absence.id,
        entity_label=f"annulation demande #{absence.id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/rh/moi/absences", status_code=303)


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
