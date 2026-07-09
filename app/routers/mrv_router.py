"""MRV — events fuel, exports DNV CSV + Carbon Report.

Reprise de la V3.0.0. Le mapping SOF→MRV est porté par
services.mrv_export.SOF_TO_MRV_MAP, appelé en hook quand un nouvel SOF
event est créé (à brancher en Phase 5 si besoin).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.bunker import BUNKER_STATUSES, BunkerOperation
from app.models.leg import Leg
from app.models.mrv import MRVEvent, MRVParameter
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import bunkering
from app.services.activity import record as activity_record
from app.services.mrv_compute import recompute_leg
from app.services.mrv_export import (
    CO2_EMISSION_FACTOR_MDO,
    build_dnv_rows,
    carbon_report_summary,
    dnv_csv_18,
)
from app.templating import brand_for_lang, templates
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import selectinload
from app.models.flgo import FLGO_ACTION_TYPES, FLGO_SOURCES, FlgoReading
from app.services import flgo_sync
from app.services.safe_files import content_length_exceeds_max
from app.utils.file_validation import validate_filename, validate_size



# MRV — typage des champs d'événement pour la coercition des formulaires.
_EVENT_DECIMAL_FIELDS = (
    "fuel_mass_t",
    "fuel_volume_l",
    "rob_l",
    "distance_nm",
    "cargo_carried_t",
    "port_me_do_counter",
    "stbd_me_do_counter",
    "fwd_gen_do_counter",
    "aft_gen_do_counter",
    "bunkering_qty_t",
    "lat_min",
    "lon_min",
)
_EVENT_INT_FIELDS = ("lat_deg", "lon_deg")
_EVENT_STR_FIELDS = ("event_kind", "fuel_type", "notes", "lat_ns", "lon_ew")


def _apply_event_form(ev: MRVEvent, form: dict) -> None:
    """Applique les champs présents d'un formulaire à un événement MRV.

    Une valeur vide efface le champ (None) ; une valeur NON VIDE mais invalide
    lève une 400 plutôt que de corrompre/nullifier silencieusement la donnée
    (intégrité réglementaire MRV).
    """
    from decimal import InvalidOperation

    for f in _EVENT_STR_FIELDS:
        if f in form:
            setattr(ev, f, (form.get(f) or "").strip() or None)
    for f in _EVENT_DECIMAL_FIELDS:
        if f in form:
            raw = (form.get(f) or "").strip().replace(",", ".")
            if not raw:
                setattr(ev, f, None)
                continue
            try:
                setattr(ev, f, Decimal(raw))
            except (InvalidOperation, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"valeur invalide pour {f}") from exc
    for f in _EVENT_INT_FIELDS:
        if f in form:
            raw = (form.get(f) or "").strip()
            if not raw:
                setattr(ev, f, None)
                continue
            try:
                setattr(ev, f, int(raw))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"valeur invalide pour {f}") from exc
    if "recorded_at" in form and (form.get("recorded_at") or "").strip():
        try:
            ev.recorded_at = datetime.fromisoformat(form["recorded_at"].strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="recorded_at invalide") from exc


router = APIRouter(prefix="/mrv", tags=["mrv"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def mrv_index(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    vessels = f["vessels"]
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(30))).scalars().all())
    events = list(
        (await db.execute(select(MRVEvent).order_by(MRVEvent.recorded_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    # Decorate events with vessel/leg info
    leg_ids = {e.leg_id for e in events}
    leg_map = {}
    for lid in leg_ids:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg
    summary = carbon_report_summary([_decor(e, leg_map) for e in events])
    from app.services.leg_filter import leg_select_options

    leg_options = await leg_select_options(db)
    response = templates.TemplateResponse(
        "staff/mrv/index.html",
        {
            "request": request,
            "user": user,
            "leg_options": leg_options,
            "leg_filter_ctx": f,
            "vessels": vessels,
            "legs": legs,
            "events": events,
            "leg_map": leg_map,
            "summary": summary,
            "co2_factor": CO2_EMISSION_FACTOR_MDO,
        },
    )
    set_leg_filter_cookie(response, f)
    return response


@router.post("/legs/{leg_id}/events")
async def add_event(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """Création d'un événement MRV (compteurs DO, ROB, cargo, position DMS)."""
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404)
    form = dict(await request.form())
    if not (form.get("event_kind") or "").strip() or not (form.get("recorded_at") or "").strip():
        raise HTTPException(status_code=400, detail="event_kind et recorded_at requis")
    ev = MRVEvent(
        leg_id=leg_id,
        event_kind=form["event_kind"].strip(),
        recorded_at=datetime.fromisoformat(form["recorded_at"].strip()),
        fuel_type=(form.get("fuel_type") or "MDO").strip() or "MDO",
        created_by=user.full_name or user.username,
    )
    _apply_event_form(ev, form)
    # MRV-07 — pré-remplit la position DMS depuis le dernier point GPS du navire
    # si l'opérateur ne l'a pas saisie (best-effort, saisie manuelle prioritaire).
    from app.services.mrv_compute import autofill_event_position

    await autofill_event_position(db, leg, ev)
    db.add(ev)
    await db.flush()
    # A1 hybride — recalcule conso ME/AE, ROB chaîné et qualité du leg.
    await recompute_leg(db, leg_id)
    await activity_record(
        db,
        action="create",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="mrv_event",
        entity_id=ev.id,
        entity_label=f"{ev.event_kind} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv?leg_id={leg_id}", status_code=303)


