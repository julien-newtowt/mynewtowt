"""Performance › Navigation — trajet réellement réalisé, leg par leg.

Pour chaque leg (actif ou historique), sélectionné via le **filtre de
référence** (navire × année × leg, cf. ``_leg_filter.html``) :

- carte des points GPS satcom + trait du parcours réalisé + ligne théorique
  orthodromique POL→POD ;
- distance réelle parcourue vs distance théorique ;
- durée écoulée depuis le départ ;
- distance restant à parcourir ;
- météo le long de la trace, lue depuis l'**historique** ``vessel_weather``
  (snapshots Windy du dernier point GPS, capturés toutes les 30 min) — donc
  disponible aussi pour les legs déjà réalisés.

Endpoints machine :
- ``POST /api/weather/refresh`` — header ``X-API-Token: <WEATHER_API_TOKEN>`` :
  cron Power Automate (toutes les 30 min) qui historise la météo Windy du
  dernier point GPS de chaque navire.
"""

from __future__ import annotations

import logging
import secrets as _secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import weather as wx
from app.services import weather_history
from app.services.activity import record as activity_record
from app.services.leg_filter import build_leg_filter
from app.services.voyage_track import compute_metrics, positions_for_leg, positions_payload
from app.templating import templates

logger = logging.getLogger("weather")

# Palette de couleurs distinctes par leg sélectionné (charte Kairos + extensions).
_LEG_PALETTE = (
    "#0D5966",
    "#B47148",
    "#87BD29",
    "#1F7A8C",
    "#9B5DE5",
    "#E07A5F",
    "#3D405B",
    "#2A9D8F",
    "#C81D6B",
    "#5A7D2A",
)

# NB : préfixe ``/performance/navigation`` et non ``/navigation`` — ce dernier
# est déjà pris par la page vitrine publique (vitrine_router : marketing
# courants/propulsion vélique). On évite ainsi la collision de route.
router = APIRouter(prefix="/performance/navigation", tags=["navigation"])
api_router = APIRouter(prefix="/api/weather", tags=["weather-api"])


def _weather_payload(observations) -> list[dict]:
    """Sérialise des observations météo historisées pour la carte."""
    return [
        {
            "lat": o.latitude,
            "lon": o.longitude,
            "t": o.recorded_at.isoformat(),
            "wind_kn": o.wind_speed_kn,
            "wind_dir": o.wind_direction_deg,
            "current_kn": o.current_speed_kn,
            "current_dir": o.current_direction_deg,
            "wave_m": o.wave_height_m,
            "wave_dir": o.wave_direction_deg,
            "wave_period_s": o.wave_period_s,
            "temp_c": o.temperature_c,
            "pressure_hpa": o.pressure_hpa,
            "visibility_km": o.visibility_km,
        }
        for o in observations
    ]


def _port_dict(p: Port | None) -> dict | None:
    if p is None or p.latitude is None or p.longitude is None:
        return None
    return {"lat": p.latitude, "lon": p.longitude, "name": p.name, "locode": p.locode}


