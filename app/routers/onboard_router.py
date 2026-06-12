"""Onboard « 4 espaces » — espace bord du commandant (ARC-03).

Routes extraites de ``modules_router`` (fourre-tout V3.0) : landing,
navigation (noon reports + journal de quart), escale, cargo, crew.

PWA offline (ARC-01) : les POST noon-report / watch-log acceptent un
champ optionnel ``client_uuid`` (UUID généré côté navigateur par
``onboard-offline.js``) qui sert au dédoublonnage serveur lorsque la
file hors-ligne rejoue une soumission déjà reçue.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.noon_report import NoonReport
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.permissions import require_permission
from app.services import weather as wx
from app.services.activity import record as activity_record
from app.services.vessel_position import get_latest_position
from app.templating import templates

router = APIRouter(prefix="/onboard", tags=["onboard"])


@router.get("", response_class=HTMLResponse)
async def onboard_landing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    now = datetime.now(UTC)
    active_legs = list(
        (
            await db.execute(
                select(Leg)
                .where(Leg.atd.is_not(None))
                .where(Leg.ata.is_(None))
                .order_by(Leg.etd.desc())
            )
        )
        .scalars()
        .all()
    )
    next_etd = (
        await db.execute(select(Leg).where(Leg.etd > now).order_by(Leg.etd.asc()).limit(1))
    ).scalar_one_or_none()
    return templates.TemplateResponse(
        "staff/onboard/landing.html",
        {"request": request, "user": user, "active_legs": active_legs, "next_etd": next_etd},
    )


@router.get("/navigation", response_class=HTMLResponse)
async def onboard_navigation(
    request: Request,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    # Filtre RBAC : si l'user est rattaché à un navire (assigned_vessel_id),
    # on ne lui montre que les legs de ce navire.
    legs_stmt = select(Leg).order_by(Leg.etd.desc()).limit(30)
    if getattr(user, "assigned_vessel_id", None):
        legs_stmt = (
            select(Leg)
            .where(Leg.vessel_id == user.assigned_vessel_id)
            .order_by(Leg.etd.desc())
            .limit(30)
        )
    legs = list((await db.execute(legs_stmt)).scalars().all())
    selected = (await db.get(Leg, leg_id)) if leg_id else (legs[0] if legs else None)
    noon_reports = []
    watch_logs = []
    latest_position = None
    weather_now = None
    if selected:
        noon_reports = list(
            (
                await db.execute(
                    select(NoonReport)
                    .where(NoonReport.leg_id == selected.id)
                    .order_by(NoonReport.recorded_at.desc())
                    .limit(30)
                )
            )
            .scalars()
            .all()
        )
        watch_logs = list(
            (
                await db.execute(
                    select(WatchLog)
                    .where(WatchLog.leg_id == selected.id)
                    .order_by(WatchLog.watch_date.desc(), WatchLog.watch_period.desc())
                    .limit(30)
                )
            )
            .scalars()
            .all()
        )
        # Pré-remplissage GPS — dernière position satcom < 6h
        latest_position = await get_latest_position(db, selected.vessel_id)
        # Pré-remplissage météo au point GPS courant (vent + houle)
        if latest_position:
            try:
                weather_now = await wx.fetch_current(
                    latest_position.latitude,
                    latest_position.longitude,
                )
            except Exception:
                weather_now = None
    return templates.TemplateResponse(
        "staff/onboard/navigation.html",
        {
            "request": request,
            "user": user,
            "legs": legs,
            "leg": selected,
            "noon_reports": noon_reports,
            "watch_logs": watch_logs,
            "latest_position": latest_position,
            "weather_now": weather_now,
        },
    )


@router.post("/navigation/noon-report")
async def post_noon_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    client_uuid = _clean_client_uuid(f.get("client_uuid"))
    if client_uuid:
        existing = (
            await db.execute(select(NoonReport).where(NoonReport.client_uuid == client_uuid))
        ).scalar_one_or_none()
        if existing:
            # Rejeu file offline — déjà enregistré, on ne duplique pas.
            return RedirectResponse(
                url=f"/onboard/navigation?leg_id={existing.leg_id}", status_code=303
            )
    nr = NoonReport(
        leg_id=int(f["leg_id"]),
        recorded_at=datetime.now(UTC),
        latitude=float(f["latitude"]),
        longitude=float(f["longitude"]),
        sog_avg=_maybe_float(f.get("sog_avg")),
        cog_avg=_maybe_float(f.get("cog_avg")),
        wind_speed_kn=_maybe_float(f.get("wind_speed_kn")),
        wind_direction_deg=_maybe_float(f.get("wind_direction_deg")),
        sea_state_bf=_maybe_int(f.get("sea_state_bf")),
        visibility_nm=_maybe_float(f.get("visibility_nm")),
        barometric_hpa=_maybe_float(f.get("barometric_hpa")),
        fuel_consumed_24h_l=_maybe_float(f.get("fuel_consumed_24h_l")),
        distance_24h_nm=_maybe_float(f.get("distance_24h_nm")),
        rob_fuel_l=_maybe_float(f.get("rob_fuel_l")),
        remarks=f.get("remarks") or None,
        recorded_by_id=user.id,
        client_uuid=client_uuid,
    )
    db.add(nr)
    await db.flush()
    await activity_record(
        db,
        action="noon_report_create",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="noon_report",
        entity_id=nr.id,
    )
    return RedirectResponse(url=f"/onboard/navigation?leg_id={nr.leg_id}", status_code=303)


@router.post("/navigation/watch-log")
async def post_watch_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    client_uuid = _clean_client_uuid(f.get("client_uuid"))
    if client_uuid:
        existing = (
            await db.execute(select(WatchLog).where(WatchLog.client_uuid == client_uuid))
        ).scalar_one_or_none()
        if existing:
            # Rejeu file offline — déjà enregistré, on ne duplique pas.
            return RedirectResponse(
                url=f"/onboard/navigation?leg_id={existing.leg_id}", status_code=303
            )
    wl = WatchLog(
        leg_id=int(f["leg_id"]),
        watch_date=date.fromisoformat(f["watch_date"]),
        watch_period=f["watch_period"],
        officer_on_watch=f.get("officer_on_watch") or user.username,
        officer_id=user.id,
        entry=f["entry"],
        weather_summary=f.get("weather_summary") or None,
        client_uuid=client_uuid,
    )
    db.add(wl)
    await db.flush()
    return RedirectResponse(url=f"/onboard/navigation?leg_id={wl.leg_id}", status_code=303)


@router.get("/escale", response_class=HTMLResponse)
async def onboard_escale(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    at_quay = list(
        (await db.execute(select(Leg).where(Leg.ata.is_not(None)).where(Leg.atd.is_(None))))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/onboard/escale.html",
        {"request": request, "user": user, "at_quay": at_quay},
    )


@router.get("/cargo", response_class=HTMLResponse)
async def onboard_cargo(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(20))).scalars().all())
    return templates.TemplateResponse(
        "staff/onboard/cargo.html",
        {"request": request, "user": user, "legs": legs},
    )


@router.get("/crew", response_class=HTMLResponse)
async def onboard_crew(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(20))).scalars().all())
    visitors_today = list(
        (await db.execute(select(VisitorLog).order_by(VisitorLog.time_in.desc()).limit(20)))
        .scalars()
        .all()
    )
    checklists = list(
        (
            await db.execute(
                select(OnboardChecklist).order_by(OnboardChecklist.created_at.desc()).limit(20)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/onboard/crew.html",
        {
            "request": request,
            "user": user,
            "legs": legs,
            "visitors": visitors_today,
            "checklists": checklists,
        },
    )


@router.post("/crew/visitor")
async def post_visitor(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    v = VisitorLog(
        leg_id=int(f["leg_id"]),
        full_name=f["full_name"],
        company=f.get("company") or None,
        purpose=f.get("purpose") or None,
        id_document=f.get("id_document") or None,
        time_in=datetime.now(UTC),
        escorted_by=f.get("escorted_by") or None,
        notes=f.get("notes") or None,
    )
    db.add(v)
    await db.flush()
    return RedirectResponse(url="/onboard/crew", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                              Helpers
# ────────────────────────────────────────────────────────────────────


def _clean_client_uuid(v) -> str | None:
    """Valide le champ optionnel ``client_uuid`` (36 car max, str)."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v or len(v) > 36:
        return None
    return v


def _maybe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
