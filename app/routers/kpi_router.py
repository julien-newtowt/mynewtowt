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
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> HTMLResponse:
    # Module de filtrage standard navire × année × leg (cf. _leg_filter.html).
    from app.services.kpi import compute_for_leg
    from app.services.leg_filter import build_leg_filter

    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id)
    legs = f["legs"]
    all_legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()))).scalars().all())

    # ── Auto-alimentation : chaque leg du périmètre porte un KPI calculé ─────
    # automatiquement (tonnage, distance, CO₂ évité + émis, intensités). Les
    # KPI marqués « saisie manuelle » (is_manual) ne sont jamais réécrits.
    existing = {k.leg_id: k for k in (await db.execute(select(LegKPI))).scalars().all()}
    for leg in legs:
        k = existing.get(leg.id)
        if k is None or not k.is_manual:
            await compute_for_leg(db, leg)

    leg_ids = [leg.id for leg in legs]
    kpis = [
        k
        for k in (await db.execute(select(LegKPI).order_by(LegKPI.id.desc()))).scalars().all()
        if k.leg_id in leg_ids
    ]
    leg_map = {leg.id: leg for leg in legs}

    # Aggregates (sur le périmètre filtré)
    total_tonnage_t = sum((k.tonnage_kg or Decimal(0)) for k in kpis) / Decimal(1000)
    total_co2_avoided_kg = sum((k.co2_avoided_kg or Decimal(0)) for k in kpis)
    total_co2_emitted_t = sum((k.co2_emitted_kg or Decimal(0)) for k in kpis) / Decimal(1000)
    total_do_t = sum((k.do_consumed_t or Decimal(0)) for k in kpis)

    # FIN-03 — NOx / SOx évités par leg (cargo × distance × Δfacteur), + totaux.
    from app.services.emissions import estimate_avoided, get_emission_factors

    em_factors = await get_emission_factors(db)
    emissions_by_leg: dict[int, object] = {}
    total_nox_avoided_kg = Decimal(0)
    total_sox_avoided_kg = Decimal(0)
    for k in kpis:
        res = estimate_avoided(
            cargo_t=(k.tonnage_kg or Decimal(0)) / Decimal(1000),
            distance_nm=k.distance_nm,
            factors=em_factors,
        )
        emissions_by_leg[k.leg_id] = res
        total_nox_avoided_kg += res.nox_avoided_kg
        total_sox_avoided_kg += res.sox_avoided_kg

    if kpis:
        on_time_count = sum(1 for k in kpis if k.on_time)
        on_time_pct = on_time_count / len(kpis) * 100.0
    else:
        on_time_pct = 0.0

    from app.services.leg_filter import leg_select_options

    leg_options = await leg_select_options(db)
    return templates.TemplateResponse(
        "staff/kpi/index.html",
        {
            "request": request,
            "user": user,
            "leg_filter_ctx": f,
            "kpis": kpis,
            "legs": all_legs,
            "leg_options": leg_options,
            "leg_map": leg_map,
            "total_tonnage_t": total_tonnage_t,
            "total_co2_avoided_kg": total_co2_avoided_kg,
            "total_co2_emitted_t": total_co2_emitted_t,
            "total_do_t": total_do_t,
            "on_time_pct": on_time_pct,
            "emissions_by_leg": emissions_by_leg,
            "total_nox_avoided_kg": total_nox_avoided_kg,
            "total_sox_avoided_kg": total_sox_avoided_kg,
        },
    )


@router.get("/export.csv")
async def kpi_export_csv(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("kpi", "C")),
) -> StreamingResponse:
    kpis = list((await db.execute(select(LegKPI).order_by(LegKPI.id.asc()))).scalars().all())

    # Build leg_id → leg_code mapping
    leg_ids = {k.leg_id for k in kpis}
    leg_map: dict[int, str] = {}
    for lid in leg_ids:
        leg = await db.get(Leg, lid)
        if leg:
            leg_map[lid] = leg.leg_code

    from app.services.emissions import estimate_avoided, get_emission_factors

    em_factors = await get_emission_factors(db)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "leg_code",
            "palettes_carried",
            "tonnage_kg",
            "distance_nm",
            "duration_hours",
            "avg_speed_kn",
            "on_time",
            "occupancy_pct",
            "co2_avoided_kg",
            "nox_avoided_kg",
            "sox_avoided_kg",
        ]
    )
    for k in kpis:
        em = estimate_avoided(
            cargo_t=(k.tonnage_kg or Decimal(0)) / Decimal(1000),
            distance_nm=k.distance_nm,
            factors=em_factors,
        )
        writer.writerow(
            [
                leg_map.get(k.leg_id, ""),
                k.palettes_carried,
                k.tonnage_kg,
                k.distance_nm,
                k.duration_hours,
                k.avg_speed_kn,
                "1" if k.on_time else "0",
                k.occupancy_pct,
                k.co2_avoided_kg,
                em.nox_avoided_kg,
                em.sox_avoided_kg,
            ]
        )

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
        db,
        action="kpi_sync",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="kpi",
        entity_type="leg_kpi",
        entity_id=leg_id,
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
            is_manual=True,
        )
        db.add(kpi)
    else:
        existing.palettes_carried = palettes_carried
        existing.tonnage_kg = (
            Decimal(tonnage_kg) if tonnage_kg and tonnage_kg.strip() else Decimal(0)
        )
        existing.distance_nm = _to_decimal(distance_nm)
        existing.duration_hours = _to_decimal(duration_hours)
        existing.avg_speed_kn = _to_decimal(avg_speed_kn)
        existing.on_time = on_time
        existing.occupancy_pct = _to_decimal(occupancy_pct)
        existing.co2_avoided_kg = _to_decimal(co2_avoided_kg)
        # Saisie manuelle : on verrouille contre l'auto-alimentation.
        existing.is_manual = True

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
