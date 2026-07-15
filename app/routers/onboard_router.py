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
import secrets as _secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.bunker import BUNKER_STATUSES, BunkerOperation, BunkerTankAllocation
from app.models.leg import Leg
from app.models.nav_event import (
    EVENT_TYPES,
    HOLD_PERIODS,
    HOLD_ZONES,
    HOLD_ZONES_WITHOUT_RH,
    NAV_TIME_SLOTS,
    POSITION_SOURCES,
    VESSEL_CONDITIONS,
    CutoffEvent,
    NavEvent,
    NavEventEngineReading,
    NavEventHoldReading,
    NavEventRobByFuel,
    NavEventSailReading,
    NavEventWeatherReading,
    NoonEvent,
)
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
from app.services import (
    bunkering,
    cutoff_reminders,
    draft_reminders,
    event_capture,
    feature_flags,
    referential_env,
)
from app.services import weather as wx
from app.services.activity import record as activity_record
from app.services.vessel_position import get_latest_position
from app.templating import templates
from app.utils.timezones import TIMEZONE_CHOICES

logger = logging.getLogger("onboard")

router = APIRouter(prefix="/onboard", tags=["onboard"])
# LOT 4 — router SANS préfixe pour l'endpoint cron ``/api/mrv/draft-reminders``
# (l'endpoint ne peut pas vivre sous le préfixe ``/onboard`` du router bord ;
# même convention que ``navigation_router.api_router`` / ``tickets_router``).
api_router = APIRouter(tags=["mrv-drafts"])


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

    # LOT 4 — brouillons d'événements en cours de l'utilisateur (bloc landing).
    my_drafts = await _my_event_drafts(db, user)

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
            "my_drafts": my_drafts,
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
    # LOT 14 — bascule : sur un navire à capture v2 active, l'ancien formulaire
    # noon est gelé (remplacé par un renvoi vers la déclaration d'événements).
    # Le journal de quart et les listes restent affichés (double garde côté POST).
    selected_vessel = (
        await db.get(Vessel, selected.vessel_id) if (selected and selected.vessel_id) else None
    )
    capture_v2 = await feature_flags.capture_v2_enabled(db, selected_vessel)
    response = templates.TemplateResponse(
        "staff/onboard/navigation.html",
        {
            "request": request,
            "user": user,
            "leg_filter_ctx": f,
            "legs": legs,
            "leg": selected,
            "capture_v2": capture_v2,
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


@router.get("/navigation/noon-report")
async def get_noon_report_form(
    request: Request,
    vessel_id: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> RedirectResponse:
    """LOT 14 — point d'entrée de l'ancien formulaire noon.

    Navire à capture v2 active → renvoi 303 vers la déclaration d'événements
    (``/onboard/events/new/noon``, message flash via ``?notice=capture_v2``).
    Navire en opt-out (double-run) → renvoi vers ``/onboard/navigation`` qui
    porte encore le formulaire noon legacy.
    """
    leg, vessel = await _resolve_leg_and_vessel(db, user, vessel_id, leg_id)
    if await feature_flags.capture_v2_enabled(db, vessel):
        suffix = f"?notice=capture_v2&leg_id={leg.id}" if leg else "?notice=capture_v2"
        return RedirectResponse(url=f"/onboard/events/new/noon{suffix}", status_code=303)
    dest = f"/onboard/navigation?leg_id={leg.id}" if leg else "/onboard/navigation"
    return RedirectResponse(url=dest, status_code=303)


@router.post("/navigation/noon-report")
async def post_noon_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    f = await request.form()
    # LOT 14 — garde de bascule (AVANT le dédoublonnage) : sur un navire à
    # capture v2 active, l'ancien noon — saisie directe ET rejeu de la file
    # offline — est refusé 409 avec un message explicite (jamais de perte
    # silencieuse : le client offline voit l'échec au lieu d'un faux succès).
    _leg_id = _int_or_400(f.get("leg_id"), "leg_id")
    _leg = await db.get(Leg, _leg_id) if _leg_id is not None else None
    _vessel = await db.get(Vessel, _leg.vessel_id) if (_leg and _leg.vessel_id) else None
    if await feature_flags.capture_v2_enabled(db, _vessel):
        raise HTTPException(
            status_code=409,
            detail=(
                "capture v2 active — utilisez la déclaration d'événements "
                "(/onboard/events/new/noon)."
            ),
        )
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
    # LOT 14 — la synchro noon→MRVEvent (``mrv_sync``) est éteinte : les navires
    # en double-run (capture v2 OFF) écrivent encore ``noon_reports`` (audit,
    # signature, fallback ledger ``legacy_noon``) mais ne génèrent plus de
    # ``mrv_events`` (module archivé). La capture v2 est la voie unique.
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
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)))
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
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)))
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
    vessel = await _resolve_bunkering_vessel(
        db, user, _int_or_400(form.get("vessel_id"), "vessel_id")
    )
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
        raise HTTPException(status_code=403, detail="Seul l'auteur du brouillon peut le modifier.")
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


