"""Onboard « 4 espaces » — espace bord du commandant (ARC-03).

Routes extraites de ``modules_router`` (fourre-tout V3.0) : landing,
navigation (noon reports + journal de quart), escale, cargo, crew.

PWA offline (ARC-01) : les POST noon-report / watch-log acceptent un
champ optionnel ``client_uuid`` (UUID généré côté navigateur par
``onboard-offline.js``) qui sert au dédoublonnage serveur lorsque la
file hors-ligne rejoue une soumission déjà reçue.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.bunker import BUNKER_STATUSES, BunkerOperation, BunkerTankAllocation
from app.models.leg import Leg
from app.models.noon_report import (
    NOON_ENGINES,
    NOON_HOLD_LOCATIONS,
    NOON_REPORT_TYPES,
    NOON_TIME_SLOTS,
    NOON_VESSEL_CONDITIONS,
    NoonReport,
    NoonReportEngine,
    NoonReportHold,
    NoonReportSail,
    NoonReportWeather,
)
from app.models.user import User
from app.models.vessel import Vessel
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.permissions import require_permission
from app.services import bunkering, mrv_sync, referential_env
from app.services import weather as wx
from app.services.activity import record as activity_record
from app.services.vessel_position import get_latest_position
from app.templating import templates

logger = logging.getLogger("onboard")

router = APIRouter(prefix="/onboard", tags=["onboard"])


# ────────────────────────────────────────────────────────────────────
#   FLX-11 — Check-lists ISM/ISPS prédéfinies
# ────────────────────────────────────────────────────────────────────
# Modèles standard de check-lists sûreté/sécurité. Le commandant en
# instancie une à partir d'un ``kind`` ; chaque libellé devient un item
# décochable (items_json = liste de {label, checked}).
CHECKLIST_TEMPLATES: dict[str, dict[str, object]] = {
    "ISPS_ARRIVAL": {
        "title": "Sûreté ISPS — arrivée au port",
        "items": [
            "Niveau de sûreté du port confirmé (MARSEC / ISPS niveau 1-2-3)",
            "Déclaration de sûreté (DoS) échangée si requise",
            "Coupée gardée / contrôle des accès en place",
            "Registre visiteurs ouvert et affiché à la coupée",
            "Éclairage de pont et zones sensibles vérifié",
            "Communication établie avec le PFSO (agent de sûreté portuaire)",
            "Ronde de sûreté initiale effectuée",
        ],
    },
    "ISM_DEPARTURE": {
        "title": "ISM — appareillage",
        "items": [
            "Briefing passerelle / machine effectué",
            "Appareil à gouverner testé (essais barre)",
            "Feux de navigation et de signalisation testés",
            "Moyens de communication interne testés",
            "Saisissage cargaison vérifié et consigné",
            "Échelles de coupée / passerelles rentrées et arrimées",
            "Tirant d'eau et stabilité relevés et consignés",
            "Plan de passage (passage plan) validé par le commandant",
        ],
    },
    "SAFETY_DRILL": {
        "title": "Exercice sécurité",
        "items": [
            "Alarme générale déclenchée et reconnue",
            "Rassemblement de l'équipage aux postes (muster)",
            "Appel nominal effectué",
            "Équipements de lutte (incendie / survie) mis en œuvre",
            "Mise à l'eau / parage d'une embarcation de sauvetage",
            "Débriefing et points d'amélioration consignés",
        ],
    },
}


def _checklist_items_for(kind: str) -> list[dict[str, object]]:
    """Construit la liste d'items (tous décochés) pour un ``kind`` connu."""
    tpl = CHECKLIST_TEMPLATES.get(kind)
    if not tpl:
        return []
    return [{"label": str(label), "checked": False} for label in tpl["items"]]


