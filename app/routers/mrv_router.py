"""MRV — reporting environnemental événementiel (v2).

Écrans siège : hub ``/mrv``, voyages, soutages (BDN), FLGO, qualité,
paramètres/règles de validation, datasets réglementaires OVDLA/OVDBR.

LOT 14 — décommissionnement du legacy : le CRUD manuel ``mrv_events``, les
exports CSV DNV (9 et 18 col.) et l'écran ``/params`` (MRVParameter) ont été
retirés. Les ``mrv_events`` historiques restent consultables en **archive
lecture seule** (``/mrv/archive/events``) ; la capture d'événements v2 (bord)
et les datasets OVDLA/OVDBR (siège) sont la voie unique.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.bunker import BUNKER_STATUSES, BunkerOperation
from app.models.flgo import FLGO_ACTION_TYPES, FLGO_SOURCES, FlgoReading
from app.models.leg import Leg

# LOT 14 — ``mrv_events`` en archive lecture seule (plus de CRUD/sync/recompute).
# ``MRVEvent`` reste importé pour l'écran d'archive ; ``MRVParameter``,
# ``recompute_leg`` et le module ``mrv_export`` (CSV DNV) ne sont plus consommés.
from app.models.mrv import MRVEvent
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import bunkering, flgo_sync
from app.services.activity import record as activity_record
from app.services.safe_files import content_length_exceeds_max
from app.templating import brand_for_lang, templates
from app.utils.file_validation import validate_filename, validate_size

router = APIRouter(prefix="/mrv", tags=["mrv"])


# ══════════════════════════════ LOT 14 — hub MRV ════════════════════════════
# L'ancien ``/mrv`` (table ``mrv_events`` + CRUD manuel + exports DNV 18 col. +
# écran ``/params``) est décommissionné : la capture d'événements v2 côté bord
# (``/onboard/events``) et les datasets OVDLA/OVDBR côté siège sont la voie
# UNIQUE. ``/mrv`` devient un hub de liens ; les ``mrv_events`` historiques
# restent consultables en archive lecture seule (``/mrv/archive/events``).
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def mrv_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Hub MRV — points d'entrée des écrans v2 (voyages, soutages, FLGO,
    qualité, datasets, paramètres) + compteur d'archive des événements legacy."""
    archived_events = (await db.execute(select(func.count()).select_from(MRVEvent))).scalar_one()
    return templates.TemplateResponse(
        "staff/mrv/index.html",
        {"request": request, "user": user, "archived_events": archived_events},
    )


_ARCHIVE_PAGE_SIZE = 50


@router.get("/archive/events", response_class=HTMLResponse)
async def mrv_archive_events(
    request: Request,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 14 — archive **lecture seule** des ``mrv_events`` legacy.

    Aucune écriture possible (CRUD retiré, sync éteinte) : la table est gelée,
    conservée pour l'audit et le fallback ledger. Paginée, bandeau explicite.
    """
    page = max(1, page)
    total = (await db.execute(select(func.count()).select_from(MRVEvent))).scalar_one()
    offset = (page - 1) * _ARCHIVE_PAGE_SIZE
    events = list(
        (
            await db.execute(
                select(MRVEvent)
                .order_by(MRVEvent.recorded_at.desc(), MRVEvent.id.desc())
                .offset(offset)
                .limit(_ARCHIVE_PAGE_SIZE)
            )
        )
        .scalars()
        .all()
    )
    leg_map: dict[int, Leg] = {}
    for lid in {e.leg_id for e in events}:
        leg = await db.get(Leg, lid)
        if leg is not None:
            leg_map[lid] = leg
    has_next = offset + len(events) < total
    return templates.TemplateResponse(
        "staff/mrv/archive_events.html",
        {
            "request": request,
            "user": user,
            "events": events,
            "leg_map": leg_map,
            "total": total,
            "page": page,
            "has_prev": page > 1,
            "has_next": has_next,
        },
    )


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )


# ══════════════════ LOT 2 — Paramètres & moteur de règles de validation ══════

_MAX_THRESHOLD = Decimal("1000000000")  # borne Numeric(15,6)


def _parse_threshold_value(raw: str) -> Decimal:
    """Coerce une saisie de seuil en Decimal validé (sinon HTTP 400)."""
    from decimal import InvalidOperation

    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail="valeur numérique invalide") from exc
    if not value.is_finite() or value < 0 or abs(value) >= _MAX_THRESHOLD:
        raise HTTPException(status_code=400, detail="valeur hors plage (0 ≤ x < 1e9)")
    return value


def _sorted_rules(rules: list) -> list:
    """R01-R26 d'abord, IR01-IR05 ensuite (tri lisible)."""
    return sorted(rules, key=lambda r: (r.rule_id.startswith("IR"), r.rule_id))


@router.get("/parametres", response_class=HTMLResponse)
async def mrv_parametres(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Écran d'administration des règles, seuils et paramètres dashboard (LOT 2)."""
    from app.models.validation import (
        DashboardParameter,
        ValidationRule,
        ValidationRuleThreshold,
    )

    rules = list((await db.execute(select(ValidationRule))).scalars().all())
    thresholds = list((await db.execute(select(ValidationRuleThreshold))).scalars().all())
    dashboard_params = list(
        (await db.execute(select(DashboardParameter).order_by(DashboardParameter.parameter_name)))
        .scalars()
        .all()
    )
    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)))
        .scalars()
        .all()
    )

    rule_by_id = {r.rule_id: r for r in rules}
    vessel_by_id = {v.id: v for v in vessels}
    thr_global = sorted(
        (t for t in thresholds if t.vessel_id is None),
        key=lambda t: (t.rule_id.startswith("IR"), t.rule_id, t.parameter_name),
    )
    thr_overrides = sorted(
        (t for t in thresholds if t.vessel_id is not None),
        key=lambda t: (t.rule_id, t.parameter_name, t.vessel_id or 0),
    )
    dash_global = [d for d in dashboard_params if d.vessel_id is None]
    dash_overrides = [d for d in dashboard_params if d.vessel_id is not None]

    return templates.TemplateResponse(
        "staff/mrv/parametres.html",
        {
            "request": request,
            "user": user,
            "rules": _sorted_rules(rules),
            "rule_by_id": rule_by_id,
            "thr_global": thr_global,
            "thr_overrides": thr_overrides,
            "dash_global": dash_global,
            "dash_overrides": dash_overrides,
            "vessels": vessels,
            "vessel_by_id": vessel_by_id,
            "seeded": bool(rules),
            "provisional_count": sum(1 for t in thr_global if t.provisional),
        },
    )