@router.post("/events/{event_id}/edit")
async def edit_event(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    """MRV-03 — édition d'un événement (+ recalcul du leg)."""
    ev = await db.get(MRVEvent, event_id)
    if ev is None:
        raise HTTPException(status_code=404)
    _apply_event_form(ev, dict(await request.form()))
    await db.flush()
    await recompute_leg(db, ev.leg_id)
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="mrv_event",
        entity_id=ev.id,
        entity_label=f"{ev.event_kind} leg={ev.leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv?leg_id={ev.leg_id}", status_code=303)


@router.post("/events/{event_id}/delete")
async def delete_event(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "S")),
):
    """MRV-03 — suppression d'un événement (+ recalcul du leg)."""
    ev = await db.get(MRVEvent, event_id)
    if ev is None:
        raise HTTPException(status_code=404)
    leg_id = ev.leg_id
    await db.delete(ev)
    await db.flush()
    await recompute_leg(db, leg_id)
    await activity_record(
        db,
        action="delete",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="mrv_event",
        entity_id=event_id,
        entity_label=f"event {event_id} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url=f"/mrv?leg_id={leg_id}", status_code=303)


async def _resolve_maps(db: AsyncSession, events: list[MRVEvent]):
    """Charge les maps leg/vessel/port indexées par id pour l'export DNV."""
    leg_map: dict[int, Leg] = {}
    for lid in {e.leg_id for e in events}:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg
    vessel_map: dict[int, Vessel] = {}
    port_ids: set[int] = set()
    for leg in leg_map.values():
        if leg.vessel_id and leg.vessel_id not in vessel_map:
            v = await db.get(Vessel, leg.vessel_id)
            if v:
                vessel_map[leg.vessel_id] = v
        if leg.departure_port_id:
            port_ids.add(leg.departure_port_id)
        if leg.arrival_port_id:
            port_ids.add(leg.arrival_port_id)
    port_map = {pid: await db.get(Port, pid) for pid in port_ids}
    port_map = {k: v for k, v in port_map.items() if v is not None}
    return leg_map, vessel_map, port_map


@router.get("/export/dnv.csv")
async def export_dnv_csv(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    """MRV-01 — export DNV Veracity 18 colonnes (IMO renseigné via le navire)."""
    events = list(
        (await db.execute(select(MRVEvent).order_by(MRVEvent.recorded_at.asc()))).scalars().all()
    )
    leg_map, vessel_map, port_map = await _resolve_maps(db, events)
    rows = build_dnv_rows(events, leg_map=leg_map, vessel_map=vessel_map, port_map=port_map)
    csv = dnv_csv_18(rows)
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        content=csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="mrv_dnv_veracity_{stamp}.csv"'},
    )


@router.get("/export/carbon-report.pdf")
async def export_carbon_report_pdf(
    vessel_id: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    """MRV-02 — Carbon Report PDF. Bloqué (400) si un événement est en erreur qualité."""
    from weasyprint import HTML

    from app.config import settings

    stmt = select(MRVEvent).order_by(MRVEvent.recorded_at.asc())
    events = list((await db.execute(stmt)).scalars().all())
    leg_map, vessel_map, _port = await _resolve_maps(db, events)
    if vessel_id is not None:
        events = [
            e for e in events if leg_map.get(e.leg_id) and leg_map[e.leg_id].vessel_id == vessel_id
        ]
    if year is not None:
        events = [e for e in events if e.recorded_at and e.recorded_at.year == year]

    # Garde-fou réglementaire SCOPÉ au périmètre du rapport : pas de rapport
    # tant qu'un événement DU PÉRIMÈTRE est en erreur qualité.
    if any(e.quality_status == "error" for e in events):
        raise HTTPException(
            status_code=400,
            detail="Carbon Report bloqué : des événements MRV du périmètre sont en erreur qualité.",
        )
    summary = carbon_report_summary([_AdapterMRV(e) for e in events])
    vessel = vessel_map.get(vessel_id) if vessel_id else None

    tpl = templates.get_template("pdf/carbon_report.html")
    html = tpl.render(
        events=events,
        leg_map=leg_map,
        summary=summary,
        vessel=vessel,
        year=year,
        co2_factor=CO2_EMISSION_FACTOR_MDO,
        issued_at=datetime.now(),
        brand=brand_for_lang("fr"),
        site_url=settings.site_url,
    )
    pdf = HTML(string=html, base_url=settings.site_url).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="MRV_Carbon_Report.pdf"'},
    )


@router.get("/params", response_class=HTMLResponse)
async def mrv_params_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
) -> HTMLResponse:
    """MRV-06 — écran d'édition des paramètres MRV (densité, déviation, facteur CO₂)."""
    from app.services.co2 import get_do_co2_factor

    params = {p.name: p for p in (await db.execute(select(MRVParameter))).scalars().all()}
    co2_factor = await get_do_co2_factor(db)
    return templates.TemplateResponse(
        "staff/mrv/params.html",
        {"request": request, "user": user, "params": params, "co2_factor": co2_factor},
    )