def _load_items(raw: str | None) -> list[dict[str, object]]:
    """Désérialise ``items_json`` de manière défensive (liste de dicts)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    items: list[dict[str, object]] = []
    for it in data:
        if isinstance(it, dict) and "label" in it:
            items.append({"label": str(it["label"]), "checked": bool(it.get("checked"))})
    return items


@router.get("", response_class=HTMLResponse)
async def onboard_landing(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    from app.models.port import Port
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

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

    # Sélection du leg pour tout le module opérations (persistée en cookie ;
    # les sous-pages onboard / escale en héritent).
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    selected_leg = f["selected_leg"]
    pol = pod = None
    vessel_status = None
    if selected_leg:
        pol = await db.get(Port, selected_leg.departure_port_id)
        pod = await db.get(Port, selected_leg.arrival_port_id)
        vessel_status = "en_mer" if (selected_leg.atd and not selected_leg.ata) else "a_quai"

    response = templates.TemplateResponse(
        "staff/onboard/landing.html",
        {
            "request": request,
            "user": user,
            "active_legs": active_legs,
            "next_etd": next_etd,
            "leg_filter_ctx": f,
            "selected_leg": selected_leg,
            "pol": pol,
            "pod": pod,
            "vessel_status": vessel_status,
        },
    )
    set_leg_filter_cookie(response, f)
    return response


def _beaufort_from_kn(kn: float | None) -> int | None:
    if kn is None:
        return None
    for i, th in enumerate([1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]):
        if kn < th:
            return i
    return 12


async def compute_noon_prefill(db, leg, weather_now, last_report) -> dict:
    """Valeurs pré-remplies (toutes modifiables) du noon report, dérivées des
    positions GPS, de la météo, du planning et des événements SOF (SOSP)."""
    from app.models.port import Port
    from app.models.sof_event import SofEvent
    from app.services.ports import haversine_nm
    from app.services.voyage_track import actual_distance_nm, positions_in_window

    now = datetime.now(UTC)
    pf: dict = {}
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    # Ports fixes pour le leg (depuis le planning).
    pf["previous_port"] = pol.locode if pol else ""
    pf["next_port"] = pod.locode if pod else ""
    # ETA annoncée = ETA de planification.
    pf["announced_eta"] = leg.eta.strftime("%Y-%m-%dT%H:%M") if leg.eta else ""
    # Beaufort dérivé du vent météo courant.
    bf = _beaufort_from_kn(getattr(weather_now, "wind_speed_kn", None) if weather_now else None)
    if bf is not None:
        pf["sea_state_bf"] = bf

    start = leg.atd or leg.etd
    all_pos = await positions_in_window(db, vessel_id=leg.vessel_id, start=start, end=now)
    last_pos = all_pos[-1] if all_pos else None

    if last_pos and pod and pod.latitude is not None and pod.longitude is not None:
        pf["distance_to_go_nm"] = round(
            haversine_nm(last_pos.latitude, last_pos.longitude, pod.latitude, pod.longitude), 1
        )

    win24 = await positions_in_window(
        db, vessel_id=leg.vessel_id, start=now - timedelta(hours=24), end=now
    )
    if len(win24) >= 2:
        pf["distance_24h_nm"] = round(actual_distance_nm(win24), 1)

    async def _segment(t0):
        hours = max((now - t0).total_seconds() / 3600.0, 0.0)
        win = await positions_in_window(db, vessel_id=leg.vessel_id, start=t0, end=now)
        dist = actual_distance_nm(win) if len(win) >= 2 else 0.0
        spd = round(dist / hours, 1) if hours > 0 and dist > 0 else None
        return round(hours, 1), round(dist, 1), spd

    if last_report and last_report.recorded_at:
        h, d, s = await _segment(last_report.recorded_at)
        pf["time_since_last_h"] = h
        pf["distance_since_last_nm"] = d
        if s is not None:
            pf["speed_since_last_kn"] = s

    sosp = (
        await db.execute(
            select(SofEvent)
            .where(SofEvent.leg_id == leg.id, SofEvent.event_type == "SOSP")
            .order_by(SofEvent.occurred_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if sosp and sosp.occurred_at:
        h, d, s = await _segment(sosp.occurred_at)
        pf["time_since_sosp_h"] = h
        pf["distance_since_sosp_nm"] = d
        if s is not None:
            pf["speed_since_sosp_kn"] = s

    # ── Météo 4 h : pour chaque créneau, l'observation historisée la plus
    # proche de l'horaire (tolérance 6 h). TWS ← vent, Dir mer ← houle/vent,
    # État mer ← Beaufort. AWA/AWS/vitesse restent en saisie manuelle. ──
    from app.models.noon_report import NOON_TIME_SLOTS
    from app.services.weather_history import observations_for_leg

    obs = await observations_for_leg(db, leg)
    slots: list[dict] = []
    for s in NOON_TIME_SLOTS:
        hh, mm = int(s[:2]), int(s[3:5])
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target > now:
            target -= timedelta(days=1)
        best, best_diff = None, None
        for o in obs:
            diff = abs((o.recorded_at - target).total_seconds())
            if best_diff is None or diff < best_diff:
                best, best_diff = o, diff
        entry: dict = {}
        if best is not None and best_diff is not None and best_diff <= 6 * 3600:
            if best.wind_speed_kn is not None:
                entry["tws"] = round(best.wind_speed_kn, 1)
            sd = (
                best.wave_direction_deg
                if best.wave_direction_deg is not None
                else best.wind_direction_deg
            )
            if sd is not None:
                entry["sd"] = round(sd)
            bf = _beaufort_from_kn(best.wind_speed_kn)
            if bf is not None:
                entry["ss"] = bf
        slots.append(entry)
    pf["weather_slots"] = slots

    return pf


@router.get("/navigation", response_class=HTMLResponse)
async def onboard_navigation(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    from app.services.leg_filter import build_leg_filter, set_leg_filter_cookie

    # Filtre RBAC : si l'user est rattaché à un navire, on force ce navire.
    if getattr(user, "assigned_vessel_id", None):
        assigned = await db.get(Vessel, user.assigned_vessel_id)
        if assigned is not None:
            vessel = assigned.code
    # Module de filtrage standard (hérite du leg choisi sur /onboard via cookie).
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id, request=request)
    legs = f["legs"]
    selected = f["selected_leg"] or (legs[0] if legs else None)
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
    # Pré-remplissage avancé du noon report (GPS / météo / planning / SOF).
    noon_prefill: dict = {}
    if selected:
        noon_prefill = await compute_noon_prefill(
            db, selected, weather_now, noon_reports[0] if noon_reports else None
        )

    # Le leg réellement affiché devient la sélection mémorisée.
    f["leg_id"] = selected.id if selected else f["leg_id"]
    f["selected_leg"] = selected
    response = templates.TemplateResponse(
        "staff/onboard/navigation.html",
        {
            "request": request,
            "user": user,
            "leg_filter_ctx": f,
            "legs": legs,
            "leg": selected,
            "noon_reports": noon_reports,
            "noon_prefill": noon_prefill,
            "weather_slots": noon_prefill.get("weather_slots", []),
            # ROB DO du dernier report du leg → base de chaîne pour le ROB auto
            # du nouveau report (ROB = ROB précédent − conso).
            "last_rob_do_t": (noon_reports[0].rob_do_t if noon_reports else None),
            "watch_logs": watch_logs,
            "latest_position": latest_position,
            "weather_now": weather_now,
            # Constantes du formulaire officiel TOWT (CFOTE_05) pour le rendu.
            "noon_engines": NOON_ENGINES,
            "noon_time_slots": NOON_TIME_SLOTS,
            "noon_report_types": NOON_REPORT_TYPES,
            "noon_vessel_conditions": NOON_VESSEL_CONDITIONS,
            "noon_hold_locations": NOON_HOLD_LOCATIONS,
        },
    )
    set_leg_filter_cookie(response, f)
    return response


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
        # Alignement formulaire officiel TOWT (CFOTE_05)
        report_type=(f.get("report_type") or None),
        previous_port=((f.get("previous_port") or "").strip().upper() or None),
        next_port=((f.get("next_port") or "").strip().upper() or None),
        vessel_condition=(f.get("vessel_condition") or None),
        deadweight_t=_maybe_float(f.get("deadweight_t")),
        draft_fwd_m=_maybe_float(f.get("draft_fwd_m")),
        draft_aft_m=_maybe_float(f.get("draft_aft_m")),
        trim_m=_maybe_float(f.get("trim_m")),
        time_since_last_h=_maybe_float(f.get("time_since_last_h")),
        distance_since_last_nm=_maybe_float(f.get("distance_since_last_nm")),
        speed_since_last_kn=_maybe_float(f.get("speed_since_last_kn")),
        time_since_sosp_h=_maybe_float(f.get("time_since_sosp_h")),
        distance_since_sosp_nm=_maybe_float(f.get("distance_since_sosp_nm")),
        speed_since_sosp_kn=_maybe_float(f.get("speed_since_sosp_kn")),
        distance_to_go_nm=_maybe_float(f.get("distance_to_go_nm")),
        announced_eta=_maybe_dt(f.get("announced_eta")),
        etb=_maybe_dt(f.get("etb")),
        eta_70_kt=_maybe_dt(f.get("eta_70_kt")),
        eta_75_kt=_maybe_dt(f.get("eta_75_kt")),
        eta_80_kt=_maybe_dt(f.get("eta_80_kt")),
        eta_85_kt=_maybe_dt(f.get("eta_85_kt")),
        eta_90_kt=_maybe_dt(f.get("eta_90_kt")),
        total_consumption_t=_maybe_float(f.get("total_consumption_t")),
        go_density=_maybe_float(f.get("go_density")),
        rob_do_t=_maybe_float(f.get("rob_do_t")),
        rob_uree_t=_maybe_float(f.get("rob_uree_t")),
        rob_fw_t=_maybe_float(f.get("rob_fw_t")),
        production_fw_t=_maybe_float(f.get("production_fw_t")),
        remarks=f.get("remarks") or None,
        recorded_by_id=user.id,
        client_uuid=client_uuid,
    )
    _attach_noon_children(nr, f)
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
    # FLX-03 — le noon report est la référence n°1 du MRV : génération
    # best-effort de l'événement MRV lié (idempotent ; le rejeu offline
    # dédoublonné plus haut ne repasse pas ici). Donnée réglementaire :
    # on logge fort mais on ne bloque jamais la saisie du bord.
    try:
        await mrv_sync.ensure_from_noon(db, nr)
    except Exception:
        logger.exception("MRV sync failed for noon report %s", nr.id)
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
    from app.services.leg_filter import leg_select_options

    legs = list((await db.execute(select(Leg).order_by(Leg.etd.desc()).limit(20))).scalars().all())
    leg_options = await leg_select_options(db)
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
            "leg_options": leg_options,
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
    """Registre visiteurs ISPS (FLX-11).

    Endpoint partagé : l'écran « Équipage » et l'espace « Conformité »
    postent ici. Le champ optionnel ``next`` permet de revenir sur la
    page d'origine (whitelist ``/onboard/...`` uniquement).
    """
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
    await activity_record(
        db,
        action="visitor_log_create",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="visitor_log",
        entity_id=v.id,
        entity_label=v.full_name,
    )
    return RedirectResponse(
        url=_safe_onboard_redirect(f.get("next"), "/onboard/crew"), status_code=303
    )


# ────────────────────────────────────────────────────────────────────
#   FLX-11 — Espace « Sécurité / Conformité » (check-lists + visiteurs)
# ────────────────────────────────────────────────────────────────────


@router.get("/compliance", response_class=HTMLResponse)
async def onboard_compliance(
    request: Request,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
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
    from app.services.leg_filter import leg_select_options

    leg_options = await leg_select_options(db, vessel_id=getattr(user, "assigned_vessel_id", None))

    checklists: list[OnboardChecklist] = []
    checklist_items: dict[int, list[dict[str, object]]] = {}
    visitors: list[VisitorLog] = []
    if selected:
        checklists = list(
            (
                await db.execute(
                    select(OnboardChecklist)
                    .where(OnboardChecklist.leg_id == selected.id)
                    .order_by(OnboardChecklist.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        checklist_items = {c.id: _load_items(c.items_json) for c in checklists}
        visitors = list(
            (
                await db.execute(
                    select(VisitorLog)
                    .where(VisitorLog.leg_id == selected.id)
                    .order_by(VisitorLog.time_in.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    # Modèles disponibles à l'instanciation (kind → titre).
    templates_choices = {k: v["title"] for k, v in CHECKLIST_TEMPLATES.items()}
    return templates.TemplateResponse(
        "staff/onboard/compliance.html",
        {
            "request": request,
            "user": user,
            "legs": legs,
            "leg_options": leg_options,
            "leg": selected,
            "checklists": checklists,
            "checklist_items": checklist_items,
            "visitors": visitors,
            "templates_choices": templates_choices,
        },
    )


@router.post("/compliance/checklist")
async def post_checklist(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    """Instancie une check-list à partir d'un ``kind`` prédéfini."""
    f = await request.form()
    leg_id = int(f["leg_id"])
    kind = str(f.get("kind") or "")
    tpl = CHECKLIST_TEMPLATES.get(kind)
    if not tpl:
        # kind inconnu — on ne crée rien, retour silencieux sur la page.
        return RedirectResponse(url=f"/onboard/compliance?leg_id={leg_id}", status_code=303)
    items = _checklist_items_for(kind)
    cl = OnboardChecklist(
        leg_id=leg_id,
        kind=kind,
        title=str(tpl["title"]),
        items_json=json.dumps(items, ensure_ascii=False),
    )
    db.add(cl)
    await db.flush()
    await activity_record(
        db,
        action="checklist_create",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="onboard_checklist",
        entity_id=cl.id,
        entity_label=cl.title,
    )
    return RedirectResponse(url=f"/onboard/compliance?leg_id={leg_id}", status_code=303)


