"""MRV — events fuel, exports DNV CSV + Carbon Report.

Reprise de la V3.0.0. Le mapping SOF→MRV est porté par
services.mrv_export.SOF_TO_MRV_MAP, appelé en hook quand un nouvel SOF
event est créé (à brancher en Phase 5 si besoin).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.mrv import MRVEvent, MRVParameter
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.mrv_compute import recompute_leg
from app.services.mrv_export import (
    CO2_EMISSION_FACTOR_MDO,
    build_dnv_rows,
    carbon_report_summary,
    dnv_csv_18,
)
from app.templating import brand_for_lang, templates

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
        events = [e for e in events if leg_map.get(e.leg_id) and leg_map[e.leg_id].vessel_id == vessel_id]
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
    params = {
        p.name: p
        for p in (await db.execute(select(MRVParameter))).scalars().all()
    }
    return templates.TemplateResponse(
        "staff/mrv/params.html",
        {"request": request, "user": user, "params": params},
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


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )
