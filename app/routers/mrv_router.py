"""MRV — events fuel, exports DNV CSV + Carbon Report.

Reprise de la V3.0.0. Le mapping SOF→MRV est porté par
services.mrv_export.SOF_TO_MRV_MAP, appelé en hook quand un nouvel SOF
event est créé (à brancher en Phase 5 si besoin).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.mrv import MRVEvent
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.mrv_export import (
    CO2_EMISSION_FACTOR_MDO,
    carbon_report_summary,
    to_dnv_csv,
)
from app.templating import templates

router = APIRouter(prefix="/mrv", tags=["mrv"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def mrv_index(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    legs = list((await db.execute(
        select(Leg).order_by(Leg.etd.desc()).limit(30)
    )).scalars().all())
    events = list((await db.execute(
        select(MRVEvent).order_by(MRVEvent.recorded_at.desc()).limit(50)
    )).scalars().all())
    # Decorate events with vessel/leg info
    leg_ids = {e.leg_id for e in events}
    leg_map = {}
    for lid in leg_ids:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg
    summary = carbon_report_summary([_decor(e, leg_map) for e in events])
    return templates.TemplateResponse(
        "staff/mrv/index.html",
        {
            "request": request, "user": user,
            "vessels": vessels, "legs": legs, "events": events,
            "leg_map": leg_map, "summary": summary,
            "co2_factor": CO2_EMISSION_FACTOR_MDO,
        },
    )


@router.post("/legs/{leg_id}/events")
async def add_event(
    leg_id: int,
    request: Request,
    event_kind: str = Form(...),
    recorded_at: str = Form(...),
    fuel_type: str = Form("MDO"),
    fuel_mass_t: float | None = Form(None),
    fuel_volume_l: float | None = Form(None),
    rob_l: float | None = Form(None),
    distance_nm: float | None = Form(None),
    cargo_carried_t: float | None = Form(None),
    notes: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "M")),
):
    if not await db.get(Leg, leg_id):
        raise HTTPException(status_code=404)
    ev = MRVEvent(
        leg_id=leg_id,
        event_kind=event_kind,
        recorded_at=datetime.fromisoformat(recorded_at),
        fuel_type=fuel_type,
        fuel_mass_t=Decimal(str(fuel_mass_t)) if fuel_mass_t is not None else None,
        fuel_volume_l=Decimal(str(fuel_volume_l)) if fuel_volume_l is not None else None,
        rob_l=Decimal(str(rob_l)) if rob_l is not None else None,
        distance_nm=Decimal(str(distance_nm)) if distance_nm is not None else None,
        cargo_carried_t=Decimal(str(cargo_carried_t)) if cargo_carried_t is not None else None,
        notes=notes,
    )
    db.add(ev)
    await db.flush()
    await activity_record(
        db, action="create", user_id=user.id, user_name=user.full_name or user.username,
        user_role=user.role, module="mrv", entity_type="mrv_event",
        entity_id=ev.id, entity_label=f"{event_kind} leg={leg_id}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse(url="/mrv", status_code=303)


@router.get("/export/dnv.csv")
async def export_dnv_csv(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    events = list((await db.execute(
        select(MRVEvent).order_by(MRVEvent.recorded_at.asc())
    )).scalars().all())
    leg_ids = {e.leg_id for e in events}
    leg_map = {}
    for lid in leg_ids:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg
    rows = [_decor(e, leg_map) for e in events]
    csv = to_dnv_csv(rows)
    return Response(
        content=csv,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="mrv_dnv.csv"'},
    )


@router.get("/export/carbon-report.txt", response_class=PlainTextResponse)
async def export_carbon_report(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("mrv", "C")),
):
    events = list((await db.execute(
        select(MRVEvent).order_by(MRVEvent.recorded_at.asc())
    )).scalars().all())
    summary = carbon_report_summary([_AdapterMRV(e) for e in events])
    body = (
        "NEWTOWT — MRV Carbon Report\n"
        "============================\n\n"
        f"Period       : agrégé (toutes années)\n"
        f"Events count : {summary['event_count']}\n"
        f"Total fuel   : {summary['total_fuel_t']:.3f} t\n"
        f"Total CO₂    : {summary['total_co2_t']:.3f} t\n"
        f"Factor MDO   : {CO2_EMISSION_FACTOR_MDO} t CO₂ / t fuel\n"
    )
    return PlainTextResponse(content=body)


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
        self.rob_t = float(ev.rob_l) / 1000.0 if ev.rob_l is not None else None  # rough
        self.consumed_t = float(ev.fuel_mass_t) if ev.fuel_mass_t is not None else None
        self.notes = ev.notes or ""


def _decor(ev: MRVEvent, leg_map: dict[int, Leg]) -> _AdapterMRV:
    return _AdapterMRV(ev, leg_map.get(ev.leg_id))


def _client_ip(request: Request) -> str | None:
    return request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
