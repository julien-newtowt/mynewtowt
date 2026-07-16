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

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    get_current_staff,
    hash_password,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.i18n import SUPPORTED as SUPPORTED_LANGS
from app.models.activity_log import ActivityLog
from app.models.co2_variable import Co2Variable
from app.models.emission_factor import EmissionFactor
from app.models.finance import OpexParameter
from app.models.insurance import INSURANCE_KINDS, InsuranceContract
from app.models.role_permission import RolePermission
from app.models.user import User
from app.models.vessel import Vessel
from app.permissions import (
    MODULES,
    ROLES,
    VALID_LEVELS,
    get_default_matrix,
    get_effective_matrix,
    invalidate_permissions_cache,
    require_permission,
)
from app.services import co2 as co2_service
from app.services import emissions as emissions_service
from app.services import referential_env
from app.services.activity import record as activity_record
from app.templating import templates
from app.utils import marad, pipedrive

router = APIRouter(prefix="/admin", tags=["admin-enriched"])

# Suppression B108 justifiée : cf. app/middlewares/maintenance.py.
MAINTENANCE_MARKER = Path("/tmp/.maintenance")  # nosec B108


# ────────────────────────────────────────────── Users CRUD
async def _vessels_for_form(db: AsyncSession) -> list[Vessel]:
    return list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())


@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    users = list((await db.execute(select(User).order_by(User.username))).scalars().all())
    vessels = await _vessels_for_form(db)
    # Map vessel_id → code pour afficher le navire de rattachement en liste
    vessel_codes = {v.id: v.code for v in vessels}
    return templates.TemplateResponse(
        "staff/admin/users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "roles": ROLES,
            "vessels": vessels,
            "vessel_codes": vessel_codes,
            "languages": list(SUPPORTED_LANGS),
            "edit_user": None,
        },
    )


@router.post("/users")
async def users_create(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    full_name: str | None = Form(None),
    role: str = Form(...),
    password: str = Form(...),
    language: str = Form("fr"),
    assigned_vessel_id: str = Form(""),
    must_change_password: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="invalid role")
    if language not in SUPPORTED_LANGS:
        language = "fr"
    if len(password) < 12:
        raise HTTPException(status_code=400, detail="mot de passe trop court (12 caractères min)")
    username_clean = username.strip()
    email_clean = email.strip().lower()
    existing = (
        await db.execute(
            select(User).where((User.username == username_clean) | (User.email == email_clean))
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="utilisateur déjà existant (username ou email)")

    vessel_id = int(assigned_vessel_id) if assigned_vessel_id.strip() else None
    if vessel_id is not None and (await db.get(Vessel, vessel_id)) is None:
        raise HTTPException(status_code=400, detail="navire de rattachement inconnu")

    new_user = User(
        username=username_clean,
        email=email_clean,
        full_name=(full_name or "").strip() or None,
        hashed_password=hash_password(password),
        role=role,
        language=language,
        assigned_vessel_id=vessel_id,
        must_change_password=must_change_password,
    )
    db.add(new_user)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=new_user.id,
        entity_label=new_user.username,
        detail=f"role={role} lang={language}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ────────────────────────────────────────────── ADM-05 — import utilisateurs
@router.get("/users/import", response_class=HTMLResponse)
async def users_import_form(
    request: Request,
    user=Depends(require_permission("admin", "M")),
) -> HTMLResponse:
    """ADM-05 — écran d'import Excel (modèle + dépôt + rapport)."""
    from app.services.user_import import IMPORT_COLUMNS

    return templates.TemplateResponse(
        "staff/admin/users_import.html",
        {"request": request, "user": user, "columns": IMPORT_COLUMNS, "report": None},
    )


@router.get("/users/import/template.xlsx")
async def users_import_template(
    user=Depends(require_permission("admin", "M")),
) -> Response:
    """ADM-05 — modèle Excel d'import utilisateurs."""
    from app.services.user_import import XLSX_MIME, build_template_xlsx

    return Response(
        content=build_template_xlsx(),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="users_import_template.xlsx"'},
    )


@router.post("/users/import", response_class=HTMLResponse)
async def users_import(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> HTMLResponse:
    """ADM-05 — importe les utilisateurs et restitue le rapport."""
    from app.services.safe_files import content_length_exceeds_max
    from app.services.user_import import IMPORT_COLUMNS, import_users, parse_users_xlsx
    from app.utils.file_validation import validate_size

    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")
    content = await file.read()
    size_check = validate_size(content)
    if not size_check.ok:
        raise HTTPException(status_code=413, detail=size_check.reason)
    try:
        rows = parse_users_xlsx(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="fichier Excel illisible") from exc
    report = await import_users(db, rows)
    await activity_record(
        db,
        action="import",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_label="import Excel",
        detail=f"{len(report['created'])} créés, {len(report['skipped'])} ignorés",
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/admin/users_import.html",
        {"request": request, "user": user, "columns": IMPORT_COLUMNS, "report": report},
    )


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def users_edit_form(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    users = list((await db.execute(select(User).order_by(User.username))).scalars().all())
    vessels = await _vessels_for_form(db)
    return templates.TemplateResponse(
        "staff/admin/users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "roles": ROLES,
            "vessels": vessels,
            "vessel_codes": {v.id: v.code for v in vessels},
            "languages": list(SUPPORTED_LANGS),
            "edit_user": target,
        },
    )


@router.post("/users/{user_id}/edit")
async def users_edit_submit(
    user_id: int,
    request: Request,
    email: str = Form(...),
    full_name: str | None = Form(None),
    role: str = Form(...),
    language: str = Form("fr"),
    assigned_vessel_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="invalid role")
    if language not in SUPPORTED_LANGS:
        language = "fr"
    email_clean = email.strip().lower()
    # Unicité email (hors lui-même)
    clash = (
        await db.execute(select(User).where(User.email == email_clean, User.id != user_id))
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail="email déjà utilisé par un autre compte")

    vessel_id = int(assigned_vessel_id) if assigned_vessel_id.strip() else None
    if vessel_id is not None and (await db.get(Vessel, vessel_id)) is None:
        raise HTTPException(status_code=400, detail="navire de rattachement inconnu")

    target.email = email_clean
    target.full_name = (full_name or "").strip() or None
    target.role = role
    target.language = language
    target.assigned_vessel_id = vessel_id
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=target.id,
        entity_label=target.username,
        detail=f"role={role} lang={language} vessel={vessel_id}",
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
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=target.id,
        entity_label=target.username,
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
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=target.id,
        entity_label=target.username,
        detail="password reset",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/users", status_code=303)


# ────────────────────────────────────────────── Vessels CRUD (ADM-01)


def _vessel_float(value: str | None) -> float | None:
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="valeur numérique invalide") from exc


def _apply_vessel_form(
    vessel: Vessel,
    *,
    name: str,
    vessel_class: str,
    imo_number: str | None,
    flag: str | None,
    dwt: str | None,
    capacity_palettes: str | None,
    default_speed_kn: str | None,
    default_elongation: str | None,
    opex_daily_sea_eur: str | None,
) -> None:
    vessel.name = name.strip()
    vessel.vessel_class = (vessel_class or "phoenix").strip() or "phoenix"
    vessel.imo_number = (imo_number or "").strip() or None
    vessel.flag = (flag or "").strip().upper()[:2] or None
    vessel.dwt = _vessel_float(dwt)
    cap = _vessel_float(capacity_palettes)
    if cap is not None:
        vessel.capacity_palettes = int(cap)
    spd = _vessel_float(default_speed_kn)
    if spd is not None:
        vessel.default_speed_kn = spd
    elong = _vessel_float(default_elongation)
    if elong is not None:
        vessel.default_elongation = elong
    vessel.opex_daily_sea_eur = _vessel_float(opex_daily_sea_eur)


@router.get("/vessels", response_class=HTMLResponse)
async def vessels_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    vessels = await _vessels_for_form(db)
    return templates.TemplateResponse(
        "staff/admin/vessels.html",
        {"request": request, "user": user, "vessels": vessels},
    )


@router.get("/vessels/new", response_class=HTMLResponse)
async def vessel_new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "staff/admin/vessel_form.html",
        {"request": request, "user": user, "vessel": None},
    )