@router.post("/params")
async def mrv_params_save(
    request: Request,
    avg_mdo_density: str = Form(...),
    mdo_admissible_deviation: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    from decimal import InvalidOperation

    spec = {
        "avg_mdo_density": (avg_mdo_density, "t/m³", "Densité moyenne MDO"),
        "mdo_admissible_deviation": (mdo_admissible_deviation, "t", "Écart ROB admissible"),
    }
    for name, (raw, unit, desc) in spec.items():
        try:
            value = Decimal(str(raw).strip().replace(",", "."))
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"valeur invalide pour {name}") from exc
        row = (
            await db.execute(select(MRVParameter).where(MRVParameter.name == name))
        ).scalar_one_or_none()
        if row is None:
            db.add(MRVParameter(name=name, value=value, unit=unit, description=desc))
        else:
            row.value = value
    await db.flush()
    await activity_record(
        db,
        action="update",
        user_id=user.id,
        user_name=user.full_name or user.username,
        user_role=user.role,
        module="mrv",
        entity_type="mrv_parameter",
        entity_label="params MRV",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv/params", status_code=303)


# ───────── helpers ─────────


class _AdapterMRV:
    """Adapt MRVEvent to mrv_export.to_dnv_csv expectations."""

    def __init__(self, ev: MRVEvent, leg: Leg | None = None, vessel_imo: str = ""):
        self._ev = ev
        self.vessel_imo = vessel_imo
        self.leg_code = leg.leg_code if leg else ""
        self.event_type = ev.event_kind
        self.occurred_at = ev.recorded_at
        self.fuel_type = ev.fuel_type
        self.rob_t = float(ev.rob_calculated_t) if ev.rob_calculated_t is not None else None
        # Consommation = total calculé (compteurs) sinon masse fournie (noon).
        if ev.total_consumption_t is not None:
            self.consumed_t = float(ev.total_consumption_t)
        elif ev.fuel_mass_t is not None:
            self.consumed_t = float(ev.fuel_mass_t)
        else:
            self.consumed_t = None
        self.notes = ev.notes or ""


def _decor(ev: MRVEvent, leg_map: dict[int, Leg]) -> _AdapterMRV:
    return _AdapterMRV(ev, leg_map.get(ev.leg_id))


@router.get("/legs/{leg_id}/carbon", response_class=HTMLResponse)
async def mrv_carbon_report(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """Carbon Report d'un leg (CFOTE_09) — calculé automatiquement.

    Résultats (consommation DO, CO₂ émis, intensités par mille / tonne /
    tonne·mille) dérivés des noon reports + distance + cargo + facteur DO.
    """
    from app.models.port import Port
    from app.services.carbon import compute_carbon_for_leg

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Leg not found")
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None
    carbon = await compute_carbon_for_leg(db, leg)
    return templates.TemplateResponse(
        "staff/mrv/carbon_report.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "carbon": carbon,
        },
    )


@router.get("/legs/{leg_id}", response_class=HTMLResponse)
async def mrv_leg_detail(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    """MRV-08 — vue détail d'un leg : table d'événements ligne-à-ligne (avec
    badges qualité), agrégats consommation / bunkering / cargo, et report
    carbone (CO₂ + intensités) du leg."""
    from app.services.carbon import compute_carbon_for_leg

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Leg not found")
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None

    events = list(
        (
            await db.execute(
                select(MRVEvent)
                .where(MRVEvent.leg_id == leg_id)
                .order_by(MRVEvent.recorded_at.asc())
            )
        )
        .scalars()
        .all()
    )

    zero = Decimal("0")
    totals = {
        "consumption_t": sum(
            (e.total_consumption_t or e.fuel_mass_t or zero for e in events), zero
        ),
        "bunkering_t": sum((e.bunkering_qty_t or zero for e in events), zero),
        "distance_nm": sum((e.distance_nm or zero for e in events), zero),
        "cargo_t": max((e.cargo_carried_t or zero for e in events), default=zero),
    }
    quality = {
        "error": sum(1 for e in events if e.quality_status == "error"),
        "warning": sum(1 for e in events if e.quality_status == "warning"),
        "ok": sum(1 for e in events if e.quality_status not in ("error", "warning")),
    }
    carbon = await compute_carbon_for_leg(db, leg)

    return templates.TemplateResponse(
        "staff/mrv/leg_detail.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "events": events,
            "totals": totals,
            "quality": quality,
            "carbon": carbon,
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
    thresholds = list(
        (await db.execute(select(ValidationRuleThreshold))).scalars().all()
    )
    dashboard_params = list(
        (
            await db.execute(
                select(DashboardParameter).order_by(DashboardParameter.parameter_name)
            )
        )
        .scalars()
        .all()
    )
    vessels = list(
        (
            await db.execute(
                select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)
            )
        )
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
        await db.execute(select(NavEvent.status).where(NavEvent.leg_id == leg_id))
    ).scalars().all()
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
    legs = list(
        (await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(40))).scalars().all()
    )
    vessel_ids = {leg.vessel_id for leg in legs if leg.vessel_id is not None}
    vessels: dict[int, Vessel] = {}
    if vessel_ids:
        vessels = {
            v.id: v
            for v in (
                await db.execute(select(Vessel).where(Vessel.id.in_(vessel_ids)))
            ).scalars().all()
        }
    rows = []
    for leg in legs:
        report_statuses = (
            await db.execute(select(EnvReport.status).where(EnvReport.leg_id == leg.id))
        ).scalars().all()
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
            await db.execute(
                select(Leg)
                .where(Leg.vessel_id == leg.vessel_id, Leg.etd > leg.etd)
                .order_by(Leg.etd.asc())
                .limit(1)
            )
        ).scalars().first()
        if next_leg is not None:
            next_events = await _iec.finalized_events_for_leg(db, next_leg.id)
            departure_event = next(
                (e for e in next_events if e.event_type == "departure"), None
            )

    reports = list(
        (
            await db.execute(
                select(EnvReport)
                .where(EnvReport.leg_id == leg_id)
                .order_by(EnvReport.report_type, EnvReport.id)
            )
        ).scalars().all()
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
        ).scalars().all()
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
        ).scalars().all()
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
                FlgoReading.reading_datetime
                <= datetime.fromisoformat(date_to).replace(tzinfo=UTC)
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
