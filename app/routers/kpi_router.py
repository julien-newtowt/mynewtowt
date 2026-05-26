"""KPI — indicateurs de performance par leg (tonnage, CO₂, ponctualité).

Expose :
  GET  /kpi          — tableau de bord avec agrégats
  GET  /kpi/export.csv — export CSV de tous les LegKPI
  POST /kpi/legs/{leg_id} — création ou mise à jour d'un LegKPI
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.templating import templates

router = APIRouter(prefix="/kpi", tags=["kpi"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_decimal(v: str | None) -> Decimal | None:
    """Convert a form string value to Decimal, returning None for blank/missing."""
    if v and v.strip():
        return Decimal(v.strip())
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def kpi_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    kpis = list(
        (await db.execute(select(LegKPI).order_by(LegKPI.id.desc()))).scalars().all()
    )
    legs = list(
        (await db.execute(select(Leg).order_by(Leg.etd.desc()))).scalars().all()
    )

    # Aggregates
    total_tonnage_t = sum((k.tonnage_kg or Decimal(0)) for k in kpis) / Decimal(1000)
    total_co2_avoided_kg = sum((k.co2_avoided_kg or Decimal(0)) for k in kpis)

    if kpis:
        on_time_count = sum(1 for k in kpis if k.on_time)
        on_time_pct = on_time_count / len(kpis) * 100.0
    else:
        on_time_pct = 0.0

    return templates.TemplateResponse(
        "staff/kpi/index.html",
        {
            "request": request,
            "user": user,
            "kpis": kpis,
            "legs": legs,
            "total_tonnage_t": total_tonnage_t,
            "total_co2_avoided_kg": total_co2_avoided_kg,
            "on_time_pct": on_time_pct,
        },
    )


@router.get("/export.csv")
async def kpi_export_csv(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> StreamingResponse:
    kpis = list(
        (await db.execute(select(LegKPI).order_by(LegKPI.id.asc()))).scalars().all()
    )

    # Build leg_id → leg_code mapping
    leg_ids = {k.leg_id for k in kpis}
    leg_map: dict[int, str] = {}
    for lid in leg_ids:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg.leg_code

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "leg_code",
        "palettes_carried",
        "tonnage_kg",
        "distance_nm",
        "duration_hours",
        "avg_speed_kn",
        "on_time",
        "occupancy_pct",
        "co2_avoided_kg",
    ])
    for k in kpis:
        writer.writerow([
            leg_map.get(k.leg_id, ""),
            k.palettes_carried,
            k.tonnage_kg,
            k.distance_nm,
            k.duration_hours,
            k.avg_speed_kn,
            "1" if k.on_time else "0",
            k.occupancy_pct,
            k.co2_avoided_kg,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="kpi_export.csv"'},
    )


@router.post("/legs/{leg_id}/sync")
async def kpi_sync(
    leg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "M")),
) -> RedirectResponse:
    """Recalcule automatiquement le LegKPI depuis les données réelles (bookings + SOF)."""
    from app.models.leg import Leg as LegModel
    from app.services.kpi import compute_for_leg

    leg = await db.get(LegModel, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Leg not found")

    await compute_for_leg(db, leg)
    await activity_record(
        db, action="kpi_sync", user_id=user.id, user_name=user.username,
        user_role=user.role, module="kpi", entity_type="leg_kpi", entity_id=leg_id,
        detail=f"auto-sync leg {leg_id}",
    )
    return RedirectResponse(url="/kpi", status_code=303)


@router.post("/legs/{leg_id}")
async def kpi_upsert(
    leg_id: int,
    request: Request,
    palettes_carried: int = Form(0),
    tonnage_kg: str = Form("0"),
    distance_nm: str | None = Form(None),
    duration_hours: str | None = Form(None),
    avg_speed_kn: str | None = Form(None),
    on_time_raw: str | None = Form(None),
    occupancy_pct: str | None = Form(None),
    co2_avoided_kg: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "M")),
) -> RedirectResponse:
    if not await db.get(Leg, leg_id):
        raise HTTPException(status_code=404, detail="Leg not found")

    on_time = on_time_raw is not None

    existing: LegKPI | None = (
        await db.execute(select(LegKPI).where(LegKPI.leg_id == leg_id))
    ).scalar_one_or_none()

    if existing is None:
        kpi = LegKPI(
            leg_id=leg_id,
            palettes_carried=palettes_carried,
            tonnage_kg=Decimal(tonnage_kg) if tonnage_kg and tonnage_kg.strip() else Decimal(0),
            distance_nm=_to_decimal(distance_nm),
            duration_hours=_to_decimal(duration_hours),
            avg_speed_kn=_to_decimal(avg_speed_kn),
            on_time=on_time,
            occupancy_pct=_to_decimal(occupancy_pct),
            co2_avoided_kg=_to_decimal(co2_avoided_kg),
        )
        db.add(kpi)
    else:
        existing.palettes_carried = palettes_carried
        existing.tonnage_kg = Decimal(tonnage_kg) if tonnage_kg and tonnage_kg.strip() else Decimal(0)
        existing.distance_nm = _to_decimal(distance_nm)
        existing.duration_hours = _to_decimal(duration_hours)
        existing.avg_speed_kn = _to_decimal(avg_speed_kn)
        existing.on_time = on_time
        existing.occupancy_pct = _to_decimal(occupancy_pct)
        existing.co2_avoided_kg = _to_decimal(co2_avoided_kg)

    await db.flush()

    await activity_record(
        db,
        action="kpi_upsert",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="kpi",
        entity_type="leg_kpi",
        entity_id=leg_id,
        detail=f"leg {leg_id}",
    )

    is_htmx = request.headers.get("hx-request")
    if is_htmx:
        return RedirectResponse(url="/kpi", status_code=303, headers={"HX-Redirect": "/kpi"})
    return RedirectResponse(url="/kpi", status_code=303)