@router.post("/vessels")
async def vessel_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    vessel_class: str = Form("phoenix"),
    imo_number: str | None = Form(None),
    flag: str | None = Form(None),
    dwt: str | None = Form(None),
    capacity_palettes: str | None = Form(None),
    default_speed_kn: str | None = Form(None),
    default_elongation: str | None = Form(None),
    opex_daily_sea_eur: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    code_clean = code.strip().upper()
    if not code_clean or len(code_clean) > 4:
        raise HTTPException(status_code=400, detail="code navire invalide (1 à 4 caractères)")
    existing = (
        await db.execute(select(Vessel).where(Vessel.code == code_clean))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="code navire déjà utilisé")
    vessel = Vessel(code=code_clean)
    _apply_vessel_form(
        vessel,
        name=name,
        vessel_class=vessel_class,
        imo_number=imo_number,
        flag=flag,
        dwt=dwt,
        capacity_palettes=capacity_palettes,
        default_speed_kn=default_speed_kn,
        default_elongation=default_elongation,
        opex_daily_sea_eur=opex_daily_sea_eur,
    )
    db.add(vessel)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="vessel",
        entity_id=vessel.id,
        entity_label=vessel.code,
        detail=f"class={vessel.vessel_class}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/vessels", status_code=303)


@router.get("/vessels/{vessel_id}/edit", response_class=HTMLResponse)
async def vessel_edit_form(
    vessel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> HTMLResponse:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "staff/admin/vessel_form.html",
        {"request": request, "user": user, "vessel": vessel},
    )


@router.post("/vessels/{vessel_id}/edit")
async def vessel_edit(
    vessel_id: int,
    request: Request,
    name: str = Form(...),
    vessel_class: str = Form("phoenix"),
    imo_number: str | None = Form(None),
    flag: str | None = Form(None),
    dwt: str | None = Form(None),
    capacity_palettes: str | None = Form(None),
    default_speed_kn: str | None = Form(None),
    default_elongation: str | None = Form(None),
    opex_daily_sea_eur: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404)
    _apply_vessel_form(
        vessel,
        name=name,
        vessel_class=vessel_class,
        imo_number=imo_number,
        flag=flag,
        dwt=dwt,
        capacity_palettes=capacity_palettes,
        default_speed_kn=default_speed_kn,
        default_elongation=default_elongation,
        opex_daily_sea_eur=opex_daily_sea_eur,
    )
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="vessel",
        entity_id=vessel.id,
        entity_label=vessel.code,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/vessels", status_code=303)


@router.post("/vessels/{vessel_id}/toggle")
async def vessel_toggle_active(
    vessel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404)
    vessel.is_active = not vessel.is_active
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="vessel",
        entity_id=vessel.id,
        entity_label=vessel.code,
        detail="réactivé" if vessel.is_active else "désactivé",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/vessels", status_code=303)


# ────────────────────────────────────────────── OPEX parameters
@router.get("/opex", response_class=HTMLResponse)
async def opex_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    params = list(
        (
            await db.execute(
                select(OpexParameter).order_by(OpexParameter.category, OpexParameter.parameter_name)
            )
        )
        .scalars()
        .all()
    )
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
    existing = (
        await db.execute(
            select(OpexParameter).where(OpexParameter.parameter_name == parameter_name)
        )
    ).scalar_one_or_none()
    if existing is None:
        p = OpexParameter(
            parameter_name=parameter_name,
            parameter_value=parameter_value,
            unit=unit,
            category=category,
            description=description,
        )
        db.add(p)
    else:
        existing.parameter_value = parameter_value
        existing.unit = unit
        existing.category = category
        existing.description = description
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="opex_parameter",
        entity_id=None,
        entity_label=parameter_name,
        detail=f"value={parameter_value}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/opex", status_code=303)


