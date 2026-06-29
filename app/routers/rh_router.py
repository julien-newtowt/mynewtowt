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
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
from app.models.hr_review import REVIEW_TYPES, HrReview
from app.models.payroll_variable import EVP_TYPES, PayrollVariable
from app.models.payslip import Payslip
from app.models.silae_export_batch import EXPORT_BATCH_STATUSES, SilaeExportBatch
from app.models.user import User
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.hr_absences import count_business_days
from app.services.hr_import import parse_employees_csv
from app.services.hr_reporting import age_bracket, age_on, seniority_years, turnover_rate
from app.services.payroll import (
    current_period,
    is_valid_period,
    overlaps_period,
    period_bounds,
    shift_period,
)
from app.services.silae_export import build_evp_csv
from app.templating import templates
from app.utils.file_validation import validate_upload

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


@router.get("/rh/conges", response_class=HTMLResponse)
async def rh_unified_leaves(
    request: Request,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    """EVO-02 — vue unifiée des congés marins (``CrewLeave``) et absences
    sédentaires (``HrAbsence``) derrière un service de lecture commun. Lecture
    transverse uniquement : la saisie/validation reste propre à chaque population
    (séparation des droits ``crew`` ↔ ``rh``)."""
    from app.services import leaves as leaves_svc

    status_f = status if status in ("requested", "approved", "rejected", "cancelled") else None
    rows = await leaves_svc.list_unified(db, status=status_f)
    return templates.TemplateResponse(
        "staff/rh/leaves_unified.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "summary": leaves_svc.summary(rows),
            "status_filter": status_f,
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
        (await db.execute(query.order_by(Employee.last_name, Employee.first_name))).scalars().all()
    )

    # Stats globales (indépendantes des filtres).
    total = (await db.execute(select(func.count(Employee.id)))).scalar_one()
    active = (
        await db.execute(select(func.count(Employee.id)).where(Employee.status == "active"))
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
                .order_by(EmploymentContract.start_date.desc(), EmploymentContract.id.desc())
            )
        )
        .scalars()
        .all()
    )
    # Contrats initiaux (non-avenants) proposables comme parent d'un avenant.
    base_contracts = [c for c in contracts if not c.is_amendment]
    payslips = list(
        (
            await db.execute(
                select(Payslip)
                .where(Payslip.employee_id == employee_id)
                .order_by(Payslip.period.desc())
            )
        )
        .scalars()
        .all()
    )
    reviews = list(
        (
            await db.execute(
                select(HrReview)
                .where(HrReview.employee_id == employee_id)
                .order_by(HrReview.review_date.desc())
            )
        )
        .scalars()
        .all()
    )
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
            "payslips": payslips,
            "reviews": reviews,
            "review_types": REVIEW_TYPES,
            "current_period": current_period(),
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
        raise HTTPException(
            status_code=400, detail="un collaborateur ne peut être son propre manager"
        )
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
        await db.execute(select(func.count(Employee.id)).where(Employee.manager_id == employee_id))
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
    employees = (
        {
            e.id: e
            for e in (await db.execute(select(Employee).where(Employee.id.in_(emp_ids))))
            .scalars()
            .all()
        }
        if emp_ids
        else {}
    )
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
        days = count_business_days(start, end, half_day_start=half_start, half_day_end=half_end)
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
        (await db.execute(query.order_by(HrAbsence.start_date.desc()).limit(200))).scalars().all()
    )
    employees = {e.id: e for e in (await db.execute(select(Employee))).scalars().all()}
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
    active_employees = [e for e in employees.values() if e.status == "active"]
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

        request.state.notif_count = await count_unread(db, user_id=user.id, user_role=user.role)
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
        raise HTTPException(
            status_code=400, detail="seules les demandes en attente sont annulables"
        )
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
#              Éléments variables de paie / EVP (L4)
# ────────────────────────────────────────────────────────────────────


def _period_is_locked(lines: list[PayrollVariable]) -> bool:
    """Une période est figée dès qu'une ligne est verrouillée ou exportée."""
    return any(line.status in ("locked", "exported") for line in lines)