@router.post("/compliance/checklist/{checklist_id}/item")
async def post_checklist_item(
    checklist_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    """Bascule l'état coché/décoché d'un item ; recalcule ``completed_at``."""
    f = await request.form()
    cl = await db.get(OnboardChecklist, checklist_id)
    if cl is None:
        return RedirectResponse(url="/onboard/compliance", status_code=303)
    items = _load_items(cl.items_json)
    idx = _maybe_int(f.get("item_index"))
    if idx is not None and 0 <= idx < len(items):
        items[idx]["checked"] = not bool(items[idx]["checked"])
        cl.items_json = json.dumps(items, ensure_ascii=False)
        # Complète quand tous les items sont cochés (et au moins un item).
        all_checked = bool(items) and all(bool(it["checked"]) for it in items)
        if all_checked and cl.completed_at is None:
            cl.completed_at = datetime.now(UTC)
            cl.signed_by_id = user.id
            cl.signed_by_name = getattr(user, "full_name", None) or user.username
        elif not all_checked and cl.completed_at is not None:
            cl.completed_at = None
            cl.signed_by_id = None
            cl.signed_by_name = None
        await db.flush()
        await activity_record(
            db,
            action="checklist_item_toggle",
            user_id=user.id,
            user_name=user.username,
            module="captain",
            entity_type="onboard_checklist",
            entity_id=cl.id,
            detail=f"item {idx} → {'✓' if items[idx]['checked'] else '✗'}",
        )
    return RedirectResponse(url=f"/onboard/compliance?leg_id={cl.leg_id}", status_code=303)


@router.post("/compliance/visitor")
async def post_compliance_visitor(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    """Ajoute une entrée au registre visiteurs ISPS depuis l'espace Conformité.

    Réutilise la même logique que ``post_visitor`` (endpoint historique
    ``/onboard/crew/visitor``) mais redirige vers l'espace Conformité.
    """
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
    await activity_record(
        db,
        action="visitor_log_create",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="visitor_log",
        entity_id=v.id,
        entity_label=v.full_name,
    )
    return RedirectResponse(url=f"/onboard/compliance?leg_id={v.leg_id}", status_code=303)


@router.post("/compliance/visitor/{visitor_id}/checkout")
async def post_visitor_checkout(
    visitor_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    """Renseigne ``time_out`` = maintenant pour clôturer une visite."""
    v = await db.get(VisitorLog, visitor_id)
    if v is None:
        return RedirectResponse(url="/onboard/compliance", status_code=303)
    if v.time_out is None:
        v.time_out = datetime.now(UTC)
        await db.flush()
        await activity_record(
            db,
            action="visitor_log_checkout",
            user_id=user.id,
            user_name=user.username,
            module="captain",
            entity_type="visitor_log",
            entity_id=v.id,
            entity_label=v.full_name,
        )
    return RedirectResponse(url=f"/onboard/compliance?leg_id={v.leg_id}", status_code=303)


# ────────────────────────────────────────────────────────────────────
#                              Helpers
# ────────────────────────────────────────────────────────────────────


def _safe_onboard_redirect(v, default: str) -> str:
    """Whitelist d'URL de retour : chemin relatif ``/onboard/...`` uniquement.

    Empêche un open-redirect via le champ caché ``next`` (on n'accepte ni
    URL absolue ``//host`` ni schéma externe).
    """
    if not isinstance(v, str):
        return default
    v = v.strip()
    if v.startswith("/onboard/") and not v.startswith("//"):
        return v
    return default


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


def _maybe_bool(v) -> bool:
    """Checkbox HTML : présent (« 1 »/« on ») → True, absent → False."""
    return str(v).strip().lower() in ("1", "true", "on", "yes") if v is not None else False


def _maybe_dt(v):
    """Parse un champ datetime-local (« YYYY-MM-DDTHH:MM ») en datetime UTC.

    Tolérant : renvoie None si vide ou non parsable. Pas de conversion de
    fuseau (saisie supposée en UTC bord, cohérente avec recorded_at).
    """
    if v is None or not str(v).strip():
        return None
    try:
        return datetime.fromisoformat(str(v).strip()).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _attach_noon_children(nr: NoonReport, f) -> None:
    """Construit les lignes filles (machine / météo / voilure) du noon report.

    Lit les champs indexés du formulaire officiel et n'ajoute une ligne que si
    elle porte au moins une valeur (pas de lignes vides). Les collections de
    relations sont cascade-persistées au flush du parent.
    """
    # Machine — un relevé par moteur (NOON_ENGINES).
    for i, name in enumerate(NOON_ENGINES):
        rh = _maybe_float(f.get(f"eng_rh_{i}"))
        do = _maybe_float(f.get(f"eng_do_{i}"))
        rhd = _maybe_float(f.get(f"eng_rhd_{i}"))
        rhd1 = _maybe_float(f.get(f"eng_rhd1_{i}"))
        if rh is None and do is None and rhd is None and rhd1 is None:
            continue
        nr.engines.append(
            NoonReportEngine(
                engine=name,
                running_hours_h=rh,
                do_consumption_t=do,
                running_hours_d=rhd,
                running_hours_d1=rhd1,
            )
        )
    # Météo — un relevé par créneau (NOON_TIME_SLOTS).
    for i, slot in enumerate(NOON_TIME_SLOTS):
        vals = (
            _maybe_float(f.get(f"w_tws_{i}")),
            _maybe_float(f.get(f"w_awa_{i}")),
            _maybe_float(f.get(f"w_aws_{i}")),
            _maybe_int(f.get(f"w_ss_{i}")),
            _maybe_float(f.get(f"w_sd_{i}")),
            _maybe_float(f.get(f"w_spd_{i}")),
        )
        if all(v is None for v in vals):
            continue
        nr.weather_rows.append(
            NoonReportWeather(
                slot_time=slot,
                tws_kn=vals[0],
                awa_deg=vals[1],
                aws_kn=vals[2],
                sea_state=vals[3],
                sea_direction_deg=vals[4],
                ship_speed_kn=vals[5],
            )
        )
    # Voilure — un relevé par créneau.
    for i, slot in enumerate(NOON_TIME_SLOTS):
        j0 = _maybe_bool(f.get(f"s_j0_{i}"))
        fj1 = _maybe_bool(f.get(f"s_fwdj1_{i}"))
        fms = _maybe_bool(f.get(f"s_fwdms_{i}"))
        aj1 = _maybe_bool(f.get(f"s_aftj1_{i}"))
        ams = _maybe_bool(f.get(f"s_aftms_{i}"))
        boost = _maybe_float(f.get(f"s_boost_{i}"))
        ps = _maybe_float(f.get(f"s_psload_{i}"))
        sb = _maybe_float(f.get(f"s_sbload_{i}"))
        if not (j0 or fj1 or fms or aj1 or ams) and boost is None and ps is None and sb is None:
            continue
        nr.sail_rows.append(
            NoonReportSail(
                slot_time=slot,
                j0=j0,
                fwd_j1=fj1,
                fwd_ms=fms,
                aft_j1=aj1,
                aft_ms=ams,
                sail_boost=boost,
                me_ps_load_pct=ps,
                me_sb_load_pct=sb,
            )
        )
    # Cales — température (°C) & humidité relative (%) à minuit/midi par cale.
    for i, location in enumerate(NOON_HOLD_LOCATIONS):
        tmn = _maybe_float(f.get(f"hold_tmn_{i}"))
        hmn = _maybe_float(f.get(f"hold_hmn_{i}"))
        tmd = _maybe_float(f.get(f"hold_tmd_{i}"))
        hmd = _maybe_float(f.get(f"hold_hmd_{i}"))
        if tmn is None and hmn is None and tmd is None and hmd is None:
            continue
        nr.hold_rows.append(
            NoonReportHold(
                location=location,
                temp_midnight_c=tmn,
                humidity_midnight_pct=hmn,
                temp_midday_c=tmd,
                humidity_midday_pct=hmd,
            )
        )


# ────────────────────────────────────────────────────────────────────
#   LOT 6 — Soutage (Bunker Report / BDN) : /onboard/bunkering
# ────────────────────────────────────────────────────────────────────
# Perm ``captain:M`` sur TOUT l'espace (y compris consultation) — les
# soutages sont une donnée réglementaire, pas un simple journal de bord.


def _int_or_400(raw: str | None, field: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} invalide") from exc


async def _resolve_bunkering_vessel(
    db: AsyncSession, user, vessel_id_param: int | None
) -> Vessel | None:
    """Résout le navire courant des écrans de soutage bord.

    Priorité : ``vessel_id`` explicite (query/form) > navire assigné à
    l'utilisateur (cas courant, 1 commandant = 1 navire) > premier navire
    actif (repli pour les rôles multi-navires : opération/technique/manager).
    """
    if vessel_id_param:
        v = await db.get(Vessel, vessel_id_param)
        if v is not None:
            return v
    assigned_id = getattr(user, "assigned_vessel_id", None)
    if assigned_id:
        v = await db.get(Vessel, assigned_id)
        if v is not None:
            return v
    return (
        await db.execute(
            select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code).limit(1)
        )
    ).scalar_one_or_none()


async def _bunker_allocations(db: AsyncSession, bunker_id: int) -> list[BunkerTankAllocation]:
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
async def onboard_bunkering_index(
    request: Request,
    vessel_id: int | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """LOT 6 — liste des soutages (BDN) du navire courant."""
    vessel = await _resolve_bunkering_vessel(db, user, vessel_id)
    vessels = list(
        (
            await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code))
        )
        .scalars()
        .all()
    )
    bunkers: list[BunkerOperation] = []
    if vessel is not None:
        stmt = select(BunkerOperation).where(BunkerOperation.vessel_id == vessel.id)
        if status:
            stmt = stmt.where(BunkerOperation.status == status)
        stmt = stmt.order_by(BunkerOperation.delivery_datetime_utc.desc())
        bunkers = list((await db.execute(stmt)).scalars().all())
    leg_map: dict[int, Leg] = {}
    for b in bunkers:
        if b.leg_id and b.leg_id not in leg_map:
            leg = await db.get(Leg, b.leg_id)
            if leg:
                leg_map[b.leg_id] = leg
    return templates.TemplateResponse(
        "staff/onboard/bunkering_index.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "vessels": vessels,
            "bunkers": bunkers,
            "leg_map": leg_map,
            "filter_status": status,
            "bunker_statuses": BUNKER_STATUSES,
        },
    )