def _fleet_weather_entry(vessel: Vessel, obs) -> dict:
    """Bloc « conditions actuelles » d'un navire à partir de sa dernière observation."""
    bf = wx.beaufort(obs.wind_speed_kn)
    return {
        "vessel_code": vessel.code,
        "vessel_name": vessel.name,
        "recorded_at": obs.recorded_at,
        "lat": obs.latitude,
        "lon": obs.longitude,
        "wind_kn": obs.wind_speed_kn,
        "wind_dir": obs.wind_direction_deg,
        "wind_compass": wx.compass(obs.wind_direction_deg),
        "beaufort": bf[0] if bf else None,
        "beaufort_label": bf[1] if bf else None,
        "current_kn": obs.current_speed_kn,
        "current_dir": obs.current_direction_deg,
        "current_compass": wx.compass(obs.current_direction_deg),
        "wave_m": obs.wave_height_m,
        "wave_dir": obs.wave_direction_deg,
        "wave_period_s": obs.wave_period_s,
        "temp_c": obs.temperature_c,
        "pressure_hpa": obs.pressure_hpa,
        "visibility_km": obs.visibility_km,
        "humidity_pct": obs.humidity_pct,
        "cloud_cover_pct": obs.cloud_cover_pct,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def navigation_index(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    # Multi-sélection : plusieurs legs (potentiellement de plusieurs navires)
    # via des query-params ``leg_id`` répétés (?leg_id=12&leg_id=15…).
    selected_ids: list[int] = []
    for raw in request.query_params.getlist("leg_id"):
        try:
            lid = int(raw)
        except ValueError:
            continue
        if lid not in selected_ids:
            selected_ids.append(lid)

    # Filtre de référence pour la navigation navire × année (chips multi-toggle).
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=None)

    # ── Legs sélectionnés : trace + métriques + météo, une couleur par leg ──
    legs_data: list[dict] = []
    map_legs: list[dict] = []
    for idx, lid in enumerate(selected_ids):
        leg = await db.get(Leg, lid)
        if leg is None:
            continue
        positions = await positions_for_leg(db, leg)
        dep = await db.get(Port, leg.departure_port_id)
        arr = await db.get(Port, leg.arrival_port_id)
        metrics = compute_metrics(positions, leg, arr_port=arr)
        observations = await weather_history.observations_for_leg(db, leg)
        v_obj = await db.get(Vessel, leg.vessel_id)
        color = _LEG_PALETTE[idx % len(_LEG_PALETTE)]
        pts = positions_payload(positions)
        wx_pts = _weather_payload(observations)
        legs_data.append(
            {
                "leg": leg,
                "vessel": v_obj,
                "dep": dep,
                "arr": arr,
                "metrics": metrics,
                "color": color,
                "point_count": len(pts),
                "weather_count": len(wx_pts),
            }
        )
        map_legs.append(
            {
                "leg_code": leg.leg_code,
                "vessel": v_obj.name if v_obj else "",
                "color": color,
                "points": pts,
                "weather": wx_pts,
                "dep": _port_dict(dep),
                "arr": _port_dict(arr),
            }
        )

    # ── URLs de bascule (add/remove) des chips du navire/année courant ──
    from urllib.parse import urlencode

    base = "/performance/navigation"

    def _toggle_url(lid: int) -> str:
        new = [x for x in selected_ids if x != lid] if lid in selected_ids else [*selected_ids, lid]
        qs = [("vessel", f["selected_vessel"] or ""), ("year", f["current_year"])]
        qs += [("leg_id", x) for x in new]
        return base + "?" + urlencode(qs)

    leg_chips = [
        {"leg": lg, "selected": lg.id in selected_ids, "toggle_url": _toggle_url(lg.id)}
        for lg in f["legs"]
    ]
    # Pills des legs sélectionnés (tous navires) avec URL de retrait.
    selected_pills = [
        {
            "leg": d["leg"],
            "vessel": d["vessel"],
            "color": d["color"],
            "remove_url": _toggle_url(d["leg"].id),
        }
        for d in legs_data
    ]
    # Query-string propagée sur les onglets navire/année (préserve la sélection).
    extra_query = urlencode([("leg_id", x) for x in selected_ids])

    # ── Bloc « conditions actuelles par navire » (dernière obs / navire) ──
    latest = await weather_history.latest_per_vessel(db)
    vessels_by_id = {v.id: v for v in f["vessels"]}
    fleet_weather = []
    for vid, obs in latest.items():
        v_obj = vessels_by_id.get(vid) or await db.get(Vessel, vid)
        if v_obj is not None:
            fleet_weather.append(_fleet_weather_entry(v_obj, obs))
    fleet_weather.sort(key=lambda e: e["vessel_code"])

    return templates.TemplateResponse(
        "staff/navigation/index.html",
        {
            "request": request,
            "user": user,
            "leg_filter_ctx": f,
            "maptiler_token": settings.map_token,
            "selected_ids": selected_ids,
            "legs_data": legs_data,
            "map_legs": map_legs,
            "leg_chips": leg_chips,
            "selected_pills": selected_pills,
            "extra_query": extra_query,
            "fleet_weather": fleet_weather,
            "weather_provider": weather_history.active_provider(),
        },
    )


@router.get("/legs/{leg_id}/weather")
async def leg_weather(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> JSONResponse:
    """Météo historisée le long de la trace d'un leg (lecture ``vessel_weather``).

    Consommé par ``navigation-map.js`` pour placer les marqueurs météo (vent ·
    courant · vague · température) le long du trajet. Les données proviennent
    des snapshots Windy capturés toutes les 30 min — pas d'appel live ici.
    """
    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise HTTPException(status_code=404, detail="Leg introuvable")
    observations = await weather_history.observations_for_leg(db, leg)
    return JSONResponse(
        {
            "leg_id": leg_id,
            "count": len(observations),
            "points": _weather_payload(observations),
        }
    )


@router.post("/weather/refresh")
async def weather_refresh_manual(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "M")),
) -> RedirectResponse:
    """Déclenche manuellement un snapshot météo (bouton staff)."""
    result = await weather_history.snapshot_latest(db)
    await activity_record(
        db,
        action="weather_snapshot",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="planning",
        entity_type="vessel_weather",
        entity_id=None,
        detail=f"manual snapshot — {result.get('saved', 0)} historisé(s)",
    )
    target = request.headers.get("referer") or "/performance/navigation"
    if request.headers.get("hx-request"):
        from fastapi.responses import Response

        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(url=target, status_code=303)


def _expected_token() -> str | None:
    return (settings.weather_api_token or "").strip() or None


@api_router.post("/refresh")
async def weather_refresh_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Cron externe (Power Automate, toutes les 30 min). Auth par X-API-Token.

    Historise la météo Windy au dernier point GPS connu de chaque navire.
    Retourne 503 si ``WEATHER_API_TOKEN`` n'est pas configuré.
    """
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WEATHER_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    result = await weather_history.snapshot_latest(db)
    return JSONResponse(result)