@router.get("/rh/payroll", response_class=HTMLResponse)
async def payroll_redirect(
    user=Depends(require_permission("rh", "C")),
) -> RedirectResponse:
    return RedirectResponse(url=f"/rh/payroll/{current_period()}", status_code=303)


@router.get("/rh/payroll/{period}", response_class=HTMLResponse)
async def payroll_period(
    period: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    if not is_valid_period(period):
        raise HTTPException(status_code=404, detail="période invalide")
    lines = list(
        (
            await db.execute(
                select(PayrollVariable)
                .where(PayrollVariable.period == period)
                .order_by(PayrollVariable.employee_id, PayrollVariable.evp_type)
            )
        )
        .scalars()
        .all()
    )
    employees = {e.id: e for e in (await db.execute(select(Employee))).scalars().all()}
    active_employees = sorted(
        (e for e in employees.values() if e.status == "active"),
        key=lambda e: (e.last_name, e.first_name),
    )
    return templates.TemplateResponse(
        "staff/rh/payroll.html",
        {
            "request": request,
            "user": user,
            "period": period,
            "prev_period": shift_period(period, -1),
            "next_period": shift_period(period, 1),
            "lines": lines,
            "employees": employees,
            "active_employees": active_employees,
            "evp_types": EVP_TYPES,
            "locked": _period_is_locked(lines),
            "line_count": len(lines),
        },
    )


async def _load_period_lines(db: AsyncSession, period: str) -> list[PayrollVariable]:
    return list(
        (await db.execute(select(PayrollVariable).where(PayrollVariable.period == period)))
        .scalars()
        .all()
    )


@router.post("/rh/payroll/{period}/lines")
async def payroll_add_line(
    period: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    if not is_valid_period(period):
        raise HTTPException(status_code=404, detail="période invalide")
    if _period_is_locked(await _load_period_lines(db, period)):
        raise HTTPException(status_code=400, detail="période verrouillée")
    f = await request.form()
    emp_id = _opt_int(f.get("employee_id"), "Collaborateur")
    emp = await db.get(Employee, emp_id) if emp_id else None
    if not emp:
        raise HTTPException(status_code=400, detail="collaborateur introuvable")
    evp_type = _opt_str(f.get("evp_type"))
    if evp_type not in EVP_TYPES:
        raise HTTPException(status_code=400, detail="type d'EVP invalide")
    line = PayrollVariable(
        employee_id=emp_id,
        period=period,
        evp_type=evp_type,
        quantity=_opt_decimal(f.get("quantity"), "Quantité") or Decimal("0"),
        amount=_opt_decimal(f.get("amount"), "Montant"),
        comment=_opt_str(f.get("comment")),
        source="manual",
        status="draft",
        created_by_id=user.id,
    )
    db.add(line)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="payroll_variable",
        entity_id=line.id,
        entity_label=f"{emp.full_name} — {line.type_label} ({period})",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/payroll/{period}", status_code=303)


@router.post("/rh/payroll/{period}/lines/{line_id}/delete")
async def payroll_delete_line(
    period: str,
    line_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    line = await db.get(PayrollVariable, line_id)
    if not line or line.period != period:
        raise HTTPException(status_code=404, detail="Not found")
    if not line.is_editable:
        raise HTTPException(status_code=400, detail="ligne verrouillée — suppression impossible")
    await db.delete(line)
    await db.flush()
    return RedirectResponse(url=f"/rh/payroll/{period}", status_code=303)


@router.post("/rh/payroll/{period}/sync-absences")
async def payroll_sync_absences(
    period: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    if not is_valid_period(period):
        raise HTTPException(status_code=404, detail="période invalide")
    existing = await _load_period_lines(db, period)
    if _period_is_locked(existing):
        raise HTTPException(status_code=400, detail="période verrouillée")
    already = {line.absence_id for line in existing if line.absence_id is not None}
    first, last = period_bounds(period)
    # Absences approuvées recoupant le mois.
    approved = list(
        (
            await db.execute(
                select(HrAbsence).where(
                    HrAbsence.status == "approved",
                    HrAbsence.start_date <= last,
                    HrAbsence.end_date >= first,
                )
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for ab in approved:
        if ab.id in already or not overlaps_period(ab.start_date, ab.end_date, period):
            continue
        db.add(
            PayrollVariable(
                employee_id=ab.employee_id,
                period=period,
                evp_type="absence",
                quantity=ab.business_days,
                comment=f"{ab.kind} du {ab.start_date} au {ab.end_date}",
                source="absence",
                status="draft",
                absence_id=ab.id,
                created_by_id=user.id,
            )
        )
        created += 1
    await db.flush()
    return RedirectResponse(url=f"/rh/payroll/{period}?synced={created}", status_code=303)


@router.post("/rh/payroll/{period}/lock")
async def payroll_lock(
    period: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    if not is_valid_period(period):
        raise HTTPException(status_code=404, detail="période invalide")
    lines = await _load_period_lines(db, period)
    if not lines:
        raise HTTPException(status_code=400, detail="aucune ligne à verrouiller")
    for line in lines:
        if line.status == "draft":
            line.status = "locked"
    await db.flush()
    await activity_record(
        db,
        action="lock",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="payroll_period",
        entity_id=None,
        entity_label=f"période {period} verrouillée ({len(lines)} lignes)",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/payroll/{period}", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                  Export Silae + journal des lots (L5)
# ────────────────────────────────────────────────────────────────────


@router.post("/rh/payroll/{period}/export")
async def payroll_export(
    period: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    if not is_valid_period(period):
        raise HTTPException(status_code=404, detail="période invalide")
    # Seules les lignes verrouillées (et pas déjà exportées) partent à la paie.
    lines = list(
        (
            await db.execute(
                select(PayrollVariable).where(
                    PayrollVariable.period == period,
                    PayrollVariable.status == "locked",
                )
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        raise HTTPException(
            status_code=400,
            detail="aucune ligne verrouillée à exporter (verrouillez la période d'abord)",
        )
    employees = {
        e.id: e
        for e in (
            await db.execute(
                select(Employee).where(Employee.id.in_({line.employee_id for line in lines}))
            )
        )
        .scalars()
        .all()
    }
    rows = []
    for line in lines:
        emp = employees.get(line.employee_id)
        rows.append(
            {
                "matricule": emp.matricule if emp else "",
                "silae_id": (emp.silae_id if emp else "") or "",
                "nom": emp.last_name if emp else "",
                "prenom": emp.first_name if emp else "",
                "periode": line.period,
                "type_evp": line.evp_type,
                "libelle": line.type_label,
                "quantite": line.quantity,
                "montant": "" if line.amount is None else line.amount,
                "commentaire": line.comment or "",
            }
        )
    csv_content = build_evp_csv(rows)
    batch = SilaeExportBatch(
        period=period,
        kind="evp",
        format="csv",
        content=csv_content,
        line_count=len(lines),
        status="generated",
        created_by_id=user.id,
    )
    db.add(batch)
    await db.flush()
    # Fige les lignes : exportées + rattachées au lot (idempotence garantie).
    for line in lines:
        line.status = "exported"
        line.export_batch_id = batch.id
    await db.flush()
    await activity_record(
        db,
        action="export",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="silae_export_batch",
        entity_id=batch.id,
        entity_label=f"export EVP {period} ({len(lines)} lignes)",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/rh/exports", status_code=303)


@router.get("/rh/exports", response_class=HTMLResponse)
async def exports_journal(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    batches = list(
        (await db.execute(select(SilaeExportBatch).order_by(SilaeExportBatch.created_at.desc())))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/rh/exports.html",
        {
            "request": request,
            "user": user,
            "batches": batches,
            "statuses": EXPORT_BATCH_STATUSES,
        },
    )


@router.get("/rh/exports/{batch_id}/download")
async def export_download(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> Response:
    batch = await db.get(SilaeExportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=batch.content or "",
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{batch.filename}"'},
    )


@router.post("/rh/exports/{batch_id}/status")
async def export_set_status(
    batch_id: int,
    request: Request,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    batch = await db.get(SilaeExportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Not found")
    if status not in EXPORT_BATCH_STATUSES:
        raise HTTPException(status_code=400, detail="statut de lot invalide")
    batch.status = status
    await db.flush()
    return RedirectResponse(url="/rh/exports", status_code=303)


# ────────────────────────────────────────────────────────────────────
#        Coffre-fort bulletins + entretiens + reporting (L6)
# ────────────────────────────────────────────────────────────────────


@router.post("/rh/employees/{employee_id}/payslips")
async def payslip_upload(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
    file: UploadFile | None = None,
    period: str = Form(...),
) -> RedirectResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    if not is_valid_period(period):
        raise HTTPException(status_code=400, detail="période invalide (AAAA-MM)")
    raw = await file.read() if file else b""
    if not raw:
        raise HTTPException(status_code=400, detail="fichier manquant")
    result = validate_upload(file.filename or "bulletin.pdf", raw)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.reason or "fichier invalide")
    if result.detected_mime != "application/pdf":
        raise HTTPException(status_code=400, detail="le bulletin doit être un PDF")
    payslip = Payslip(
        employee_id=employee_id,
        period=period,
        filename=file.filename or f"bulletin-{period}.pdf",
        content=raw,
        file_size=len(raw),
        uploaded_by_id=user.id,
    )
    db.add(payslip)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="payslip",
        entity_id=payslip.id,
        entity_label=f"bulletin {emp.full_name} {period}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{employee_id}", status_code=303)


def _payslip_response(payslip: Payslip) -> Response:
    return Response(
        content=payslip.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{payslip.filename}"'},
    )


@router.get("/rh/payslips/{payslip_id}/download")
async def payslip_download(
    payslip_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> Response:
    payslip = await db.get(Payslip, payslip_id)
    if not payslip:
        raise HTTPException(status_code=404, detail="Not found")
    await activity_record(
        db,
        action="download",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="payslip",
        entity_id=payslip.id,
        entity_label=f"bulletin #{payslip.id} ({payslip.period})",
        ip_address=_client_ip(request),
    )
    return _payslip_response(payslip)


@router.post("/rh/payslips/{payslip_id}/delete")
async def payslip_delete(
    payslip_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "S")),
) -> RedirectResponse:
    payslip = await db.get(Payslip, payslip_id)
    if not payslip:
        raise HTTPException(status_code=404, detail="Not found")
    employee_id = payslip.employee_id
    await db.delete(payslip)
    await db.flush()
    return RedirectResponse(url=f"/rh/employees/{employee_id}", status_code=303)


@router.post("/rh/employees/{employee_id}/reviews")
async def review_create(
    employee_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "M")),
) -> RedirectResponse:
    emp = await db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Not found")
    f = await request.form()
    review_type = _opt_str(f.get("review_type"))
    if review_type not in REVIEW_TYPES:
        raise HTTPException(status_code=400, detail="type d'entretien invalide")
    review_date = _opt_date(f.get("review_date"), "Date de l'entretien")
    if not review_date:
        raise HTTPException(status_code=400, detail="date de l'entretien obligatoire")
    review = HrReview(
        employee_id=employee_id,
        review_type=review_type,
        review_date=review_date,
        next_due_date=_opt_date(f.get("next_due_date"), "Prochaine échéance"),
        summary=_opt_str(f.get("summary")),
        created_by_id=user.id,
    )
    db.add(review)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="hr_review",
        entity_id=review.id,
        entity_label=f"entretien {emp.full_name} ({review.type_label})",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/rh/employees/{employee_id}", status_code=303)


@router.post("/rh/reviews/{review_id}/delete")
async def review_delete(
    review_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "S")),
) -> RedirectResponse:
    review = await db.get(HrReview, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Not found")
    employee_id = review.employee_id
    await db.delete(review)
    await db.flush()
    return RedirectResponse(url=f"/rh/employees/{employee_id}", status_code=303)


# ── Self-service : mes bulletins ────────────────────────────────────────


@router.get("/rh/moi/bulletins", response_class=HTMLResponse)
async def self_payslips(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> HTMLResponse:
    await _populate_topbar_state(request, db, user)
    emp = await _my_employee(db, user)
    payslips = []
    if emp:
        payslips = list(
            (
                await db.execute(
                    select(Payslip)
                    .where(Payslip.employee_id == emp.id)
                    .order_by(Payslip.period.desc())
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        "staff/rh/self_payslips.html",
        {"request": request, "user": user, "employee": emp, "payslips": payslips},
    )


@router.get("/rh/moi/bulletins/{payslip_id}/download")
async def self_payslip_download(
    payslip_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> Response:
    emp = await _my_employee(db, user)
    payslip = await db.get(Payslip, payslip_id)
    # Scoping : on ne télécharge que SES propres bulletins.
    if not emp or not payslip or payslip.employee_id != emp.id:
        raise HTTPException(status_code=404, detail="Not found")
    await activity_record(
        db,
        action="download",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="rh",
        entity_type="payslip",
        entity_id=payslip.id,
        entity_label=f"self-bulletin #{payslip.id} ({payslip.period})",
        ip_address=_client_ip(request),
    )
    return _payslip_response(payslip)


# ── Reporting RH ────────────────────────────────────────────────────────


async def _reporting_data(db: AsyncSession) -> dict:
    employees = list((await db.execute(select(Employee))).scalars().all())
    active = [e for e in employees if e.status == "active"]
    today = date.today()
    year = today.year

    by_department: dict[str, int] = {}
    for e in active:
        by_department[e.department or "—"] = by_department.get(e.department or "—", 0) + 1

    by_bracket: dict[str, int] = {}
    for e in active:
        if e.birth_date:
            b = age_bracket(age_on(e.birth_date, today))
            by_bracket[b] = by_bracket.get(b, 0) + 1

    seniorities = [seniority_years(e.entry_date, today) for e in active if e.entry_date]
    avg_seniority = round(sum(seniorities) / len(seniorities), 1) if seniorities else 0.0

    entries_year = sum(1 for e in employees if e.entry_date and e.entry_date.year == year)
    exits_year = sum(1 for e in employees if e.exit_date and e.exit_date.year == year)

    # Masse salariale = somme des bruts des contrats actifs (approx. v1).
    contracts = list(
        (await db.execute(select(EmploymentContract).where(EmploymentContract.status == "active")))
        .scalars()
        .all()
    )
    payroll_mass = sum((c.gross_monthly or 0) for c in contracts)

    # Absentéisme : jours ouvrés d'absences approuvées de l'année, par type.
    absences = list(
        (await db.execute(select(HrAbsence).where(HrAbsence.status == "approved"))).scalars().all()
    )
    absence_days: dict[str, float] = {}
    for ab in absences:
        if ab.start_date.year == year or ab.end_date.year == year:
            absence_days[ab.kind] = absence_days.get(ab.kind, 0) + float(ab.business_days)

    return {
        "headcount": len(active),
        "by_department": dict(sorted(by_department.items())),
        "by_bracket": {b: by_bracket.get(b, 0) for b in ("<25", "25-34", "35-44", "45-54", "55+")},
        "avg_seniority": avg_seniority,
        "entries_year": entries_year,
        "exits_year": exits_year,
        "turnover": turnover_rate(exits_year, len(active)),
        "payroll_mass": payroll_mass,
        "absence_days": dict(sorted(absence_days.items())),
        "year": year,
    }


@router.get("/rh/reporting", response_class=HTMLResponse)
async def reporting(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> HTMLResponse:
    data = await _reporting_data(db)
    return templates.TemplateResponse(
        "staff/rh/reporting.html",
        {"request": request, "user": user, **data},
    )


@router.get("/rh/reporting/export.csv")
async def reporting_export(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("rh", "C")),
) -> Response:
    d = await _reporting_data(db)
    lines = [
        "indicateur;valeur",
        f"effectif_actif;{d['headcount']}",
        f"anciennete_moyenne_ans;{d['avg_seniority']}",
        f"entrees_{d['year']};{d['entries_year']}",
        f"sorties_{d['year']};{d['exits_year']}",
        f"turnover_pct;{d['turnover']}",
        f"masse_salariale_brute;{d['payroll_mass']}",
    ]
    for dept, n in d["by_department"].items():
        lines.append(f"effectif_service_{dept};{n}")
    for bracket, n in d["by_bracket"].items():
        lines.append(f"pyramide_{bracket};{n}")
    for kind, days in d["absence_days"].items():
        lines.append(f"absences_{kind}_jours;{days}")
    return Response(
        content="﻿" + "\n".join(lines) + "\n",
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="reporting_rh.csv"'},
    )


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
    existing = {m for (m,) in (await db.execute(select(Employee.matricule))).all()}
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