@router.get("/bunkering/new", response_class=HTMLResponse)
async def onboard_bunkering_new_form(
    request: Request,
    vessel_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """LOT 6 — formulaire de saisie d'un nouveau soutage (en-tête + cuves)."""
    vessel = await _resolve_bunkering_vessel(db, user, vessel_id)
    vessels = list(
        (
            await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code))
        )
        .scalars()
        .all()
    )
    tanks = await referential_env.get_vessel_tanks(db, vessel.id) if vessel else []
    from app.services.leg_filter import leg_select_options

    leg_options = await leg_select_options(db, vessel_id=vessel.id) if vessel else []
    return templates.TemplateResponse(
        "staff/onboard/bunkering_form.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "vessels": vessels,
            "tanks": tanks,
            "leg_options": leg_options,
            "bunker": None,
            "allocations_by_tank": {},
            # Un commandant rattaché à un seul navire n'a pas besoin du sélecteur
            # (cas courant) ; les rôles multi-navires (opération/technique/manager)
            # peuvent choisir explicitement.
            "can_pick_vessel": getattr(user, "assigned_vessel_id", None) is None,
        },
    )


@router.post("/bunkering/new")
async def onboard_bunkering_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """LOT 6 — création d'un brouillon de soutage (en-tête + allocations cuves)."""
    form = dict(await request.form())
    vessel = await _resolve_bunkering_vessel(db, user, _int_or_400(form.get("vessel_id"), "vessel_id"))
    if vessel is None:
        raise HTTPException(status_code=400, detail="Navire introuvable.")

    delivery_raw = (form.get("delivery_datetime_utc") or "").strip()
    if not delivery_raw:
        raise HTTPException(status_code=400, detail="Date de livraison obligatoire.")
    try:
        delivery_dt = datetime.fromisoformat(delivery_raw).replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date de livraison invalide.") from exc

    manual_leg_id = _int_or_400(form.get("leg_id"), "leg_id")
    if manual_leg_id is not None:
        leg = await db.get(Leg, manual_leg_id)
        if leg is None or leg.vessel_id != vessel.id:
            raise HTTPException(status_code=400, detail="Leg invalide pour ce navire.")

    tanks = await referential_env.get_vessel_tanks(db, vessel.id)
    try:
        density = bunkering.parse_decimal(
            form.get("density_15c_t_m3"), "density_15c_t_m3", required=True
        )
        mass_t = bunkering.parse_decimal(form.get("mass_t"), "mass_t", required=True)
        allocations = bunkering.parse_allocation_rows(form, tanks, default_density=density)
        bunker = await bunkering.create_draft(
            db,
            vessel=vessel,
            author_user_id=user.id,
            bdn_number=form.get("bdn_number") or "",
            port_locode=form.get("port_locode") or "",
            delivery_datetime_utc=delivery_dt,
            mass_t=mass_t,
            density_15c_t_m3=density,
            fuel_type=form.get("fuel_type") or "MDO",
            sulfur_content_pct=bunkering.parse_decimal(
                form.get("sulfur_content_pct"), "sulfur_content_pct"
            ),
            viscosity_cst=bunkering.parse_decimal(form.get("viscosity_cst"), "viscosity_cst"),
            water_content_pct=bunkering.parse_decimal(
                form.get("water_content_pct"), "water_content_pct"
            ),
            lower_heating_value=bunkering.parse_decimal(
                form.get("lower_heating_value"), "lower_heating_value"
            ),
            higher_heating_value=bunkering.parse_decimal(
                form.get("higher_heating_value"), "higher_heating_value"
            ),
            ef_ttw_co2=bunkering.parse_decimal(form.get("ef_ttw_co2"), "ef_ttw_co2"),
            supplier_name=form.get("supplier_name"),
            leg_id=manual_leg_id,
            allocations=allocations,
        )
    except bunkering.BunkerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await activity_record(
        db,
        action="bunker_create",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="bunker_operation",
        entity_id=bunker.id,
        entity_label=bunker.bdn_number,
    )
    return RedirectResponse(url=f"/onboard/bunkering/{bunker.id}", status_code=303)