@router.post("/parametres/init")
async def mrv_parametres_init(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Initialise (idempotent) le référentiel de validation depuis le catalogue codé."""
    from app.services.validation_engine import seed_reference_data

    created = await seed_reference_data(db, updated_by=user.id)
    total = sum(len(v) for v in created.values())
    if total:
        await activity_record(
            db,
            action="mrv_validation_seed",
            user_id=user.id,
            user_name=user.full_name or user.username,
            user_role=user.role,
            module="mrv",
            entity_type="validation_rule",
            entity_label="init référentiel validation",
            detail=(
                f"rules={len(created['rules'])} thresholds={len(created['thresholds'])} "
                f"dashboard={len(created['dashboard'])}"
            ),
            ip_address=_client_ip(request),
        )
    return RedirectResponse(url="/mrv/parametres", status_code=303)


@router.post("/parametres/rules/{rule_id}/toggle")
async def mrv_parametres_rule_toggle(
    rule_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Active/désactive une règle du moteur de validation."""
    from app.models.validation import ValidationRule

    rule = await db.get(ValidationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="règle inconnue")
    rule.active = not rule.active
    await db.flush()
    await activity_record(
        db,
        action="mrv_validation_rule_toggle",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="validation_rule",
        entity_label=rule_id,
        detail=f"active={rule.active}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/parametres", status_code=303)


@router.post("/parametres/thresholds/{threshold_id}/update")
async def mrv_parametres_threshold_update(
    threshold_id: int,
    request: Request,
    value: str = Form(...),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Édite la valeur (et la note) d'un seuil — global ou override navire."""
    from app.models.validation import ValidationRuleThreshold
    from app.services.validation_engine import invalidate_cache

    thr = await db.get(ValidationRuleThreshold, threshold_id)
    if thr is None:
        raise HTTPException(status_code=404, detail="seuil inconnu")
    thr.value = _parse_threshold_value(value)
    note_clean = note.strip()
    if note_clean:
        thr.note = note_clean[:500]
    thr.updated_by = user.id
    await db.flush()
    invalidate_cache()
    await activity_record(
        db,
        action="mrv_validation_threshold_update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="validation_rule_threshold",
        entity_id=thr.id,
        entity_label=f"{thr.rule_id}:{thr.parameter_name}",
        detail=f"value={thr.value} vessel={thr.vessel_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/parametres", status_code=303)


@router.post("/parametres/thresholds/override")
async def mrv_parametres_threshold_override(
    request: Request,
    rule_id: str = Form(...),
    parameter_name: str = Form(...),
    vessel_id: int = Form(...),
    value: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Crée (ou met à jour) un override de seuil pour un navire donné."""
    from app.models.validation import ValidationRuleThreshold
    from app.services.validation_engine import invalidate_cache

    # Le seuil global sert de gabarit (unité, provisoire) pour l'override.
    base = (
        await db.execute(
            select(ValidationRuleThreshold).where(
                ValidationRuleThreshold.rule_id == rule_id,
                ValidationRuleThreshold.parameter_name == parameter_name,
                ValidationRuleThreshold.vessel_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if base is None:
        raise HTTPException(status_code=404, detail="seuil global introuvable")
    if await db.get(Vessel, vessel_id) is None:
        raise HTTPException(status_code=404, detail="navire inconnu")
    parsed = _parse_threshold_value(value)

    existing = (
        await db.execute(
            select(ValidationRuleThreshold).where(
                ValidationRuleThreshold.rule_id == rule_id,
                ValidationRuleThreshold.parameter_name == parameter_name,
                ValidationRuleThreshold.vessel_id == vessel_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = parsed
        existing.updated_by = user.id
        target = existing
    else:
        target = ValidationRuleThreshold(
            rule_id=rule_id,
            vessel_id=vessel_id,
            parameter_name=parameter_name,
            value=parsed,
            unit=base.unit,
            provisional=base.provisional,
            note=f"Override navire de {base.rule_id}:{base.parameter_name}",
            updated_by=user.id,
        )
        db.add(target)
    await db.flush()
    invalidate_cache()
    await activity_record(
        db,
        action="mrv_validation_threshold_override",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="validation_rule_threshold",
        entity_id=target.id,
        entity_label=f"{rule_id}:{parameter_name}@vessel{vessel_id}",
        detail=f"value={parsed}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/parametres", status_code=303)


@router.post("/parametres/dashboard/{param_id}/update")
async def mrv_parametres_dashboard_update(
    param_id: int,
    request: Request,
    value: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """Édite la valeur d'un paramètre du dashboard Performance Environnementale."""
    from app.models.validation import DashboardParameter

    param = await db.get(DashboardParameter, param_id)
    if param is None:
        raise HTTPException(status_code=404, detail="paramètre inconnu")
    param.value = _parse_threshold_value(value)
    param.updated_by = user.id
    await db.flush()
    await activity_record(
        db,
        action="mrv_dashboard_parameter_update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="dashboard_parameter",
        entity_id=param.id,
        entity_label=param.parameter_name,
        detail=f"value={param.value} vessel={param.vessel_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/parametres", status_code=303)


# ══════════════════════════ LOT 6 — Soutage (Bunker Report / BDN), vue siège ══


def _int_or_400(raw: str | None, field: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} invalide") from exc


async def _mrv_bunker_allocations(db: AsyncSession, bunker_id: int):
    from app.models.bunker import BunkerTankAllocation

    return list(
        (
            await db.execute(
                select(BunkerTankAllocation)
                .where(BunkerTankAllocation.bunker_id == bunker_id)
                .order_by(BunkerTankAllocation.id)
            )
        )
        .scalars()
        .all()
    )


@router.get("/bunkering", response_class=HTMLResponse)
async def mrv_bunkering_index(
    request: Request,
    vessel_id: int | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ecart: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 6 — liste des soutages (BDN), filtrable navire/période/statut/écarts.

    L'écart (masse déclarée vs Σ volume×densité, R23) n'est pas une colonne
    persistée — il est recalculé à l'affichage (``bunkering.evaluate_bunker``),
    conforme au principe « aucune nouvelle règle codée dans ce lot ».
    """
    stmt = select(BunkerOperation).order_by(BunkerOperation.delivery_datetime_utc.desc())
    if vessel_id:
        stmt = stmt.where(BunkerOperation.vessel_id == vessel_id)
    if status:
        stmt = stmt.where(BunkerOperation.status == status)
    if date_from:
        with contextlib.suppress(ValueError):
            stmt = stmt.where(
                BunkerOperation.delivery_datetime_utc
                >= datetime.fromisoformat(date_from).replace(tzinfo=UTC)
            )
    if date_to:
        with contextlib.suppress(ValueError):
            stmt = stmt.where(
                BunkerOperation.delivery_datetime_utc
                <= datetime.fromisoformat(date_to).replace(tzinfo=UTC)
            )
    bunkers = list((await db.execute(stmt)).scalars().all())

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    vessel_map = {v.id: v for v in vessels}
    leg_map: dict[int, Leg] = {}
    for b in bunkers:
        if b.leg_id and b.leg_id not in leg_map:
            leg = await db.get(Leg, b.leg_id)
            if leg:
                leg_map[b.leg_id] = leg

    rows = []
    for b in bunkers:
        allocations = await _mrv_bunker_allocations(db, b.id)
        tanks_by_id = await bunkering.vessel_tanks_by_id(db, b.vessel_id)
        checks = await bunkering.evaluate_bunker(db, b, allocations, tanks_by_id)
        if ecart and checks.mass.status != ecart:
            continue
        rows.append(
            {
                "bunker": b,
                "vessel": vessel_map.get(b.vessel_id),
                "leg": leg_map.get(b.leg_id),
                "checks": checks,
            }
        )

    return templates.TemplateResponse(
        "staff/mrv/bunkering_index.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "vessels": vessels,
            "filter_vessel_id": vessel_id,
            "filter_status": status,
            "filter_date_from": date_from or "",
            "filter_date_to": date_to or "",
            "filter_ecart": ecart or "",
            "bunker_statuses": BUNKER_STATUSES,
        },
    )


@router.get("/bunkering/{bunker_id}", response_class=HTMLResponse)
async def mrv_bunkering_detail(
    bunker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 6 — détail siège d'un soutage + formulaire de correction (mrv:M)."""
    bunker = await db.get(BunkerOperation, bunker_id)
    if bunker is None:
        raise HTTPException(status_code=404)
    vessel = await db.get(Vessel, bunker.vessel_id)
    leg = await db.get(Leg, bunker.leg_id) if bunker.leg_id else None
    allocations = await _mrv_bunker_allocations(db, bunker.id)
    tanks_by_id = await bunkering.vessel_tanks_by_id(db, bunker.vessel_id)
    checks = await bunkering.evaluate_bunker(db, bunker, allocations, tanks_by_id)
    from app.models.user import User
    from app.services.leg_filter import leg_select_options

    leg_options = await leg_select_options(db, vessel_id=bunker.vessel_id)
    validated_by_name = None
    if bunker.validated_master_by:
        validator = await db.get(User, bunker.validated_master_by)
        validated_by_name = (validator.full_name or validator.username) if validator else None
    # N'affiche le formulaire de correction qu'aux rôles qui pourront
    # effectivement le soumettre (POST gardé par mrv:M) — évite d'exposer une
    # action qui échouerait en 403 (matrice EFFECTIVE, overrides ARC-04 inclus).
    from app.permissions import has_permission_effective

    can_correct = await has_permission_effective(db, user.role, "mrv", "M")
    return templates.TemplateResponse(
        "staff/mrv/bunkering_detail.html",
        {
            "request": request,
            "user": user,
            "bunker": bunker,
            "vessel": vessel,
            "leg": leg,
            "allocations": allocations,
            "tanks_by_id": tanks_by_id,
            "checks": checks,
            "can_correct": can_correct,
            "leg_options": leg_options,
            "validated_by_name": validated_by_name,
            "audience": "mrv",
        },
    )


@router.post("/bunkering/{bunker_id}/edit")
async def mrv_bunkering_edit(
    bunker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 6 — correction siège : possible même après validation Master,
    toujours tracée (``services.activity``) — jamais silencieuse."""
    bunker = await db.get(BunkerOperation, bunker_id)
    if bunker is None:
        raise HTTPException(status_code=404)
    form = dict(await request.form())
    was_validated = bunker.status == "valide_master"
    clear_leg = (form.pop("clear_leg", "") or "").strip() == "1"
    manual_leg_raw = (form.pop("leg_id", "") or "").strip()
    manual_leg_id = _int_or_400(manual_leg_raw, "leg_id") if manual_leg_raw else None
    if manual_leg_id is not None:
        leg = await db.get(Leg, manual_leg_id)
        if leg is None or leg.vessel_id != bunker.vessel_id:
            raise HTTPException(status_code=400, detail="Leg invalide pour ce navire.")

    try:
        await bunkering.apply_review_correction(
            db, bunker, form=form, manual_leg_id=manual_leg_id, clear_leg=clear_leg
        )
    except bunkering.BunkerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # LOT 8 — une correction siège d'un soutage DÉJÀ validé Master rejoue les
    # règles scope ``bunker`` (R16/R23/R24) sur la donnée corrigée.
    if was_validated:
        await _vrc.run_bunker_rules_and_route(db, bunker)

    await activity_record(
        db,
        action="bunker_review_correction",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="bunker_operation",
        entity_id=bunker.id,
        entity_label=bunker.bdn_number,
        detail=(
            "Correction post-validation Master" if was_validated else "Correction brouillon (siège)"
        ),
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv/bunkering/{bunker.id}", status_code=303)


# === LOT 5 — voyages & rapports générés ===
#
# Vues siège de la chaîne événementielle → rapports générés (Noon/Carbon/
# Stopover) + workflow de validation deux niveaux (Master bord → siège Carbon).
# Section purement additive (aucun réagencement de l'existant). Le rendu PDF est
# fait depuis le snapshot ``payload`` (reproductibilité d'audit), jamais recalculé.

from app.models.env_report import EnvFieldModification, EnvReport  # noqa: E402
from app.models.nav_event import NavEvent  # noqa: E402
from app.permissions import has_permission_effective  # noqa: E402
from app.services import inter_event_compute as _iec  # noqa: E402
from app.services import report_generation as _rg  # noqa: E402

_LOT5_REPORT_TYPES: tuple[str, ...] = ("noon", "carbon", "stopover")
_LOT5_PDF_TEMPLATES: dict[str, str] = {
    "noon": "pdf/noon_report_generated.html",
    "carbon": "pdf/carbon_report_v2.html",
    "stopover": "pdf/stopover_report.html",
}


async def _lot5_event_counts(db: AsyncSession, leg_id: int) -> dict[str, int]:
    """Compte des événements d'un voyage par statut (brouillon/finalisé/validé)."""
    rows = (
        (await db.execute(select(NavEvent.status).where(NavEvent.leg_id == leg_id))).scalars().all()
    )
    return {
        "brouillon": sum(1 for s in rows if s == "brouillon"),
        "finalise": sum(1 for s in rows if s == "finalise"),
        "valide": sum(1 for s in rows if s == "valide"),
    }


@router.get("/voyages", response_class=HTMLResponse)
async def mrv_voyages(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 5 — liste des voyages avec compteurs d'événements et de rapports."""
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(40))).scalars().all())
    vessel_ids = {leg.vessel_id for leg in legs if leg.vessel_id is not None}
    vessels: dict[int, Vessel] = {}
    if vessel_ids:
        vessels = {
            v.id: v
            for v in (await db.execute(select(Vessel).where(Vessel.id.in_(vessel_ids))))
            .scalars()
            .all()
        }
    rows = []
    for leg in legs:
        report_statuses = (
            (await db.execute(select(EnvReport.status).where(EnvReport.leg_id == leg.id)))
            .scalars()
            .all()
        )
        by_status: dict[str, int] = {}
        for s in report_statuses:
            by_status[s] = by_status.get(s, 0) + 1
        rows.append(
            {
                "leg": leg,
                "vessel": vessels.get(leg.vessel_id),
                "events": await _lot5_event_counts(db, leg.id),
                "reports_total": len(report_statuses),
                "reports_by_status": by_status,
            }
        )
    return templates.TemplateResponse(
        "staff/mrv/voyages.html", {"request": request, "user": user, "rows": rows}
    )


@router.get("/voyages/{leg_id}", response_class=HTMLResponse)
async def mrv_voyage_detail(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 5 — chaîne d'événements (calculs inter-événements) + rapports + génération."""
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Voyage introuvable")
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None

    lookup = await _rg._build_bunker_lookup(db, leg.id)
    comp = await _iec.compute_leg(db, leg, bunkered_t_lookup=lookup)
    windows = _rg._anchoring_windows(comp.events)
    event_rows = []
    for i, ev in enumerate(comp.events):
        interval = comp.intervals[i - 1] if i > 0 else None
        rob = comp.rob_chain[i] if i < len(comp.rob_chain) else None
        event_rows.append(
            {
                "event": ev,
                "distance_nm": interval.distance_nm if interval else None,
                "conso_total_t": interval.total_conso_t if interval else None,
                "rob_calculated_t": rob.rob_calculated_t if rob else None,
                "in_anchoring": bool(interval and _rg._interval_in_anchoring(interval, windows)),
            }
        )
    noon_events = [e for e in comp.events if e.event_type == "noon"]

    # Rattachement Stopover : Arrivée de CE voyage + Départ du voyage suivant.
    arrival = next((e for e in comp.events if e.event_type == "arrival"), None)
    next_leg = None
    departure_event = None
    if leg.vessel_id is not None and leg.etd is not None:
        next_leg = (
            (
                await db.execute(
                    select(Leg)
                    .where(Leg.vessel_id == leg.vessel_id, Leg.etd > leg.etd)
                    .order_by(Leg.etd.asc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if next_leg is not None:
            next_events = await _iec.finalized_events_for_leg(db, next_leg.id)
            departure_event = next((e for e in next_events if e.event_type == "departure"), None)

    reports = list(
        (
            await db.execute(
                select(EnvReport)
                .where(EnvReport.leg_id == leg_id)
                .order_by(EnvReport.report_type, EnvReport.id)
            )
        )
        .scalars()
        .all()
    )
    can_generate = await has_permission_effective(db, user.role, "mrv", "M")
    can_master = await has_permission_effective(db, user.role, "captain", "M")

    return templates.TemplateResponse(
        "staff/mrv/voyage_detail.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "event_rows": event_rows,
            "noon_events": noon_events,
            "reports": reports,
            "next_leg": next_leg,
            "arrival_event_id": arrival.id if arrival else None,
            "departure_event_id": departure_event.id if departure_event else None,
            "can_generate": can_generate,
            "can_master": can_master,
        },
    )


@router.post("/voyages/{leg_id}/reports/{report_type}/generate")
async def mrv_generate_report(
    leg_id: int,
    report_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 5 — génération (ou regénération) d'un rapport depuis les événements."""
    if report_type not in _LOT5_REPORT_TYPES:
        raise HTTPException(status_code=400, detail="type de rapport inconnu")
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Voyage introuvable")
    form = dict(await request.form())

    try:
        if report_type == "carbon":
            report = await _rg.generate_carbon_report(db, leg, author_user_id=user.id)
        elif report_type == "noon":
            event_id = _int_or_400(form.get("event_id"), "event_id")
            if event_id is None:
                raise HTTPException(status_code=400, detail="event_id requis")
            ev = await db.get(NavEvent, event_id)
            if ev is None or ev.leg_id != leg_id or ev.event_type != "noon":
                raise HTTPException(status_code=400, detail="événement Noon invalide")
            report = await _rg.generate_noon_report(db, leg, ev, author_user_id=user.id)
        else:  # stopover
            arr_id = _int_or_400(form.get("arrival_event_id"), "arrival_event_id")
            dep_id = _int_or_400(form.get("departure_event_id"), "departure_event_id")
            if arr_id is None or dep_id is None:
                raise HTTPException(
                    status_code=400, detail="arrival_event_id et departure_event_id requis"
                )
            arr = await db.get(NavEvent, arr_id)
            dep = await db.get(NavEvent, dep_id)
            if arr is None or dep is None:
                raise HTTPException(status_code=404, detail="événement d'escale introuvable")
            report = await _rg.generate_stopover_report(db, arr, dep, author_user_id=user.id)
    except _rg.ReportImmutableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except _rg.ReportGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.flush()
    await activity_record(
        db,
        action="mrv_report_generate",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="env_report",
        entity_id=report.id,
        entity_label=f"{report_type} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv/reports/{report.id}", status_code=303)


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def mrv_report_detail(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 5 — payload lisible (snapshot) + historique des modifications tracées."""
    import json

    report = await db.get(EnvReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    modifications = list(
        (
            await db.execute(
                select(EnvFieldModification)
                .where(EnvFieldModification.report_id == report_id)
                .order_by(EnvFieldModification.timestamp_utc)
            )
        )
        .scalars()
        .all()
    )
    payload_json = json.dumps(report.payload, indent=2, ensure_ascii=False)
    can_master = await has_permission_effective(db, user.role, "captain", "M")
    can_siege = await has_permission_effective(db, user.role, "mrv", "M")
    can_modify = await has_permission_effective(db, user.role, "mrv", "M")
    return templates.TemplateResponse(
        "staff/mrv/report_detail.html",
        {
            "request": request,
            "user": user,
            "report": report,
            "modifications": modifications,
            "payload_json": payload_json,
            "can_master": can_master,
            "can_siege": can_siege,
            "can_modify": can_modify,
        },
    )


@router.get("/reports/{report_id}.pdf")
async def mrv_report_pdf(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    """LOT 5 — PDF WeasyPrint rendu depuis le snapshot payload (jamais recalculé)."""
    from weasyprint import HTML

    from app.config import settings

    report = await db.get(EnvReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    tpl_name = _LOT5_PDF_TEMPLATES.get(report.report_type)
    if tpl_name is None:
        raise HTTPException(status_code=400, detail="type de rapport sans rendu PDF")
    modifications = list(
        (
            await db.execute(
                select(EnvFieldModification)
                .where(EnvFieldModification.report_id == report_id)
                .order_by(EnvFieldModification.timestamp_utc)
            )
        )
        .scalars()
        .all()
    )
    tpl = templates.get_template(tpl_name)
    html = tpl.render(
        report=report,
        payload=report.payload,
        modifications=modifications,
        issued_at=datetime.now(UTC),
        brand=brand_for_lang("fr"),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="MRV_{report.report_type}_{report.id}.pdf"'
        },
    )


@router.post("/reports/{report_id}/validate-master")
async def mrv_report_validate_master(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """LOT 5 — validation Master (bord) : le commandant valide le rapport."""
    report = await db.get(EnvReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    try:
        await _rg.validate_master(db, report, user)
    except _rg.ReportGenerationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.flush()
    # LOT 8 — déclencheur qualité : règles scope ``report`` (R18/R22) à la
    # validation Master. Signale (QCR + alertes routées), ne bloque jamais
    # une validation déjà actée.
    await _vrc.run_report_rules_and_route(db, report)
    await activity_record(
        db,
        action="mrv_report_validate_master",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="env_report",
        entity_id=report.id,
        entity_label=f"{report.report_type} #{report.id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv/reports/{report.id}", status_code=303)


@router.post("/reports/{report_id}/validate-siege")
async def mrv_report_validate_siege(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 5 — validation siège : 2ᵉ niveau, RÉSERVÉ au Carbon (refus propre sinon)."""
    report = await db.get(EnvReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    try:
        await _rg.validate_siege(db, report, user)
    except _rg.SiegeValidationNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except _rg.ReportGenerationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.flush()
    # LOT 8 — déclencheur qualité : scope ``report`` aussi à la validation siège.
    await _vrc.run_report_rules_and_route(db, report)
    await activity_record(
        db,
        action="mrv_report_validate_siege",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="env_report",
        entity_id=report.id,
        entity_label=f"{report.report_type} #{report.id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv/reports/{report.id}", status_code=303)


@router.post("/reports/{report_id}/fields/modify")
async def mrv_report_field_modify(
    report_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 5 — correction tracée d'un champ (R18) : justification obligatoire.

    ``apply_field_modification`` écrit la trace, met à jour le payload et
    enregistre l'audit (``services.activity``) — la route se contente du
    Redirect 303.
    """
    report = await db.get(EnvReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    form = dict(await request.form())
    field_name = (form.get("field_name") or "").strip()
    if not field_name:
        raise HTTPException(status_code=400, detail="field_name requis")
    corrected_value = form.get("corrected_value")
    justification = form.get("justification") or ""
    quality = (form.get("resulting_quality_status") or "").strip()
    try:
        await _rg.apply_field_modification(
            db, report, field_name, corrected_value, justification, user, quality
        )
    except _rg.ReportGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/mrv/reports/{report.id}", status_code=303)


# === LOT 7 — FLGO ===
# Intégration FLGO (Marad, LECTURE SEULE) : écran de consultation + import
# xlsx de repli. Câblage API + parsing : app/services/flgo_sync.py. Aucune
# écriture vers BunkerOperation/bunker.py depuis cet écran (rapprochements
# service-level, jamais wired ici — cf. flgo_sync.flgo_matches_for_bunker).


@router.get("/flgo", response_class=HTMLResponse)
async def mrv_flgo_index(
    request: Request,
    vessel_id: int | None = None,
    action_type: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 7 — liste des relevés FLGO (Marad), filtrable navire/période/type/
    source, avec indicateur de cohérence interne (R25 — signalé, jamais
    corrigé, cf. ``flgo_sync.check_internal_consistency``)."""
    stmt = (
        select(FlgoReading)
        .options(selectinload(FlgoReading.compartments))
        .order_by(FlgoReading.reading_datetime.desc())
    )
    if vessel_id:
        stmt = stmt.where(FlgoReading.vessel_id == vessel_id)
    if action_type:
        stmt = stmt.where(FlgoReading.action_type == action_type)
    if source:
        stmt = stmt.where(FlgoReading.source == source)
    if date_from:
        with contextlib.suppress(ValueError):
            stmt = stmt.where(
                FlgoReading.reading_datetime
                >= datetime.fromisoformat(date_from).replace(tzinfo=UTC)
            )
    if date_to:
        with contextlib.suppress(ValueError):
            stmt = stmt.where(
                FlgoReading.reading_datetime <= datetime.fromisoformat(date_to).replace(tzinfo=UTC)
            )
    readings = list((await db.execute(stmt.limit(200))).scalars().all())

    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    vessel_map = {v.id: v for v in vessels}

    rows = []
    for r in readings:
        check = await flgo_sync.check_internal_consistency(db, r, compartments=r.compartments)
        rows.append({"reading": r, "vessel": vessel_map.get(r.vessel_id), "check": check})

    return templates.TemplateResponse(
        "staff/mrv/flgo_index.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "vessels": vessels,
            "action_types": FLGO_ACTION_TYPES,
            "sources": FLGO_SOURCES,
            "filter_vessel_id": vessel_id,
            "filter_action_type": action_type,
            "filter_source": source,
            "filter_date_from": date_from,
            "filter_date_to": date_to,
            "audience": "mrv",
        },
    )


@router.post("/flgo/import", response_class=HTMLResponse)
async def mrv_flgo_import(
    request: Request,
    vessel_id: int = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
) -> HTMLResponse:
    """LOT 7 — import xlsx de repli (export IHM Marad FLGO), upsert idempotent.

    Restitue un rapport (importés/mis à jour/ignorés/erreurs) — jamais une
    exception non gérée sur un contenu malformé (cellule composite illisible,
    date illisible…) : ces anomalies sont collectées dans le rapport.
    """
    if content_length_exceeds_max(request.headers.get("content-length")):
        raise HTTPException(status_code=413, detail="fichier trop volumineux")
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="navire introuvable")

    name_check = validate_filename(file.filename or "")
    if not name_check.ok:
        raise HTTPException(status_code=400, detail=name_check.reason)
    content = await file.read()
    size_check = validate_size(content)
    if not size_check.ok:
        raise HTTPException(status_code=413, detail=size_check.reason)

    try:
        report = await flgo_sync.import_flgo_xlsx(db, vessel, content)
    except flgo_sync.FlgoSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # LOT 8 — déclencheur qualité : règles scope ``flgo`` (R25, 2 volets) sur
    # les lectures du navire fraîchement importées. No-op sans catalogue seedé.
    if report.imported or report.updated:
        await _vrc.run_flgo_rules_and_route(db, vessel.id)

    await activity_record(
        db,
        action="import",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="flgo_reading",
        entity_label=f"xlsx {vessel.code}",
        detail=(
            f"import={report.imported} maj={report.updated} "
            f"ignorés={report.skipped} erreurs={len(report.errors)}"
        ),
        ip_address=_client_ip(request),
    )
    return templates.TemplateResponse(
        "staff/mrv/flgo_import_result.html",
        {"request": request, "user": user, "report": report, "vessel": vessel, "audience": "mrv"},
    )


# === LOT 8 — qualité ===
#
# Écran /mrv/qualite : journal filtrable des QualityCheckResult (règle,
# sévérité, navire, leg, résultat, période) + compteurs de fails par sévérité +
# actions de traitement (mrv:M) : confirmation d'un reset compteur (R10) et
# acquittement d'un fail (stoppe la re-notification, cf.
# ``validation_rules_catalog.route_alerts``). Cron nocturne
# POST /api/mrv/quality-run (patron des crons Power Automate :
# X-API-Token temps constant, 503 non configuré).

import secrets as _secrets_l8  # noqa: E402

from fastapi.responses import JSONResponse  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.nav_event import NavEventEngineReading  # noqa: E402
from app.models.validation import QualityCheckResult, ValidationRule  # noqa: E402
from app.services import validation_rules_catalog as _vrc  # noqa: E402

api_router = APIRouter(tags=["mrv-quality"])

_QUAL_SEVERITIES: tuple[str, ...] = ("bloquant", "warning", "info")
_QUAL_RESULTS: tuple[str, ...] = ("pass", "fail")
_QUAL_ROWS_LIMIT = 200


def _qual_parse_date(raw: str | None):
    """'YYYY-MM-DD' → datetime UTC (None si vide/invalide — filtre ignoré)."""
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned).replace(tzinfo=UTC)
    except ValueError:
        return None


@router.get("/qualite", response_class=HTMLResponse)
async def mrv_qualite(
    request: Request,
    rule: str | None = None,
    severity: str | None = None,
    vessel_id: int | None = None,
    leg_id: int | None = None,
    result: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 8 — journal qualité filtrable + compteurs par sévérité.

    Filtre navire = jointure ``legs.vessel_id`` (les résultats sans leg —
    lectures FLGO par ex. — sortent du filtre navire, comportement documenté).
    """
    from sqlalchemy import func as _f

    filters = []
    rule = (rule or "").strip() or None
    if rule:
        filters.append(QualityCheckResult.rule_id == rule)
    severity = (severity or "").strip() or None
    if severity in _QUAL_SEVERITIES:
        filters.append(QualityCheckResult.severity_applied == severity)
    result = (result or "").strip() or None
    if result in _QUAL_RESULTS:
        filters.append(QualityCheckResult.result == result)
    if leg_id is not None:
        filters.append(QualityCheckResult.leg_id == leg_id)
    dt_from = _qual_parse_date(date_from)
    if dt_from is not None:
        filters.append(QualityCheckResult.executed_at >= dt_from)
    dt_to = _qual_parse_date(date_to)
    if dt_to is not None:
        filters.append(QualityCheckResult.executed_at < dt_to + timedelta(days=1))

    stmt = select(QualityCheckResult)
    count_stmt = select(QualityCheckResult.severity_applied, _f.count(QualityCheckResult.id)).where(
        QualityCheckResult.result == "fail"
    )
    if vessel_id is not None:
        stmt = stmt.join(Leg, Leg.id == QualityCheckResult.leg_id).where(Leg.vessel_id == vessel_id)
        count_stmt = count_stmt.join(Leg, Leg.id == QualityCheckResult.leg_id).where(
            Leg.vessel_id == vessel_id
        )
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    rows_qcr = list(
        (
            await db.execute(
                stmt.order_by(
                    QualityCheckResult.executed_at.desc(), QualityCheckResult.id.desc()
                ).limit(_QUAL_ROWS_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    severity_counts = dict.fromkeys(_QUAL_SEVERITIES, 0)
    for sev, n in (
        await db.execute(count_stmt.group_by(QualityCheckResult.severity_applied))
    ).all():
        if sev in severity_counts:
            severity_counts[sev] = int(n)

    # Décor : legs + navires des lignes affichées (2 requêtes groupées).
    leg_ids = {r.leg_id for r in rows_qcr if r.leg_id is not None}
    legs_by_id: dict[int, Leg] = {}
    if leg_ids:
        legs_by_id = {
            leg.id: leg
            for leg in (await db.execute(select(Leg).where(Leg.id.in_(leg_ids)))).scalars().all()
        }
    v_ids = {leg.vessel_id for leg in legs_by_id.values() if leg.vessel_id is not None}
    vessels_by_id: dict[int, Vessel] = {}
    if v_ids:
        vessels_by_id = {
            v.id: v
            for v in (await db.execute(select(Vessel).where(Vessel.id.in_(v_ids)))).scalars().all()
        }

    rows = []
    for r in rows_qcr:
        leg = legs_by_id.get(r.leg_id) if r.leg_id is not None else None
        reading_ids = []
        if isinstance(r.details, dict):
            reading_ids = [i for i in (r.details.get("reading_ids") or []) if isinstance(i, int)]
        rows.append(
            {
                "qcr": r,
                "leg": leg,
                "vessel": (
                    vessels_by_id.get(leg.vessel_id) if leg is not None and leg.vessel_id else None
                ),
                "reading_ids": reading_ids,
            }
        )

    # Resets compteur en attente de confirmation (R10) — panneau d'action.
    pending_resets = list(
        (
            await db.execute(
                select(NavEventEngineReading)
                .where(
                    NavEventEngineReading.is_counter_reset.is_(True),
                    NavEventEngineReading.reset_confirmed_by.is_(None),
                )
                .order_by(NavEventEngineReading.id.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    rules = list(
        (await db.execute(select(ValidationRule).order_by(ValidationRule.rule_id))).scalars().all()
    )
    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)))
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        "staff/mrv/qualite.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "severity_counts": severity_counts,
            "pending_resets": pending_resets,
            "rules": rules,
            "vessels": vessels,
            "can_act": await has_permission_effective(db, user.role, "mrv", "M"),
            "filter_rule": rule or "",
            "filter_severity": severity or "",
            "filter_vessel_id": vessel_id,
            "filter_leg_id": leg_id,
            "filter_result": result or "",
            "filter_date_from": (date_from or "").strip(),
            "filter_date_to": (date_to or "").strip(),
        },
    )


@router.post("/qualite/engine-readings/{reading_id}/confirm-reset")
async def mrv_qualite_confirm_reset(
    reading_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 8 — R10 : confirmation d'une réinitialisation de compteur légitime.

    Renseigne ``reset_confirmed_by``/``reset_confirmed_at`` (et pose
    ``is_counter_reset`` si le bord ne l'avait pas déclaré) → la couche de
    calcul (``inter_event_compute``) reprend une nouvelle base de référence et
    R10/IR04 passent au prochain run. Tracé dans l'activity trail.
    """
    reading = await db.get(NavEventEngineReading, reading_id)
    if reading is None:
        raise HTTPException(status_code=404, detail="Relevé compteur introuvable")
    if reading.reset_confirmed_by is not None:
        raise HTTPException(status_code=409, detail="Reset déjà confirmé")
    reading.is_counter_reset = True
    reading.reset_confirmed_by = user.id
    reading.reset_confirmed_at = datetime.now(UTC)
    await db.flush()
    await activity_record(
        db,
        action="mrv_counter_reset_confirm",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="nav_event_engine_reading",
        entity_id=reading.id,
        entity_label=f"event #{reading.event_id} · engine #{reading.engine_id}",
        detail="Réinitialisation compteur confirmée (R10) — nouvelle base de référence.",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/qualite", status_code=303)


@router.post("/qualite/{qcr_id}/acknowledge")
async def mrv_qualite_acknowledge(
    qcr_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 8 — acquittement d'un ``fail`` : stoppe la re-notification (dédup
    ``route_alerts``). Le journal reste intact (append-only) — seule l'action
    de traitement est datée/attribuée. Tracé dans l'activity trail."""
    qcr = await db.get(QualityCheckResult, qcr_id)
    if qcr is None:
        raise HTTPException(status_code=404, detail="Résultat de contrôle introuvable")
    if qcr.result != "fail":
        raise HTTPException(status_code=400, detail="Seul un contrôle en échec s'acquitte")
    if qcr.acknowledged_at is not None:
        raise HTTPException(status_code=409, detail="Déjà acquitté")
    qcr.acknowledged_at = datetime.now(UTC)
    qcr.acknowledged_by = user.id
    await db.flush()
    await activity_record(
        db,
        action="mrv_quality_acknowledge",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="quality_check_result",
        entity_id=qcr.id,
        entity_label=f"{qcr.rule_id} · {qcr.subject_type}#{qcr.subject_id}",
        detail=(qcr.message or "")[:200],
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/qualite", status_code=303)


# === LOT 10 — datasets ===
# Sorties réglementaires OVDLA / OVDBR déposées chez DNV — voie UNIQUE depuis le
# lot 14 (le CSV 18 colonnes a été retiré, Q3). Génération + aperçu des lignes
# (statut/exclusions motivées) + snapshot gelé + exports xlsx/csv. Toute la
# logique (deltas, portes, gel) vit dans ``services.mrv_dataset`` — ces routes
# n'orchestrent que la sélection + le rendu.

from app.services import mrv_dataset as _mrv_ds  # noqa: E402


def _dataset_period(year: int | None) -> tuple[datetime | None, datetime | None]:
    """Année de reporting → (début, fin UTC) ; ``None`` = tout l'historique."""
    if year is None:
        return None, None
    start = datetime(year, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)
    return start, end


async def _dataset_years(db: AsyncSession, vessel_id: int | None) -> list[int]:
    """Années couvertes par les événements de navigation (pour le sélecteur)."""
    from app.models.nav_event import NavEvent

    stmt = select(NavEvent.datetime_utc).where(NavEvent.datetime_utc.isnot(None))
    if vessel_id:
        stmt = stmt.where(NavEvent.vessel_id == vessel_id)
    dts = [d for d in (await db.execute(stmt)).scalars().all() if d is not None]
    return sorted({d.year for d in dts}, reverse=True)


async def _build_dataset_rows(
    db: AsyncSession, vessel: Vessel, year: int | None, *, alert: bool
) -> tuple[list, list]:
    start, end = _dataset_period(year)
    ovdla = await _mrv_ds.build_ovdla_rows(db, vessel, start, end, alert=alert)
    ovdbr = await _mrv_ds.build_ovdbr_rows(db, vessel, (start, end), alert=alert)
    return ovdla, ovdbr


@router.get("/datasets", response_class=HTMLResponse)
async def mrv_datasets(
    request: Request,
    vessel_id: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """LOT 10 — écran datasets : sélection navire+période, aperçu OVDLA/OVDBR
    (statut + exclusions motivées). Génération/téléchargements séparés."""
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    vessel = None
    if vessel_id:
        vessel = await db.get(Vessel, vessel_id)
    elif vessels:
        vessel = vessels[0]

    years = await _dataset_years(db, vessel.id if vessel else None)
    ovdla_rows: list = []
    ovdbr_rows: list = []
    if vessel is not None:
        ovdla_rows, ovdbr_rows = await _build_dataset_rows(db, vessel, year, alert=False)

    def _counts(rows: list) -> dict:
        included = [r for r in rows if r.included]
        return {
            "total": len(rows),
            "included": len(included),
            "excluded": len(rows) - len(included),
        }

    # LOT 14 — OVDLA/OVDBR sont désormais la voie de dépôt DNV UNIQUE (CSV 18
    # col. retiré, Q3) : plus de flag ``mrv_v2_exports`` ni de bandeau conditionnel.
    return templates.TemplateResponse(
        "staff/mrv/datasets.html",
        {
            "request": request,
            "user": user,
            "vessels": vessels,
            "vessel": vessel,
            "years": years,
            "year": year,
            "ovdla_rows": ovdla_rows,
            "ovdbr_rows": ovdbr_rows,
            "ovdla_counts": _counts(ovdla_rows),
            "ovdbr_counts": _counts(ovdbr_rows),
            "ovdla_columns": _mrv_ds.OVDLA_COLUMNS,
            "ovdbr_columns": _mrv_ds.OVDBR_COLUMNS,
        },
    )


@router.post("/datasets/generate")
async def mrv_datasets_generate(
    request: Request,
    vessel_id: int = Form(...),
    year: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """LOT 10 — génère + GÈLE (snapshot) les entrées OVDLA/OVDBR du navire.

    Les exclusions ``under_conformity`` déclenchent une alerte admin
    (``alert=True``, pattern lot 8). Idempotent (upsert des payloads gelés)."""
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire inconnu")
    ovdla, ovdbr = await _build_dataset_rows(db, vessel, year, alert=True)
    snap = await _mrv_ds.snapshot_entries(db, ovdla + ovdbr)
    await activity_record(
        db,
        action="mrv_dataset_generate",
        user_id=getattr(user, "id", None),
        user_name=getattr(user, "full_name", None) or getattr(user, "username", None),
        user_role=getattr(user, "role", None),
        module="mrv",
        entity_type="mrv_dataset",
        entity_id=vessel.id,
        entity_label=f"OVDLA/OVDBR {vessel.code} {year or 'all'}",
        detail=f"gel: créés={snap['created']}, maj={snap['updated']}",
        ip_address=_client_ip(request),
    )
    dest = f"/mrv/datasets?vessel_id={vessel.id}" + (f"&year={year}" if year else "")
    return RedirectResponse(url=dest, status_code=303)


_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _dataset_download(
    db: AsyncSession, vessel_id: int, year: int | None, dataset: str, fmt: str
) -> Response:
    vessel = await db.get(Vessel, vessel_id)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Navire inconnu")
    ovdla, ovdbr = await _build_dataset_rows(db, vessel, year, alert=False)
    rows = ovdla if dataset == "ovdla" else ovdbr
    stamp = str(year) if year else datetime.now().strftime("%Y%m%d")
    base = f"{dataset.upper()}_{vessel.code}_{stamp}"
    if fmt == "xlsx":
        content = _mrv_ds.export_xlsx(rows, kind=dataset)
        return Response(
            content=content,
            media_type=_XLSX_MEDIA,
            headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'},
        )
    csv_text = _mrv_ds.export_csv(rows, kind=dataset)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
    )


@router.get("/datasets/ovdla.xlsx")
async def mrv_datasets_ovdla_xlsx(
    vessel_id: int,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    return await _dataset_download(db, vessel_id, year, "ovdla", "xlsx")


@router.get("/datasets/ovdla.csv")
async def mrv_datasets_ovdla_csv(
    vessel_id: int,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    return await _dataset_download(db, vessel_id, year, "ovdla", "csv")


@router.get("/datasets/ovdbr.xlsx")
async def mrv_datasets_ovdbr_xlsx(
    vessel_id: int,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    return await _dataset_download(db, vessel_id, year, "ovdbr", "xlsx")


@router.get("/datasets/ovdbr.csv")
async def mrv_datasets_ovdbr_csv(
    vessel_id: int,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    return await _dataset_download(db, vessel_id, year, "ovdbr", "csv")


@api_router.post("/api/mrv/quality-run")
async def mrv_quality_run_cron(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """LOT 8 — cron externe (Power Automate) : run nocturne du moteur de règles.

    Exécute les scopes ``event`` (dont IR01-IR05 sur séquences) + ``voyage``
    sur les legs ACTIFS (non clôturés, non annulés) de chaque navire, route
    les alertes (idempotent) et renvoie ``{legs_scanned, checks, fails}``.
    Auth ``X-API-Token`` (temps constant) ; 503 si ``MRV_QUALITY_API_TOKEN``
    n'est pas configuré — patron des crons existants.
    """
    expected = (settings.mrv_quality_api_token or "").strip() or None
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="MRV_QUALITY_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets_l8.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    summary = await _vrc.run_nightly_quality(db)
    return JSONResponse(summary)
