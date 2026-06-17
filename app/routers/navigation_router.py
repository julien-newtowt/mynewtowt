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
from app.permissions import require_permission
from app.services import weather_history
from app.services.activity import record as activity_record
from app.services.leg_filter import build_leg_filter
from app.services.voyage_track import compute_metrics, positions_for_leg, positions_payload
from app.templating import templates

logger = logging.getLogger("weather")

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
        }
        for o in observations
    ]


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def navigation_index(
    request: Request,
    vessel: str | None = None,
    year: int | None = None,
    leg_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("planning", "C")),
) -> HTMLResponse:
    f = await build_leg_filter(db, vessel=vessel, year=year, leg_id=leg_id)
    selected: Leg | None = f["selected_leg"]

    ctx: dict = {
        "request": request,
        "user": user,
        "leg_filter_ctx": f,
        "maptiler_token": settings.map_token,
        "selected_leg": selected,
        "metrics": None,
        "points": [],
        "dep_port": None,
        "arr_port": None,
        "weather_count": 0,
        "weather_provider": weather_history.active_provider(),
    }

    if selected is not None:
        positions = await positions_for_leg(db, selected)
        dep_port = await db.get(Port, selected.departure_port_id)
        arr_port = await db.get(Port, selected.arrival_port_id)
        metrics = compute_metrics(positions, selected, arr_port=arr_port)
        observations = await weather_history.observations_for_leg(db, selected)
        ctx.update(
            {
                "metrics": metrics,
                "points": positions_payload(positions),
                "dep_port": dep_port,
                "arr_port": arr_port,
                "weather_count": len(observations),
            }
        )

    return templates.TemplateResponse("staff/navigation/index.html", ctx)


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