@router.get("/bunkering/{bunker_id}", response_class=HTMLResponse)
async def onboard_bunkering_detail(
    bunker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """LOT 6 — détail d'un soutage : en-tête, cuves, contrôles structurels."""
    bunker = await db.get(BunkerOperation, bunker_id)
    if bunker is None:
        raise HTTPException(status_code=404)
    vessel = await db.get(Vessel, bunker.vessel_id)
    leg = await db.get(Leg, bunker.leg_id) if bunker.leg_id else None
    allocations = await _bunker_allocations(db, bunker.id)
    tanks_by_id = await bunkering.vessel_tanks_by_id(db, bunker.vessel_id)
    checks = await bunkering.evaluate_bunker(db, bunker, allocations, tanks_by_id)
    is_author = bunker.author_user_id is None or bunker.author_user_id == user.id
    can_edit = bunker.status == "brouillon" and is_author
    validated_by_name = None
    if bunker.validated_master_by:
        validator = await db.get(User, bunker.validated_master_by)
        validated_by_name = (validator.full_name or validator.username) if validator else None
    return templates.TemplateResponse(
        "staff/onboard/bunkering_detail.html",
        {
            "request": request,
            "user": user,
            "bunker": bunker,
            "vessel": vessel,
            "leg": leg,
            "allocations": allocations,
            "tanks_by_id": tanks_by_id,
            "checks": checks,
            "can_edit": can_edit,
            "validated_by_name": validated_by_name,
            "audience": "onboard",
        },
    )


@router.get("/bunkering/{bunker_id}/edit", response_class=HTMLResponse)
async def onboard_bunkering_edit_form(
    bunker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """LOT 6 — formulaire d'édition d'un brouillon — réservé à son auteur."""
    bunker = await db.get(BunkerOperation, bunker_id)
    if bunker is None:
        raise HTTPException(status_code=404)
    if bunker.status != "brouillon":
        raise HTTPException(status_code=409, detail="Ce soutage est déjà validé Master.")
    if bunker.author_user_id is not None and bunker.author_user_id != user.id:
        raise HTTPException(
            status_code=403, detail="Seul l'auteur du brouillon peut le modifier."
        )
    vessel = await db.get(Vessel, bunker.vessel_id)
    tanks = await referential_env.get_vessel_tanks(db, bunker.vessel_id)
    allocations = await _bunker_allocations(db, bunker.id)
    allocations_by_tank = {a.tank_id: a for a in allocations}
    from app.services.leg_filter import leg_select_options

    leg_options = await leg_select_options(db, vessel_id=bunker.vessel_id)
    return templates.TemplateResponse(
        "staff/onboard/bunkering_form.html",
        {
            "request": request,
            "user": user,
            "vessel": vessel,
            "vessels": [vessel] if vessel else [],
            "tanks": tanks,
            "leg_options": leg_options,
            "bunker": bunker,
            "allocations_by_tank": allocations_by_tank,
            "can_pick_vessel": False,
        },
    )


@router.post("/bunkering/{bunker_id}/edit")
async def onboard_bunkering_edit_post(
    bunker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """LOT 6 — sauvegarde d'un brouillon — garde auteur-seul (service)."""
    bunker = await db.get(BunkerOperation, bunker_id)
    if bunker is None:
        raise HTTPException(status_code=404)
    form = dict(await request.form())
    vessel = await db.get(Vessel, bunker.vessel_id)
    tanks = await referential_env.get_vessel_tanks(db, bunker.vessel_id)

    manual_leg_raw = (form.pop("leg_id", "") or "").strip()
    manual_leg_id: int | None = None
    auto_leg_vessel = None
    if manual_leg_raw:
        manual_leg_id = _int_or_400(manual_leg_raw, "leg_id")
        leg = await db.get(Leg, manual_leg_id)
        if leg is None or leg.vessel_id != bunker.vessel_id:
            raise HTTPException(status_code=400, detail="Leg invalide pour ce navire.")
    else:
        auto_leg_vessel = vessel

    try:
        density_raw = (form.get("density_15c_t_m3") or "").strip()
        default_density = (
            bunkering.parse_decimal(density_raw, "density_15c_t_m3")
            if density_raw
            else bunker.density_15c_t_m3
        )
        allocations = bunkering.parse_allocation_rows(form, tanks, default_density=default_density)
        await bunkering.update_draft(
            db,
            bunker,
            user_id=user.id,
            form=form,
            allocations=allocations,
            manual_leg_id=manual_leg_id,
            auto_leg_vessel=auto_leg_vessel,
        )
    except bunkering.AuthorOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except bunkering.BunkerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await activity_record(
        db,
        action="bunker_update",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="bunker_operation",
        entity_id=bunker.id,
        entity_label=bunker.bdn_number,
    )
    return RedirectResponse(url=f"/onboard/bunkering/{bunker.id}", status_code=303)


@router.post("/bunkering/{bunker_id}/validate")
async def onboard_bunkering_validate(
    bunker_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """LOT 6 — validation Master : verrouille le soutage côté bord."""
    bunker = await db.get(BunkerOperation, bunker_id)
    if bunker is None:
        raise HTTPException(status_code=404)
    try:
        await bunkering.validate_master(db, bunker, user)
    except bunkering.BunkerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await activity_record(
        db,
        action="bunker_validate_master",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="bunker_operation",
        entity_id=bunker.id,
        entity_label=bunker.bdn_number,
    )
    return RedirectResponse(url=f"/onboard/bunkering/{bunker.id}", status_code=303)