# ══════════════════════════════════════════════════════════════════════════
# === LOT 4 — capture d'événements ===
# ══════════════════════════════════════════════════════════════════════════
# Saisie bord DÉCLARATIVE des événements MRV (Noon / Departure / Arrival /
# Begin|End Anchoring). Perm ``captain:M`` sur TOUT l'espace (donnée
# réglementaire). Cycle Brouillon → Finalisé (via ``services.event_capture``,
# lot 3) : préremplissage Thalos/planning/SOF **modifiable** (position manuelle
# ⇒ justification R05), autosave, reprise réservée à l'auteur, parité offline
# NoonEvent (mécanisme générique ``data-offline-queue`` + client_uuid, lot 3
# dédoublonne côté serveur).

# 7 paliers ETA du Noon (7,0 → 10,0 kt) — clés du JSON ``eta_7_to_10kt``.
NAV_ETA_PALIERS: tuple[str, ...] = ("7.0", "7.5", "8.0", "8.5", "9.0", "9.5", "10.0")


def _dec(v) -> Decimal | None:
    """Parse tolérant d'un champ numérique en Decimal (None si vide/invalide)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _clean_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _dt_local(v) -> datetime | None:
    """Parse un ``datetime-local`` en datetime NAÏF (heure murale du fuseau saisi)."""
    if v is None or not str(v).strip():
        return None
    try:
        return datetime.fromisoformat(str(v).strip())
    except (TypeError, ValueError):
        return None


def _event_sort_key(e: NavEvent) -> datetime:
    """Clé de tri chronologique (UTC calculé sinon création), robuste naïf/aware."""
    dt = e.datetime_utc or e.created_at
    if dt is None:
        return datetime.max.replace(tzinfo=None)
    return dt if dt.tzinfo is None else dt.astimezone(UTC).replace(tzinfo=None)


def _build_eta_paliers(f) -> dict | None:
    """Assemble le JSON des 7 paliers ETA depuis ``eta_palier_{i}`` (ISO, non vides)."""
    out: dict[str, str] = {}
    for i, spd in enumerate(NAV_ETA_PALIERS):
        dt = _maybe_dt(f.get(f"eta_palier_{i}"))
        if dt is not None:
            out[spd] = dt.isoformat()
    return out or None


def _build_event_payload(event_type: str, f) -> dict:
    """Construit le payload de champs scalaires selon le type (champs autorisés
    filtrés ensuite par ``event_capture._apply_payload``)."""
    src = f.get("position_source")
    payload: dict = {
        "datetime_local": _dt_local(f.get("datetime_local")),
        "timezone": _clean_str(f.get("timezone")),
        "lat_decimal": _dec(f.get("lat_decimal")),
        "lon_decimal": _dec(f.get("lon_decimal")),
        "position_source": (src if src in POSITION_SOURCES else None),
        "position_justification": _clean_str(f.get("position_justification")),
        "cargo_mrv_t": _dec(f.get("cargo_mrv_t")),
    }
    if event_type == "noon":
        payload.update(
            {
                "time_from_sosp_h": _dec(f.get("time_from_sosp_h")),
                "distance_from_sosp_nm": _dec(f.get("distance_from_sosp_nm")),
                "distance_to_go_nm": _dec(f.get("distance_to_go_nm")),
                "announced_eta": _maybe_dt(f.get("announced_eta")),
                "etb": _maybe_dt(f.get("etb")),
                "eta_7_to_10kt": _build_eta_paliers(f),
                "comments": _clean_str(f.get("comments")),
            }
        )
    elif event_type in ("departure", "arrival"):
        cond = f.get("vessel_condition")
        payload.update(
            {
                "draft_fwd_m": _dec(f.get("draft_fwd_m")),
                "draft_aft_m": _dec(f.get("draft_aft_m")),
                "trim_m": _dec(f.get("trim_m")),
                "vessel_condition": (cond if cond in VESSEL_CONDITIONS else None),
                "rob_t": _dec(f.get("rob_t")),
            }
        )
        if event_type == "departure":
            payload["cargo_bl_t"] = _dec(f.get("cargo_bl_t"))
            payload["etd_confirmed"] = _maybe_dt(f.get("etd_confirmed"))
        else:
            payload["eta_announced"] = _maybe_dt(f.get("eta_announced"))
            payload["etb"] = _maybe_dt(f.get("etb"))
    elif event_type in ("anchoring_begin", "anchoring_end"):
        payload["sequence_no"] = _maybe_int(f.get("sequence_no"))
        if event_type == "anchoring_begin":
            payload["reason"] = _clean_str(f.get("reason"))
        else:
            payload["paired_event_id"] = _maybe_int(f.get("paired_event_id"))
    return payload


async def _sync_event_readings(db: AsyncSession, event: NavEvent, f, engines) -> None:
    """Reconstruit les relevés fins d'un événement depuis le formulaire.

    Relevés machine (1 ligne/moteur du référentiel) : rattachables à **tout**
    type d'événement (``NavEvent.engine_readings``, cf. modèle) — reconstruits
    ici quel que soit le type. Météo/voilure/cales (créneaux 4 h, 2 périodes ×
    zones) restent propres au ``NoonEvent`` (champs scalaires du Noon).

    Les collections (``lazy="selectin"``) sont d'abord chargées dans le
    contexte async (``db.refresh``) : sur un objet fraîchement créé ou repris
    depuis l'identity-map, y accéder en contexte synchrone déclencherait une
    IO paresseuse (``MissingGreenlet``)."""
    await db.refresh(event, ["engine_readings"])
    event.engine_readings.clear()
    for eng in engines:
        hours = _dec(f.get(f"eng_hours_{eng.id}"))
        fuel = _dec(f.get(f"eng_fuel_{eng.id}"))
        reset = _maybe_bool(f.get(f"eng_reset_{eng.id}"))
        if hours is None and fuel is None and not reset:
            continue
        event.engine_readings.append(
            NavEventEngineReading(
                engine_id=eng.id,
                running_hours_counter_h=hours,
                fuel_counter_l=fuel,
                is_counter_reset=reset,
            )
        )

    if isinstance(event, CutoffEvent):
        await db.refresh(event, ["rob_by_fuel_readings"])
        event.rob_by_fuel_readings.clear()
        for i in range(len(_CUTOFF_FUEL_TYPES)):
            fuel_type = _clean_str(f.get(f"robfuel_type_{i}"))
            rob = _dec(f.get(f"robfuel_val_{i}"))
            if not fuel_type or rob is None:
                continue
            event.rob_by_fuel_readings.append(NavEventRobByFuel(fuel_type=fuel_type, rob_t=rob))

    if not isinstance(event, NoonEvent):
        return

    await db.refresh(event, ["weather_readings", "sail_readings", "hold_readings"])
    event.weather_readings.clear()
    for i, slot in enumerate(NAV_TIME_SLOTS):
        vals = (
            _dec(f.get(f"w_tws_{i}")),
            _dec(f.get(f"w_awa_{i}")),
            _dec(f.get(f"w_aws_{i}")),
            _maybe_int(f.get(f"w_ss_{i}")),
            _dec(f.get(f"w_sd_{i}")),
            _dec(f.get(f"w_spd_{i}")),
        )
        if all(v is None for v in vals):
            continue
        event.weather_readings.append(
            NavEventWeatherReading(
                slot_time=slot,
                tws_kn=vals[0],
                awa_deg=vals[1],
                aws_kn=vals[2],
                sea_state=vals[3],
                sea_direction_deg=vals[4],
                ship_speed_kn=vals[5],
            )
        )

    event.sail_readings.clear()
    for i, slot in enumerate(NAV_TIME_SLOTS):
        j0 = _maybe_bool(f.get(f"s_j0_{i}"))
        fj1 = _maybe_bool(f.get(f"s_fwdj1_{i}"))
        fms = _maybe_bool(f.get(f"s_fwdms_{i}"))
        aj1 = _maybe_bool(f.get(f"s_aftj1_{i}"))
        ams = _maybe_bool(f.get(f"s_aftms_{i}"))
        boost = _dec(f.get(f"s_boost_{i}"))
        ps = _dec(f.get(f"s_psload_{i}"))
        sb = _dec(f.get(f"s_sbload_{i}"))
        if not (j0 or fj1 or fms or aj1 or ams) and boost is None and ps is None and sb is None:
            continue
        event.sail_readings.append(
            NavEventSailReading(
                slot_time=slot,
                j0=j0,
                fwd_j1=fj1,
                fwd_ms=fms,
                aft_j1=aj1,
                aft_ms=ams,
                sail_boost_pct=boost,
                me_ps_load_pct=ps,
                me_sb_load_pct=sb,
            )
        )

    event.hold_readings.clear()
    for i, zone in enumerate(HOLD_ZONES):
        rh_ok = zone not in HOLD_ZONES_WITHOUT_RH
        min_t = _dec(f.get(f"hold_{i}_min_t"))
        min_rh = _dec(f.get(f"hold_{i}_min_rh")) if rh_ok else None
        mid_t = _dec(f.get(f"hold_{i}_mid_t"))
        mid_rh = _dec(f.get(f"hold_{i}_mid_rh")) if rh_ok else None
        if min_t is not None or min_rh is not None:
            event.hold_readings.append(
                NavEventHoldReading(period="minuit", zone=zone, temp_c=min_t, rh_pct=min_rh)
            )
        if mid_t is not None or mid_rh is not None:
            event.hold_readings.append(
                NavEventHoldReading(period="midi", zone=zone, temp_c=mid_t, rh_pct=mid_rh)
            )


async def _apply_form_to_draft(db: AsyncSession, event: NavEvent, user, f, engines) -> None:
    """Applique le formulaire à un brouillon : scalaires (garde auteur-seul du
    service) + relevés. Lève ``DraftAuthorError``/``EventStateError`` (service)."""
    payload = _build_event_payload(event.event_type, f)
    await event_capture.update_draft(db, event, user, payload)  # garde + scalaires + flush
    await _sync_event_readings(db, event, f, engines)
    await db.flush()


async def _my_event_drafts(db: AsyncSession, user) -> list[dict]:
    """Brouillons d'événements de l'utilisateur (les plus anciens d'abord)."""
    if user is None or getattr(user, "id", None) is None:
        return []
    now = datetime.now(UTC)
    rows = list(
        (
            await db.execute(
                select(NavEvent).where(
                    NavEvent.status == "brouillon",
                    NavEvent.author_user_id == user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    out = [
        {
            "event": e,
            "age_h": int(draft_reminders._age_hours(draft_reminders._last_saved(e), now)),
            "completion": event_capture.draft_completion(e),
        }
        for e in rows
    ]
    out.sort(key=lambda d: d["age_h"], reverse=True)
    return out


async def _resolve_leg_and_vessel(db: AsyncSession, user, vessel_id, leg_id):
    """Résout (leg, vessel) : leg explicite prioritaire, sinon leg actif du
    navire (ATD posé, ATA vide), sinon dernier leg connu du navire."""
    if leg_id:
        leg = await db.get(Leg, leg_id)
        if leg is not None:
            return leg, await db.get(Vessel, leg.vessel_id)
    vessel = await _resolve_bunkering_vessel(db, user, vessel_id)
    leg = None
    if vessel is not None:
        leg = (
            await db.execute(
                select(Leg)
                .where(Leg.vessel_id == vessel.id, Leg.atd.is_not(None), Leg.ata.is_(None))
                .order_by(Leg.etd.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if leg is None:
            leg = (
                await db.execute(
                    select(Leg).where(Leg.vessel_id == vessel.id).order_by(Leg.etd.desc()).limit(1)
                )
            ).scalar_one_or_none()
    return leg, vessel


async def _event_form_context(
    db: AsyncSession,
    *,
    event_type: str,
    event: NavEvent | None,
    leg: Leg | None,
    vessel: Vessel | None,
    errors: list[str] | None = None,
    locked: bool = False,
) -> dict:
    """Contexte commun du wizard (new / edit / réaffichage post-erreur)."""
    from app.models.port import Port

    now = datetime.now(UTC)
    engines = await referential_env.get_vessel_engines(db, vessel.id) if vessel else []

    prefill_pos = None
    if event is None and vessel is not None:
        prefill_pos = await event_capture.prefill_position(db, vessel, now)

    pol = pod = None
    if leg is not None:
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)

    # Relevés machine : rattachables à tout type d'événement (cf. modèle) —
    # préremplis quel que soit le type, pas seulement pour le Noon.
    engine_values: dict[int, NavEventEngineReading] = {}
    if event is not None:
        engine_values = {r.engine_id: r for r in event.engine_readings}

    weather_by_slot: dict[str, NavEventWeatherReading] = {}
    sail_by_slot: dict[str, NavEventSailReading] = {}
    hold_by_zone: dict[str, dict[str, NavEventHoldReading]] = {}
    if isinstance(event, NoonEvent):
        weather_by_slot = {r.slot_time: r for r in event.weather_readings}
        sail_by_slot = {r.slot_time: r for r in event.sail_readings}
        for r in event.hold_readings:
            hold_by_zone.setdefault(r.zone, {})[r.period] = r

    # ROB par carburant (CutoffEvent uniquement, G1) — préremplissage par
    # index de ligne (fuel_type est du texte libre, pas une clé référentielle
    # comme engine_id).
    rob_by_fuel_values: list[NavEventRobByFuel | None] = [None] * len(_CUTOFF_FUEL_TYPES)
    if isinstance(event, CutoffEvent):
        by_fuel = {r.fuel_type: r for r in event.rob_by_fuel_readings}
        rob_by_fuel_values = [by_fuel.get(ft) for ft in _CUTOFF_FUEL_TYPES]

    open_begins: list[NavEvent] = []
    if event_type == "anchoring_end" and leg is not None:
        anchorings = list(
            (
                await db.execute(
                    select(NavEvent).where(
                        NavEvent.leg_id == leg.id,
                        NavEvent.event_type.in_(("anchoring_begin", "anchoring_end")),
                    )
                )
            )
            .scalars()
            .all()
        )
        paired = {
            getattr(a, "paired_event_id", None)
            for a in anchorings
            if getattr(a, "paired_event_id", None)
        }
        open_begins = [
            a for a in anchorings if a.event_type == "anchoring_begin" and a.id not in paired
        ]

    # Défaut départ/arrivée : dates planning (prériempli, modifiable).
    default_portcall_dt = ""
    if event_type == "departure" and leg is not None:
        d = leg.atd or leg.etd
        default_portcall_dt = d.strftime("%Y-%m-%dT%H:%M") if d else ""
    elif event_type == "arrival" and leg is not None:
        d = leg.ata or leg.eta
        default_portcall_dt = d.strftime("%Y-%m-%dT%H:%M") if d else ""

    return {
        "mode": "edit" if event is not None else "new",
        "event_type": event_type,
        "event": event,
        "leg": leg,
        "vessel": vessel,
        "pol": pol,
        "pod": pod,
        "engines": engines,
        "engine_values": engine_values,
        "weather_by_slot": weather_by_slot,
        "sail_by_slot": sail_by_slot,
        "hold_by_zone": hold_by_zone,
        "cutoff_fuel_types": list(enumerate(_CUTOFF_FUEL_TYPES)),
        "rob_by_fuel_values": rob_by_fuel_values,
        "open_begins": open_begins,
        "prefill_pos": prefill_pos,
        "default_dt_local": now.strftime("%Y-%m-%dT%H:%M"),
        "default_portcall_dt": default_portcall_dt,
        "tz_choices": TIMEZONE_CHOICES,
        "time_slots": NAV_TIME_SLOTS,
        "hold_zones": list(enumerate(HOLD_ZONES)),
        "hold_zones_without_rh": HOLD_ZONES_WITHOUT_RH,
        "hold_periods": HOLD_PERIODS,
        "vessel_conditions": VESSEL_CONDITIONS,
        "position_sources": POSITION_SOURCES,
        "eta_paliers": list(enumerate(NAV_ETA_PALIERS)),
        "errors": errors or [],
        "locked": locked,
        "autosave_url": (f"/onboard/events/{event.id}/autosave" if event is not None else None),
        "event_type_labels": _EVENT_TYPE_LABELS,
    }


# Libellés courts par type (affichage listes/wizard).
_EVENT_TYPE_LABELS: dict[str, str] = {
    "noon": "Noon report",
    "departure": "Départ (Departure)",
    "arrival": "Arrivée (Arrival)",
    "anchoring_begin": "Début de mouillage",
    "anchoring_end": "Fin de mouillage",
    "cutoff": "Cut-off de fin d'année (Year-End Cut-off)",
}

# Carburants proposés au formulaire Cut-off (ROB par carburant, G1) — texte
# libre côté modèle (cf. NavEventRobByFuel.fuel_type), mais un nombre fixe de
# lignes préremplies suffit en pratique (flotte actuelle très majoritairement
# MDO — cf. referential_env.py, note V1). Lignes laissées vides ignorées.
_CUTOFF_FUEL_TYPES: tuple[str, ...] = ("MDO", "MGO", "VLSFO")


async def _render_event_form(
    request: Request,
    user,
    db: AsyncSession,
    *,
    event_type: str,
    event: NavEvent | None,
    leg: Leg | None,
    vessel: Vessel | None,
    errors: list[str] | None = None,
    locked: bool = False,
    status_code: int = 200,
    notice: str | None = None,
) -> HTMLResponse:
    ctx = await _event_form_context(
        db,
        event_type=event_type,
        event=event,
        leg=leg,
        vessel=vessel,
        errors=errors,
        locked=locked,
    )
    ctx.update({"request": request, "user": user, "notice": notice})
    return templates.TemplateResponse("staff/onboard/event_form.html", ctx, status_code=status_code)


# ─────────────────────────────── Écrans ────────────────────────────────────


@router.get("/events", response_class=HTMLResponse)
async def onboard_events_index(
    request: Request,
    vessel_id: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """Chaîne chronologique des événements du leg actif + brouillons du user."""
    leg, vessel = await _resolve_leg_and_vessel(db, user, vessel_id, leg_id)
    events: list[NavEvent] = []
    if leg is not None:
        rows = list(
            (await db.execute(select(NavEvent).where(NavEvent.leg_id == leg.id))).scalars().all()
        )
        events = sorted(rows, key=_event_sort_key)
    my_drafts = await _my_event_drafts(db, user)
    vessels = list(
        (await db.execute(select(Vessel).where(Vessel.is_active.is_(True)).order_by(Vessel.code)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        "staff/onboard/events_list.html",
        {
            "request": request,
            "user": user,
            "leg": leg,
            "vessel": vessel,
            "vessels": vessels,
            "events": events,
            "my_drafts": my_drafts,
            "event_types": EVENT_TYPES,
            "event_type_labels": _EVENT_TYPE_LABELS,
        },
    )


@router.get("/events/new/{event_type}", response_class=HTMLResponse)
async def onboard_event_new_form(
    event_type: str,
    request: Request,
    vessel_id: int | None = None,
    leg_id: int | None = None,
    notice: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """Wizard mobile-first de déclaration d'un événement (5 types).

    ``notice=capture_v2`` (LOT 14) → bandeau informant que l'ancien formulaire
    noon a été remplacé par cette déclaration d'événements.
    """
    if event_type not in EVENT_TYPES:
        raise HTTPException(status_code=404, detail="Type d'événement inconnu.")
    leg, vessel = await _resolve_leg_and_vessel(db, user, vessel_id, leg_id)
    return await _render_event_form(
        request,
        user,
        db,
        event_type=event_type,
        event=None,
        leg=leg,
        vessel=vessel,
        notice=notice,
    )


@router.post("/events")
async def onboard_event_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """Création d'un brouillon d'événement (+ dédoublonnage PWA par client_uuid)."""
    f = await request.form()
    event_type = str(f.get("event_type") or "")
    if event_type not in EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Type d'événement inconnu.")

    client_uuid = _clean_client_uuid(f.get("client_uuid"))
    if client_uuid:
        existing = (
            await db.execute(select(NavEvent).where(NavEvent.client_uuid == client_uuid))
        ).scalar_one_or_none()
        if existing is not None:
            # Rejeu file offline — déjà enregistré, on ne duplique pas (lot 3).
            return RedirectResponse(url=f"/onboard/events/{existing.id}/edit", status_code=303)

    leg_id = _int_or_400(f.get("leg_id"), "leg_id")
    if leg_id is None:
        raise HTTPException(status_code=400, detail="Voyage (leg) obligatoire.")
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=400, detail="Voyage introuvable.")
    vessel = await db.get(Vessel, leg.vessel_id)

    payload = _build_event_payload(event_type, f)
    try:
        event = await event_capture.create_draft(
            db,
            leg=leg,
            vessel=vessel,
            event_type=event_type,
            author=user,
            payload=payload,
            client_uuid=client_uuid,
        )
    except event_capture.EventCaptureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    engines = await referential_env.get_vessel_engines(db, vessel.id) if vessel else []
    await _sync_event_readings(db, event, f, engines)
    await db.flush()

    await activity_record(
        db,
        action="nav_event_draft_create",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="nav_event",
        entity_id=event.id,
        entity_label=event.event_type,
    )
    return RedirectResponse(url=f"/onboard/events/{event.id}/edit", status_code=303)


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
async def onboard_event_edit_form(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> HTMLResponse:
    """Reprise d'un brouillon — réservée à son auteur (garde D11 → 403 clair)."""
    event = await db.get(NavEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    if event.author_user_id is not None and event.author_user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Seul l'auteur du brouillon peut le reprendre.",
        )
    leg = await db.get(Leg, event.leg_id)
    vessel = await db.get(Vessel, event.vessel_id) if event.vessel_id else None
    return await _render_event_form(
        request,
        user,
        db,
        event_type=event.event_type,
        event=event,
        leg=leg,
        vessel=vessel,
        locked=(event.status != "brouillon"),
    )


@router.post("/events/{event_id}/edit")
async def onboard_event_edit_post(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """Sauvegarde d'un brouillon (garde auteur-seul du service → 403)."""
    event = await db.get(NavEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    f = await request.form()
    vessel = await db.get(Vessel, event.vessel_id) if event.vessel_id else None
    engines = await referential_env.get_vessel_engines(db, vessel.id) if vessel else []
    try:
        await _apply_form_to_draft(db, event, user, f, engines)
    except event_capture.DraftAuthorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except event_capture.EventStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await activity_record(
        db,
        action="nav_event_draft_update",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="nav_event",
        entity_id=event.id,
        entity_label=event.event_type,
    )
    return RedirectResponse(url=f"/onboard/events/{event.id}/edit", status_code=303)


@router.post("/events/{event_id}/autosave")
async def onboard_event_autosave(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> Response:
    """Autosave léger (appelé par ``event-autosave.js``) : met à jour le
    brouillon + ``last_saved_at``, répond 204 (aucun rendu). Même garde
    auteur-seul (403) et garde d'état (409)."""
    event = await db.get(NavEvent, event_id)
    if event is None:
        return Response(status_code=404)
    f = await request.form()
    vessel = await db.get(Vessel, event.vessel_id) if event.vessel_id else None
    engines = await referential_env.get_vessel_engines(db, vessel.id) if vessel else []
    try:
        await _apply_form_to_draft(db, event, user, f, engines)
    except event_capture.DraftAuthorError:
        return Response(status_code=403)
    except event_capture.EventStateError:
        return Response(status_code=409)
    return Response(status_code=204)


@router.post("/events/{event_id}/finalize")
async def onboard_event_finalize(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
):
    """Finalisation d'un événement : applique les dernières saisies puis
    ``event_capture.finalize`` (moteur de règles). Un refus (règle bloquante ou
    position manuelle sans justification, R05) réaffiche le formulaire avec les
    messages (200) — jamais de 500."""
    event = await db.get(NavEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404)
    f = await request.form()
    vessel = await db.get(Vessel, event.vessel_id) if event.vessel_id else None
    engines = await referential_env.get_vessel_engines(db, vessel.id) if vessel else []

    try:
        await _apply_form_to_draft(db, event, user, f, engines)
    except event_capture.DraftAuthorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except event_capture.EventStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        await event_capture.finalize(db, event, user)
    except event_capture.EventFinalizationError as exc:
        leg = await db.get(Leg, event.leg_id)
        return await _render_event_form(
            request,
            user,
            db,
            event_type=event.event_type,
            event=event,
            leg=leg,
            vessel=vessel,
            errors=exc.messages,
            status_code=200,
        )
    except event_capture.DraftAuthorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    await activity_record(
        db,
        action="nav_event_finalize",
        user_id=user.id,
        user_name=user.username,
        module="captain",
        entity_type="nav_event",
        entity_id=event.id,
        entity_label=event.event_type,
    )
    return RedirectResponse(url=f"/onboard/events?leg_id={event.leg_id}", status_code=303)


# ───────────────────── Cron R19 — brouillons dormants ───────────────────────


@api_router.post("/api/mrv/draft-reminders")
async def mrv_draft_reminders_cron(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Cron externe (Power Automate) — alerte R19 des brouillons dormants.

    Auth ``X-API-Token`` (temps constant) ; 503 si ``MRV_DRAFTS_API_TOKEN``
    non configuré. Rappel Master (1er seuil) + alerte siège (2e seuil),
    idempotents (cf. ``services.draft_reminders``)."""
    expected = (settings.mrv_drafts_api_token or "").strip() or None
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MRV_DRAFTS_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    summary = await draft_reminders.run_draft_reminders(db)
    logger.info(
        "R19 draft reminders (API cron): scanned=%d master=%d siege=%d",
        summary["scanned"],
        summary["master"],
        summary["siege"],
    )
    return JSONResponse(summary)


# ─────────────────── Cron R27 — approche bascule d'année (G1) ──────────────


@api_router.post("/api/mrv/cutoff-reminders")
async def mrv_cutoff_reminders_cron(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Cron externe (Power Automate) — rappel R27 d'approche de bascule
    d'année civile sans événement Cut-off finalisé (CDC v0.7 §9.2).

    Auth ``X-API-Token`` (temps constant) ; 503 si ``MRV_CUTOFF_API_TOKEN``
    non configuré. Rappel nominatif à chaque utilisateur assigné au navire
    (idempotent, cf. ``services.cutoff_reminders``)."""
    expected = (settings.mrv_cutoff_api_token or "").strip() or None
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MRV_CUTOFF_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    summary = await cutoff_reminders.run_cutoff_reminders(db)
    logger.info(
        "R27 cutoff reminders (API cron): scanned=%d notified=%d",
        summary["scanned"],
        summary["notified"],
    )
    return JSONResponse(summary)