# ────────────────────────────────────────────── Stowage zone specs (référentiel)
@router.get("/stowage-specs", response_class=HTMLResponse)
async def stowage_specs_list(
    request: Request,
    vessel_class: str = "phoenix",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    """Référentiel d'arrimage par classe de navire (capacités & résistances).

    Seed idempotent à l'ouverture (matérialise le référentiel théorique en DB
    pour permettre l'édition zone par zone).
    """
    from app.services.stowage_specs import ensure_specs

    classes = list((await db.execute(select(Vessel.vessel_class).distinct())).scalars().all())
    classes = sorted({c for c in classes if c} | {"phoenix"})
    specs = await ensure_specs(db, vessel_class)
    # Ordre de chargement pour un affichage cohérent.
    from app.models.stowage import ZONE_LOADING_ORDER

    ordered = [specs[z] for z in ZONE_LOADING_ORDER if z in specs]
    return templates.TemplateResponse(
        "staff/admin/stowage_specs.html",
        {
            "request": request,
            "user": user,
            "specs": ordered,
            "vessel_class": vessel_class,
            "classes": classes,
        },
    )


@router.post("/stowage-specs")
async def stowage_specs_update(
    request: Request,
    vessel_class: str = Form(...),
    zone: str = Form(...),
    capacity_epal: int = Form(...),
    max_load_t: float | None = Form(None),
    max_pallet_weight_kg: float | None = Form(None),
    stack_allowed: bool = Form(False),
    heavy_stack_allowed: bool = Form(False),
    segregated: bool = Form(False),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    from app.models.stowage import StowageZoneSpec

    existing = (
        await db.execute(
            select(StowageZoneSpec).where(
                StowageZoneSpec.vessel_class == vessel_class,
                StowageZoneSpec.zone == zone,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = StowageZoneSpec(vessel_class=vessel_class, zone=zone)
        db.add(existing)
    existing.capacity_epal = capacity_epal
    existing.max_load_t = max_load_t
    existing.max_pallet_weight_kg = max_pallet_weight_kg
    existing.stack_allowed = stack_allowed
    existing.heavy_stack_allowed = heavy_stack_allowed
    existing.segregated = segregated
    existing.notes = notes
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="stowage_zone_spec",
        entity_id=existing.id,
        entity_label=f"{vessel_class}/{zone}",
        detail=f"cap={capacity_epal} max_t={max_load_t}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(
        url=f"/admin/stowage-specs?vessel_class={vessel_class}", status_code=303
    )


# ────────────────────────────────────────────── Insurance contracts
@router.get("/insurance", response_class=HTMLResponse)
async def insurance_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    contracts = list(
        (await db.execute(select(InsuranceContract).order_by(InsuranceContract.valid_to.desc())))
        .scalars()
        .all()
    )
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
        kind=kind,
        reference=reference,
        insurer=insurer,
        broker=broker,
        valid_from=_date.fromisoformat(valid_from),
        valid_to=_date.fromisoformat(valid_to),
        premium_eur=premium_eur,
        deductible_eur=deductible_eur,
        coverage_amount_eur=coverage_amount_eur,
        notes=notes,
    )
    db.add(c)
    await db.flush()
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="insurance_contract",
        entity_id=c.id,
        entity_label=reference,
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
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="maintenance",
        entity_label="enabled",
        ip_address=_client_ip(request),
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
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="maintenance",
        entity_label="disabled",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/maintenance", status_code=303)


# ─────────────────────────────────────── Newtowt Agent (chatbot) toggle
@router.post("/newtowt-agent")
async def newtowt_agent_toggle(
    request: Request,
    enabled: str = Form("off"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Active / désactive le Newtowt Agent (chatbot Kairos AI) — toggle config."""
    from app.services.feature_flags import set_newtowt_agent

    on = enabled in ("on", "true", "1", "yes")
    await set_newtowt_agent(db, on, user_id=user.id)
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="feature_flag",
        entity_label="newtowt_agent=" + ("on" if on else "off"),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin", status_code=303)


# ─────────────────────────────────────── ADM-07 — Intégrations externes
@router.get("/integrations", response_class=HTMLResponse)
async def integrations(
    request: Request,
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    """État des intégrations externes + test de connexion (Pipedrive).

    Le jeton reste piloté par l'environnement (``PIPEDRIVE_API_TOKEN``) —
    source de vérité ; cet écran expose l'état de configuration et permet de
    vérifier la connectivité sans manipuler le secret côté UI.
    """
    return templates.TemplateResponse(
        "staff/admin/integrations.html",
        {
            "request": request,
            "user": user,
            "pd_enabled": pipedrive.enabled(),
            "pd_base_url": pipedrive.PIPEDRIVE_BASE_URL,
            "pd_pipeline": settings.pipedrive_pipeline_name,
            "marad_enabled": marad.enabled(),
            "marad_base_url": settings.marad_base_url,
            "marad_header": settings.marad_api_key_header or "auto (sondage)",
        },
    )


@router.post("/integrations/pipedrive/test", response_class=HTMLResponse)
async def integrations_pipedrive_test(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> HTMLResponse:
    """Teste la connexion Pipedrive (``GET /users/me``) et renvoie un badge HTMX."""
    try:
        ok = await pipedrive.ping()
    except Exception:
        ok = False
    await activity_record(
        db,
        action="test",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="integration",
        entity_label="pipedrive",
        detail="ok" if ok else "échec",
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/admin/_integration_test_result.html",
        {"request": request, "ok": ok, "configured": pipedrive.enabled()},
    )


@router.post("/integrations/marad/test", response_class=HTMLResponse)
async def integrations_marad_test(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
) -> HTMLResponse:
    """Teste la connexion Marad et renvoie un badge HTMX.

    Utilise ``marad.diagnose()`` : distingue hôte injoignable, auth refusée,
    chemin inconnu, quota (429) et succès — sans jamais exposer le jeton
    (``diagnose`` le masque). Journalise le test (classification uniquement).
    """
    try:
        diag = await marad.diagnose()
    except Exception:  # pragma: no cover - garde-fou, ne casse jamais l'UI
        diag = {"configured": marad.enabled(), "classification": "http_error"}
    await activity_record(
        db,
        action="test",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="integration",
        entity_label="marad",
        detail=diag.get("classification", "?"),
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/admin/_marad_test_result.html",
        {"request": request, "diag": diag},
    )


# ────────────────────────────────────────────── Activity log viewer
@router.get("/activity-logs", response_class=HTMLResponse)
async def activity_logs_view(
    request: Request,
    module: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    page: int = 1,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    """ADM-08 — viewer d'audit : filtres module/action/**utilisateur** +
    **pagination**. Le filtre acteur est une recherche partielle insensible à la
    casse sur ``user_name`` (valeur bindée — pas de f-string SQL)."""
    limit = max(10, min(limit, 500))
    page = max(1, page)
    stmt = select(ActivityLog).order_by(ActivityLog.created_at.desc())
    if module:
        stmt = stmt.where(ActivityLog.module == module)
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    if actor:
        stmt = stmt.where(ActivityLog.user_name.ilike(f"%{actor}%"))
    # On lit ``limit + 1`` lignes pour détecter la page suivante sans COUNT.
    rows = list(
        (await db.execute(stmt.offset((page - 1) * limit).limit(limit + 1))).scalars().all()
    )
    has_next = len(rows) > limit
    logs = rows[:limit]

    # Aggregate counts for filter chips (sur la page courante)
    modules_count: dict[str, int] = {}
    for log in logs:
        modules_count[log.module or "—"] = modules_count.get(log.module or "—", 0) + 1

    return templates.TemplateResponse(
        "staff/admin/activity_logs.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "modules_count": modules_count,
            "filter_module": module,
            "filter_action": action,
            "filter_actor": actor,
            "page": page,
            "has_prev": page > 1,
            "has_next": has_next,
        },
    )


# ────────────────────────────────────────────── Security audit
@router.get("/security", response_class=HTMLResponse)
async def security_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    """Tableau de bord sécurité — adoption MFA TOTP par compte."""
    from app.models.client_account import ClientAccount
    from app.models.user import User

    staff_users = list(
        (
            await db.execute(
                select(User).where(User.is_active.is_(True)).order_by(User.role, User.username)
            )
        )
        .scalars()
        .all()
    )
    clients = list(
        (
            await db.execute(
                select(ClientAccount)
                .where(ClientAccount.is_verified.is_(True))
                .order_by(ClientAccount.company_name)
            )
        )
        .scalars()
        .all()
    )

    def _bucket(items, get_mfa):
        total = len(items)
        mfa_on = sum(1 for x in items if get_mfa(x))
        return {"total": total, "mfa_on": mfa_on, "none_on": total - mfa_on}

    stats_staff = _bucket(staff_users, lambda u: u.mfa_enabled)
    stats_client = _bucket(clients, lambda c: c.mfa_enabled)

    SENSITIVE_ROLES = ("administrateur", "manager_maritime")
    risky_staff = [u for u in staff_users if u.role in SENSITIVE_ROLES and not u.mfa_enabled]

    return templates.TemplateResponse(
        "staff/admin/security_dashboard.html",
        {
            "request": request,
            "user": user,
            "staff_users": staff_users,
            "clients": clients,
            "stats_staff": stats_staff,
            "stats_client": stats_client,
            "risky_staff": risky_staff,
            "require_mfa_for_admin": settings.require_mfa_for_admin,
        },
    )


@router.post("/users/{user_id}/reset-mfa")
async def users_reset_mfa(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Réinitialise la MFA d'un utilisateur (perte de téléphone, etc.).

    Désactive MFA TOTP + purge le secret + supprime les recovery codes.
    Désactive MFA TOTP + purge le secret + supprime les recovery codes.
    Si require_mfa_for_admin est actif et que la cible est admin, elle
    sera redirigée vers la reconfiguration MFA à sa prochaine requête.
    """
    from sqlalchemy import delete

    from app.models.mfa_recovery_code import MfaRecoveryCode

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    target.mfa_enabled = False
    target.mfa_secret = None
    await db.flush()
    await db.execute(
        delete(MfaRecoveryCode)
        .where(MfaRecoveryCode.owner_type == "staff")
        .where(MfaRecoveryCode.owner_id == target.id)
    )
    await activity_record(
        db,
        action="staff_mfa_reset_by_admin",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=target.id,
        entity_label=target.username,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/security", status_code=303)


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
            {"request": request, "user": user, "error": "Mot de passe actuel incorrect."},
            status_code=400,
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "staff/admin/change_password.html",
            {
                "request": request,
                "user": user,
                "error": "Les deux nouveaux mots de passe diffèrent.",
            },
            status_code=400,
        )
    if len(new_password) < 12:
        return templates.TemplateResponse(
            "staff/admin/change_password.html",
            {
                "request": request,
                "user": user,
                "error": "Mot de passe trop court (12 caractères minimum).",
            },
            status_code=400,
        )
    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=user.id,
        entity_label=user.username,
        detail="password changed",
        ip_address=_client_ip(request),
    )
    if user.email:
        from app.services import security_alerts

        await security_alerts.notify_password_changed(
            to_email=user.email,
            recipient_name=user.full_name or user.username,
            ip=_client_ip(request),
            ua=request.headers.get("user-agent"),
        )
    return RedirectResponse(url="/dashboard", status_code=303)


# ─────────────────────────────────────────────────────────────────────
#                    MFA TOTP staff — setup / verify / disable
# ─────────────────────────────────────────────────────────────────────


@router.get("/my-account/mfa", response_class=HTMLResponse)
async def staff_mfa_setup_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
) -> HTMLResponse:
    from app.services import mfa

    qr = None
    uri = None
    secret = None
    if not user.mfa_enabled:
        if not user.mfa_secret:
            user.mfa_secret = mfa.generate_secret()
            await db.flush()
        secret = user.mfa_secret
        uri = mfa.provisioning_uri(secret, user.email or user.username)
        qr = mfa.qr_data_uri(uri)
    return templates.TemplateResponse(
        "staff/admin/mfa_setup.html",
        {
            "request": request,
            "user": user,
            "qr_data_uri": qr,
            "otpauth_uri": uri,
            "secret": secret,
            "error": None,
        },
    )


@router.post("/my-account/mfa/verify", response_class=HTMLResponse)
async def staff_mfa_verify(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    from app.services import mfa

    if user.mfa_enabled or not user.mfa_secret:
        return RedirectResponse(url="/admin/my-account/mfa", status_code=303)
    if not mfa.verify_totp(user.mfa_secret, code):
        uri = mfa.provisioning_uri(user.mfa_secret, user.email or user.username)
        return templates.TemplateResponse(
            "staff/admin/mfa_setup.html",
            {
                "request": request,
                "user": user,
                "qr_data_uri": mfa.qr_data_uri(uri),
                "otpauth_uri": uri,
                "secret": user.mfa_secret,
                "error": "Code incorrect — réessayez.",
            },
            status_code=400,
        )
    user.mfa_enabled = True
    await db.flush()
    recovery_codes = await mfa.generate_recovery_codes(
        db,
        owner_type="staff",
        owner_id=user.id,
    )
    await activity_record(
        db,
        action="staff_mfa_enabled",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=user.id,
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/admin/mfa_recovery_codes.html",
        {"request": request, "user": user, "codes": recovery_codes, "is_regeneration": False},
    )


@router.post("/my-account/mfa/regenerate", response_class=HTMLResponse)
async def staff_mfa_regenerate(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    from app.services import mfa

    if not user.mfa_enabled or not user.mfa_secret:
        return RedirectResponse(url="/admin/my-account/mfa", status_code=303)
    if not mfa.verify_totp(user.mfa_secret, code):
        return templates.TemplateResponse(
            "staff/admin/mfa_setup.html",
            {
                "request": request,
                "user": user,
                "qr_data_uri": None,
                "otpauth_uri": None,
                "secret": None,
                "error": "Code TOTP incorrect — codes non régénérés.",
            },
            status_code=400,
        )
    new_codes = await mfa.generate_recovery_codes(
        db,
        owner_type="staff",
        owner_id=user.id,
    )
    await activity_record(
        db,
        action="staff_mfa_codes_regen",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=user.id,
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/admin/mfa_recovery_codes.html",
        {"request": request, "user": user, "codes": new_codes, "is_regeneration": True},
    )


@router.post("/my-account/mfa/disable", response_class=HTMLResponse)
async def staff_mfa_disable(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_staff),
):
    from sqlalchemy import delete

    from app.models.mfa_recovery_code import MfaRecoveryCode
    from app.services import mfa

    if not user.mfa_enabled or not user.mfa_secret:
        return RedirectResponse(url="/admin/my-account/mfa", status_code=303)
    if not mfa.verify_totp(user.mfa_secret, code):
        return templates.TemplateResponse(
            "staff/admin/mfa_setup.html",
            {
                "request": request,
                "user": user,
                "qr_data_uri": None,
                "otpauth_uri": None,
                "secret": None,
                "error": "Code TOTP incorrect — MFA non désactivée.",
            },
            status_code=400,
        )
    user.mfa_enabled = False
    user.mfa_secret = None
    await db.flush()
    await db.execute(
        delete(MfaRecoveryCode)
        .where(MfaRecoveryCode.owner_type == "staff")
        .where(MfaRecoveryCode.owner_id == user.id)
    )
    await activity_record(
        db,
        action="staff_mfa_disabled",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="admin",
        entity_type="user",
        entity_id=user.id,
        ip_address=_client_ip(request),
    )
    if user.email:
        from app.services import security_alerts

        await security_alerts.notify_mfa_disabled(
            to_email=user.email,
            recipient_name=user.full_name or user.username,
            ip=_client_ip(request),
            ua=request.headers.get("user-agent"),
        )
    return RedirectResponse(url="/admin/my-account?mfa=disabled", status_code=303)


# ────────────────────────────────────────────── CO2 variables (ENV-02)
# Variables versionnées consommées par services/co2.py — les fallbacks
# codés restent la référence tant que la table est vide.
#
# ADM-06 — on réexpose aussi les facteurs NOx / SOx (FIN-03, kg/t.nm) dans le
# même éditeur versionné : ils sont lus par ``services.emissions`` mais
# n'étaient pas éditables. Source de vérité unique : ``emissions`` (noms +
# constantes de repli) pour éviter toute dérive nom/valeur.
CO2_VARIABLE_DEFS: dict[str, dict] = {
    co2_service.TOWT_EF_VARIABLE: {
        "label": "Facteur d'émission TOWT (voile)",
        "unit": "gCO2/t.km",
        "fallback": co2_service.TOWT_CO2_EF_G_PER_TKM,
    },
    co2_service.CONV_EF_VARIABLE: {
        "label": "Facteur d'émission conventionnel (cargo fuel)",
        "unit": "gCO2/t.km",
        "fallback": co2_service.CONV_CO2_EF_G_PER_TKM,
    },
    emissions_service.NOX_CONV_VAR: {
        "label": "Facteur NOx — navire conventionnel",
        "unit": "kg/t.nm",
        "fallback": emissions_service.CONV_NOX_PER_TNM,
    },
    emissions_service.NOX_SAIL_VAR: {
        "label": "Facteur NOx — voilier-cargo",
        "unit": "kg/t.nm",
        "fallback": emissions_service.SAIL_NOX_PER_TNM,
    },
    emissions_service.SOX_CONV_VAR: {
        "label": "Facteur SOx — navire conventionnel",
        "unit": "kg/t.nm",
        "fallback": emissions_service.CONV_SOX_PER_TNM,
    },
    emissions_service.SOX_SAIL_VAR: {
        "label": "Facteur SOx — voilier-cargo",
        "unit": "kg/t.nm",
        "fallback": emissions_service.SAIL_SOX_PER_TNM,
    },
}


@router.get("/co2", response_class=HTMLResponse)
async def co2_variables_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    rows = list(
        (
            await db.execute(
                select(Co2Variable).order_by(
                    Co2Variable.name,
                    Co2Variable.effective_date.desc(),
                    Co2Variable.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    current_by_name = {r.name: r for r in rows if r.is_current}
    return templates.TemplateResponse(
        "staff/admin/co2_variables.html",
        {
            "request": request,
            "user": user,
            "variable_defs": CO2_VARIABLE_DEFS,
            "current_by_name": current_by_name,
            "history": rows,
            "today": date.today().isoformat(),
        },
    )


@router.post("/co2/init")
async def co2_variables_init(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Initialise les variables versionnées depuis les constantes codées."""
    existing = set(
        (
            await db.execute(
                select(Co2Variable.name).where(Co2Variable.name.in_(tuple(CO2_VARIABLE_DEFS)))
            )
        )
        .scalars()
        .all()
    )
    created: list[str] = []
    for name, meta in CO2_VARIABLE_DEFS.items():
        if name in existing:
            continue
        db.add(
            Co2Variable(
                name=name,
                value=meta["fallback"],
                unit=meta["unit"],
                source="Valeurs codées (init admin)",
                effective_date=date.today(),
                is_current=True,
                created_by=user.username,
            )
        )
        created.append(f"{name}={meta['fallback']}")
    if created:
        await db.flush()
        co2_service.invalidate_factors_cache()
        await activity_record(
            db,
            action="co2_variable_init",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="admin",
            entity_type="co2_variable",
            entity_label="init",
            detail="; ".join(created),
            ip_address=_client_ip(request),
        )
    return RedirectResponse(url="/admin/co2", status_code=303)


@router.post("/co2/update")
async def co2_variables_update(
    request: Request,
    name: str = Form(...),
    value: str = Form(...),
    source: str = Form(""),
    effective_date: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Mise à jour versionnée : INSERT nouvelle ligne + bascule is_current.

    L'historique n'est jamais supprimé ni modifié (hors flag is_current).
    """
    if name not in CO2_VARIABLE_DEFS:
        raise HTTPException(status_code=400, detail="variable CO2 inconnue")
    try:
        new_value = Decimal(value.strip().replace(",", "."))
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="valeur numérique invalide") from None
    if not new_value.is_finite() or new_value <= 0:
        raise HTTPException(status_code=400, detail="la valeur doit être strictement positive")
    if new_value >= Decimal("1000000"):
        raise HTTPException(status_code=400, detail="valeur hors plage (max 999999.999999)")
    try:
        eff_date = date.fromisoformat(effective_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date d'effet invalide") from None
    source_clean = source.strip()[:200] or None

    previous_rows = (
        (
            await db.execute(
                select(Co2Variable).where(
                    Co2Variable.name == name, Co2Variable.is_current.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    old_value = previous_rows[0].value if previous_rows else CO2_VARIABLE_DEFS[name]["fallback"]
    for prev in previous_rows:
        prev.is_current = False
    db.add(
        Co2Variable(
            name=name,
            value=new_value,
            unit=CO2_VARIABLE_DEFS[name]["unit"],
            source=source_clean,
            effective_date=eff_date,
            is_current=True,
            created_by=user.username,
        )
    )
    await db.flush()
    co2_service.invalidate_factors_cache()
    await activity_record(
        db,
        action="co2_variable_update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="co2_variable",
        entity_label=name,
        detail=f"{name}: {old_value} → {new_value} ({source_clean or 'sans source'})",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/co2", status_code=303)


# ────────────────────────────────────────────── Flotte — référentiel environnemental (MRV lot 1)
# Cuves / moteurs par navire (app.models.vessel_env) + les 3 champs
# référentiel portés par Vessel. Patron : la page /admin/co2 ci-dessus.
def _parse_decimal_or_none(value: str | None) -> Decimal | None:
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="valeur numérique invalide") from None
    if not parsed.is_finite():
        raise HTTPException(status_code=400, detail="valeur numérique invalide")
    return parsed


@router.get("/flotte-env", response_class=HTMLResponse)
async def flotte_env_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    vessels = await _vessels_for_form(db)
    tanks_by_vessel = {}
    engines_by_vessel = {}
    for v in vessels:
        tanks_by_vessel[v.id] = await referential_env.get_vessel_tanks(db, v.id)
        engines_by_vessel[v.id] = await referential_env.get_vessel_engines(db, v.id)
    return templates.TemplateResponse(
        "staff/admin/flotte_env.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "tanks_by_vessel": tanks_by_vessel,
            "engines_by_vessel": engines_by_vessel,
        },
    )


@router.post("/flotte-env/{vessel_id}/init")
async def flotte_env_init(
    vessel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Initialise les cuves/moteurs par défaut d'un navire — idempotent (lot 1).

    N'écrit que ce qui manque encore (aucun doublon, aucune ligne existante
    modifiée) : rejouable sans risque, y compris sur un navire déjà complet.
    """
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404)
    result = await referential_env.ensure_vessel_env_defaults(db, vessel)
    if result.changed:
        await activity_record(
            db,
            action="vessel_env_init",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="admin",
            entity_type="vessel",
            entity_id=vessel.id,
            entity_label=vessel.code,
            detail=(
                f"cuves créées: {', '.join(result.tanks_created) or 'aucune'} ; "
                f"moteurs créés: {', '.join(result.engines_created) or 'aucun'}"
            ),
            ip_address=_client_ip(request),
        )
    return RedirectResponse(url="/admin/flotte-env", status_code=303)


@router.post("/flotte-env/{vessel_id}/update")
async def flotte_env_update(
    vessel_id: int,
    request: Request,
    lightweight_t: str | None = Form(None),
    deadweight_t: str | None = Form(None),
    default_fuel_type: str = Form("MDO"),
    water_density_default_t_m3: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Édite les champs référentiel environnemental portés par ``Vessel`` (lot 1
    + ``deadweight_t``, G17 — symétrique de ``lightweight_t``, purement
    informatif comme lui)."""
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404)
    fuel_clean = (default_fuel_type or "MDO").strip().upper()[:20] or "MDO"
    vessel.lightweight_t = _parse_decimal_or_none(lightweight_t)
    vessel.deadweight_t = _parse_decimal_or_none(deadweight_t)
    vessel.default_fuel_type = fuel_clean
    vessel.water_density_default_t_m3 = _parse_decimal_or_none(water_density_default_t_m3)
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="vessel_env",
        entity_id=vessel.id,
        entity_label=vessel.code,
        detail=(
            f"lightweight_t={vessel.lightweight_t}; deadweight_t={vessel.deadweight_t}; "
            f"fuel={fuel_clean}; water_density_t_m3={vessel.water_density_default_t_m3}"
        ),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/flotte-env", status_code=303)


# ────────────────────────────────────────────── Facteurs d'émission multi-GES (MRV lot 1)
# Référentiel emission_factors (app.models.emission_factor) — 1 ligne par
# carburant, append-only versionné. Patron : la page /admin/co2 ci-dessus,
# adapté au multi-GES (CO₂/CH₄/N₂O + WtT) et à la fenêtre de validité datée.
@router.get("/emission-factors", response_class=HTMLResponse)
async def emission_factors_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    rows = list(
        (
            await db.execute(
                select(EmissionFactor).order_by(
                    EmissionFactor.fuel_type,
                    EmissionFactor.valid_from.desc(),
                    EmissionFactor.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    current_by_fuel = {r.fuel_type: r for r in rows if r.is_current}
    author_ids = {r.created_by_id for r in rows if r.created_by_id is not None}
    author_names: dict[int, str] = {}
    if author_ids:
        authors = (await db.execute(select(User).where(User.id.in_(author_ids)))).scalars().all()
        author_names = {a.id: (a.full_name or a.username) for a in authors}
    return templates.TemplateResponse(
        "staff/admin/emission_factors.html",
        {
            "request": request,
            "user": user,
            "history": rows,
            "current_by_fuel": current_by_fuel,
            "author_names": author_names,
            "today": date.today().isoformat(),
        },
    )


@router.post("/emission-factors/create")
async def emission_factors_create(
    request: Request,
    fuel_type: str = Form("MDO"),
    ef_co2_kg_per_kg: str = Form(...),
    ef_ch4_kg_per_kg: str = Form(...),
    ef_n2o_kg_per_kg: str = Form(...),
    wtt_gco2eq_per_mj: str = Form(...),
    source_reference: str = Form(""),
    valid_from: str = Form(...),
    valid_to: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Nouvelle version d'un facteur d'émission — append-only (lot 1).

    L'ancienne ligne ``is_current`` du même carburant bascule à ``False`` ;
    l'historique n'est jamais supprimé ni modifié (même pattern que
    ``/admin/co2/update`` pour ``co2_variables``).
    """
    fuel_clean = (fuel_type or "MDO").strip().upper()[:20] or "MDO"

    def _req_decimal(raw: str, field: str) -> Decimal:
        try:
            value = Decimal(raw.strip().replace(",", "."))
        except InvalidOperation:
            raise HTTPException(
                status_code=400, detail=f"{field} : valeur numérique invalide"
            ) from None
        if not value.is_finite():
            raise HTTPException(status_code=400, detail=f"{field} : valeur numérique invalide")
        return value

    ef_co2 = _req_decimal(ef_co2_kg_per_kg, "ef_co2_kg_per_kg")
    ef_ch4 = _req_decimal(ef_ch4_kg_per_kg, "ef_ch4_kg_per_kg")
    ef_n2o = _req_decimal(ef_n2o_kg_per_kg, "ef_n2o_kg_per_kg")
    wtt = _req_decimal(wtt_gco2eq_per_mj, "wtt_gco2eq_per_mj")
    for value, field in (
        (ef_co2, "ef_co2_kg_per_kg"),
        (ef_ch4, "ef_ch4_kg_per_kg"),
        (ef_n2o, "ef_n2o_kg_per_kg"),
        (wtt, "wtt_gco2eq_per_mj"),
    ):
        if value < 0:
            raise HTTPException(status_code=400, detail=f"{field} doit être positif ou nul")

    try:
        valid_from_date = date.fromisoformat(valid_from)
    except ValueError:
        raise HTTPException(status_code=400, detail="date de début de validité invalide") from None
    valid_to_clean = valid_to.strip()
    valid_to_date = None
    if valid_to_clean:
        try:
            valid_to_date = date.fromisoformat(valid_to_clean)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="date de fin de validité invalide"
            ) from None
        if valid_to_date < valid_from_date:
            raise HTTPException(
                status_code=400, detail="la date de fin doit suivre la date de début"
            )

    source_clean = source_reference.strip()[:200] or None

    previous_current = (
        (
            await db.execute(
                select(EmissionFactor).where(
                    EmissionFactor.fuel_type == fuel_clean,
                    EmissionFactor.is_current.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for prev in previous_current:
        prev.is_current = False

    db.add(
        EmissionFactor(
            fuel_type=fuel_clean,
            ef_co2_kg_per_kg=ef_co2,
            ef_ch4_kg_per_kg=ef_ch4,
            ef_n2o_kg_per_kg=ef_n2o,
            wtt_gco2eq_per_mj=wtt,
            source_reference=source_clean,
            valid_from=valid_from_date,
            valid_to=valid_to_date,
            is_current=True,
            created_by_id=user.id,
        )
    )
    await db.flush()
    referential_env.invalidate_emission_factor_cache()
    await activity_record(
        db,
        action="emission_factor_create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="emission_factor",
        entity_label=fuel_clean,
        detail=(
            f"{fuel_clean}: co2={ef_co2} ch4={ef_ch4} n2o={ef_n2o} wtt={wtt} "
            f"({source_clean or 'sans source'})"
        ),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/emission-factors", status_code=303)


# ────────────────────────────────────────────── Permissions matrix (ARC-04)
@router.get("/permissions", response_class=HTMLResponse)
async def permissions_matrix_page(
    request: Request,
    saved: int = 0,
    skipped: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    effective = await get_effective_matrix(db)
    defaults = get_default_matrix()
    grid: dict[str, dict[str, dict]] = {}
    override_count = 0
    for role in ROLES:
        row: dict[str, dict] = {}
        for module in MODULES:
            level = effective.get((role, module), "")
            default = defaults.get((role, module), "")
            overridden = level != default
            if overridden:
                override_count += 1
            row[module] = {"level": level, "default": default, "overridden": overridden}
        grid[role] = row
    return templates.TemplateResponse(
        "staff/admin/permissions_matrix.html",
        {
            "request": request,
            "user": user,
            "grid": grid,
            "roles": ROLES,
            "modules": MODULES,
            "levels": VALID_LEVELS,
            "override_count": override_count,
            "saved": saved,
            "skipped": skipped,
        },
    )


@router.post("/permissions")
async def permissions_matrix_update(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "M")),
):
    """Upsert des seules cellules qui diffèrent du défaut codé en dur.

    - cellule revenue au défaut → suppression de l'override DB ;
    - (administrateur, admin) jamais dégradable → ignorée + avertissement ;
    - cache permissions invalidé (prise d'effet ≤ 60 s).
    """
    form = await request.form()
    defaults = get_default_matrix()
    existing = {
        (r.role, r.module): r for r in (await db.execute(select(RolePermission))).scalars().all()
    }
    changes: list[str] = []
    skipped_admin = False
    for role in ROLES:
        for module in MODULES:
            raw = form.get(f"perm-{role}-{module}")
            if raw is None:
                continue
            submitted = str(raw).strip()
            if submitted not in VALID_LEVELS:
                continue  # valeur inattendue → on ignore la cellule
            default = defaults.get((role, module), "")
            if role == "administrateur" and module == "admin" and submitted != default:
                skipped_admin = True
                continue
            row = existing.get((role, module))
            current = row.level if row is not None else default
            if submitted == current:
                continue
            label_from = current or "∅"
            label_to = submitted or "∅"
            if submitted == default:
                # Retour au défaut → l'override n'a plus lieu d'exister.
                if row is not None:
                    await db.delete(row)
                    changes.append(f"{role}/{module}: {label_from} → défaut ({label_to})")
            elif row is None:
                db.add(
                    RolePermission(
                        role=role, module=module, level=submitted, updated_by=user.username
                    )
                )
                changes.append(f"{role}/{module}: {label_from} → {label_to}")
            else:
                row.level = submitted
                row.updated_by = user.username
                changes.append(f"{role}/{module}: {label_from} → {label_to}")
    if changes:
        await db.flush()
        invalidate_permissions_cache()
        await activity_record(
            db,
            action="permissions_update",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="admin",
            entity_type="role_permission",
            entity_label=f"{len(changes)} cellule(s)",
            detail="; ".join(changes)[:2000],
            ip_address=_client_ip(request),
        )
    url = f"/admin/permissions?saved={1 if changes else 0}"
    if skipped_admin:
        url += "&skipped=1"
    return RedirectResponse(url=url, status_code=303)


# ────────────────────────────────────────────── Data export / purge (ADM-04)


@router.get("/data", response_class=HTMLResponse)
async def admin_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> HTMLResponse:
    from app.services.admin_data import ALLOWED_EXPORT_TABLES, ALLOWED_PURGE_TABLES

    return templates.TemplateResponse(
        "staff/admin/data.html",
        {
            "request": request,
            "user": user,
            "export_tables": ALLOWED_EXPORT_TABLES,
            "purge_tables": ALLOWED_PURGE_TABLES,
        },
    )


@router.get("/export/global.zip")
async def admin_export_global(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> Response:
    """ADM-04 — export ZIP global (un CSV par table whitelistée)."""
    from app.services.admin_data import export_global_zip

    data = await export_global_zip(db)
    await activity_record(
        db,
        action="export_global",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="database",
        entity_label="global.zip",
        ip_address=_client_ip(request),
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="newtowt_export.zip"'},
    )


@router.get("/export/table/{table_name}.csv")
async def admin_export_table(
    table_name: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "C")),
) -> Response:
    """ADM-04 — export CSV sélectif d'une table whitelistée."""
    from app.services.admin_data import export_table_csv

    try:
        csv_text = await export_table_csv(db, table_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table_name}.csv"'},
    )


@router.post("/database/purge")
async def admin_purge_table(
    request: Request,
    table_name: str = Form(...),
    confirm: str = Form(...),
    older_than_days: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin", "S")),
):
    """ADM-04 — purge d'une table whitelistée. Double confirmation : l'opérateur
    retape le nom exact de la table dans ``confirm``.

    Si ``older_than_days`` est fourni (> 0), la purge est **ciblée par
    rétention** : seules les lignes plus anciennes que ``maintenant −
    older_than_days`` sont supprimées (colonne d'horodatage whitelistée).
    Sinon, la table est intégralement vidée.
    """
    from datetime import UTC, datetime, timedelta

    from app.services.admin_data import purge_table, purge_table_before

    if confirm.strip() != table_name:
        raise HTTPException(
            status_code=400,
            detail="Confirmation invalide : retapez le nom exact de la table.",
        )
    try:
        if older_than_days is not None and older_than_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
            deleted = await purge_table_before(db, table_name, cutoff)
            detail = f"{deleted} lignes supprimées (> {older_than_days} j)"
        else:
            deleted = await purge_table(db, table_name)
            detail = f"{deleted} lignes supprimées (table vidée)"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await activity_record(
        db,
        action="purge",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="admin",
        entity_type="database",
        entity_label=table_name,
        detail=detail,
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/admin/data?purged=1", status_code=303)


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
