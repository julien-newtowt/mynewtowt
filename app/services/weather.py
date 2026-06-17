"""Marine weather forecasts — Open-Meteo (free) + Windy (paid, optional).

Default provider: Open-Meteo (no API key, free for non-commercial).
If WINDY_API_KEY is configured, the caller can request Windy via the
``provider="windy"`` argument; falls back to Open-Meteo on Windy failure.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WINDY_POINT_FORECAST_URL = "https://api.windy.com/api/point-forecast/v2"


@dataclass(frozen=True)
class WeatherPoint:
    time: str
    wind_speed_kn: float | None
    wind_direction_deg: float | None
    wave_height_m: float | None
    wave_direction_deg: float | None
    wave_period_s: float | None
    # V3.8 — conditions complémentaires (température air, courant de surface).
    temperature_c: float | None = None
    current_speed_kn: float | None = None
    current_direction_deg: float | None = None
    # V3.9 — bloc « conditions actuelles » par navire (page Navigation).
    pressure_hpa: float | None = None
    visibility_km: float | None = None
    humidity_pct: float | None = None
    cloud_cover_pct: float | None = None


@dataclass(frozen=True)
class WeatherForecast:
    latitude: float
    longitude: float
    provider: str
    points: list[WeatherPoint]


async def fetch_forecast(
    lat: float,
    lon: float,
    *,
    hours: int = 48,
    provider: Literal["open-meteo", "windy"] = "open-meteo",
) -> WeatherForecast | None:
    """Fetch a marine forecast. Falls back to Open-Meteo if Windy fails."""
    if provider == "windy" and settings.windy_api_key:
        result = await _fetch_windy(lat, lon, hours)
        if result is not None:
            return result
        logger.info("Windy fetch failed; falling back to Open-Meteo")
    return await _fetch_open_meteo(lat, lon, hours)


# Backwards-compatible alias
fetch_marine_forecast = fetch_forecast


async def _fetch_open_meteo(lat: float, lon: float, hours: int) -> WeatherForecast | None:
    params_wind = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "wind_speed_10m,wind_direction_10m,temperature_2m,"
            "surface_pressure,visibility,relative_humidity_2m,cloud_cover"
        ),
        "wind_speed_unit": "kn",
        "forecast_hours": hours,
    }
    params_marine = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "wave_height,wave_direction,wave_period,"
            "ocean_current_velocity,ocean_current_direction"
        ),
        "forecast_hours": hours,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r_wind = await client.get(OPEN_METEO_FORECAST_URL, params=params_wind)
            r_wind.raise_for_status()
            r_marine = await client.get(OPEN_METEO_MARINE_URL, params=params_marine)
            r_marine.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Open-Meteo fetch failed for (%s, %s): %s", lat, lon, e)
        return None

    wind = r_wind.json().get("hourly", {})
    marine = r_marine.json().get("hourly", {})
    times = wind.get("time", []) or marine.get("time", [])
    points = [
        WeatherPoint(
            time=t,
            wind_speed_kn=_safe(wind.get("wind_speed_10m"), i),
            wind_direction_deg=_safe(wind.get("wind_direction_10m"), i),
            wave_height_m=_safe(marine.get("wave_height"), i),
            wave_direction_deg=_safe(marine.get("wave_direction"), i),
            wave_period_s=_safe(marine.get("wave_period"), i),
            temperature_c=_safe(wind.get("temperature_2m"), i),
            current_speed_kn=_kmh_to_kn(_safe(marine.get("ocean_current_velocity"), i)),
            current_direction_deg=_safe(marine.get("ocean_current_direction"), i),
            pressure_hpa=_safe(wind.get("surface_pressure"), i),
            visibility_km=_m_to_km(_safe(wind.get("visibility"), i)),
            humidity_pct=_safe(wind.get("relative_humidity_2m"), i),
            cloud_cover_pct=_safe(wind.get("cloud_cover"), i),
        )
        for i, t in enumerate(times)
    ]
    return WeatherForecast(latitude=lat, longitude=lon, provider="open-meteo", points=points)


async def _fetch_windy(lat: float, lon: float, hours: int) -> WeatherForecast | None:
    if not settings.windy_api_key:
        return None
    payload = {
        "lat": lat,
        "lon": lon,
        "model": "gfs",
        "parameters": ["wind", "waves", "temp", "pressure"],
        "levels": ["surface"],
        "key": settings.windy_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(WINDY_POINT_FORECAST_URL, json=payload)
            r.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Windy fetch failed: %s", e)
        return None

    data = r.json()
    times = data.get("ts", [])
    wind_u = data.get("wind_u-surface", [])
    wind_v = data.get("wind_v-surface", [])
    waves_h = data.get("waves_height-surface", [])
    waves_d = data.get("waves_direction-surface", [])
    waves_p = data.get("waves_period-surface", [])
    temp_k = data.get("temp-surface", [])
    press_pa = data.get("pressure-surface", [])
    points: list[WeatherPoint] = []
    for i, t in enumerate(times[:hours]):
        u = _safe(wind_u, i) or 0.0
        v = _safe(wind_v, i) or 0.0
        speed_ms = math.sqrt(u * u + v * v)
        speed_kn = speed_ms * 1.9438
        dir_deg = (math.degrees(math.atan2(-u, -v)) + 360) % 360
        iso = _dt.datetime.utcfromtimestamp(t / 1000).isoformat() + "Z"
        tk = _safe(temp_k, i)
        pa = _safe(press_pa, i)
        points.append(
            WeatherPoint(
                time=iso,
                wind_speed_kn=round(speed_kn, 1),
                wind_direction_deg=round(dir_deg, 1),
                wave_height_m=_safe(waves_h, i),
                wave_direction_deg=_safe(waves_d, i),
                wave_period_s=_safe(waves_p, i),
                # Windy : température en Kelvin, pression en Pa. Courant /
                # visibilité non fournis par GFS → complétés via Open-Meteo
                # (fetch_point_conditions).
                temperature_c=round(tk - 273.15, 1) if tk is not None else None,
                pressure_hpa=round(pa / 100.0, 1) if pa is not None else None,
            )
        )
    return WeatherForecast(latitude=lat, longitude=lon, provider="windy", points=points)


def _safe(arr, i):
    try:
        return arr[i] if arr else None
    except (IndexError, TypeError):
        return None


def _kmh_to_kn(v: float | None) -> float | None:
    """km/h → nœuds (Open-Meteo donne les courants océaniques en km/h)."""
    return round(v * 0.539957, 2) if v is not None else None


def _m_to_km(v: float | None) -> float | None:
    """mètres → km (Open-Meteo donne la visibilité en mètres)."""
    return round(v / 1000.0, 1) if v is not None else None


def _nearest_point(points: list[WeatherPoint], when) -> WeatherPoint | None:
    """Point dont le timestamp est le plus proche de ``when`` (aware UTC)."""
    if not points:
        return None
    target = when.replace(tzinfo=_dt.UTC) if when.tzinfo is None else when
    best: WeatherPoint | None = None
    best_delta = float("inf")
    for p in points:
        try:
            t = _dt.datetime.fromisoformat(p.time.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=_dt.UTC)
        except (ValueError, AttributeError):
            continue
        delta = abs((t - target).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best = p
    return best


# ─────────────────────────────────────────────────────────────────────
# Helpers haut niveau pour les écrans (point unique, summary)
# ─────────────────────────────────────────────────────────────────────


async def fetch_current(lat: float, lon: float) -> WeatherPoint | None:
    """Renvoie la météo au plus proche du moment présent (1er point H+0).

    Utilisé par le pré-remplissage noon report (vent au point GPS courant).
    """
    fc = await fetch_forecast(lat, lon, hours=6)
    if fc and fc.points:
        return fc.points[0]
    return None


async def fetch_at(
    lat: float,
    lon: float,
    when,
    *,
    window_hours: int = 72,
    provider: Literal["open-meteo", "windy"] = "open-meteo",
) -> WeatherPoint | None:
    """Renvoie le point forecast le plus proche d'une datetime cible.

    ``when`` est aware UTC. On charge un forecast couvrant ``window_hours``
    et on pioche l'index dont le timestamp est le plus proche. Utilisé par
    leg_detail (POL @ ETD, POD @ ETA) et next-port (ETA arrivée).
    """
    fc = await fetch_forecast(lat, lon, hours=window_hours, provider=provider)
    if not fc or not fc.points:
        return None
    return _nearest_point(fc.points, when)


def _merge_points(primary: WeatherPoint, fallback: WeatherPoint | None) -> WeatherPoint:
    """Complète les champs ``None`` de ``primary`` avec ceux de ``fallback``."""
    if fallback is None:
        return primary
    import dataclasses

    patch = {
        field: getattr(fallback, field)
        for field in (
            "wind_speed_kn",
            "wind_direction_deg",
            "wave_height_m",
            "wave_direction_deg",
            "wave_period_s",
            "temperature_c",
            "current_speed_kn",
            "current_direction_deg",
            "pressure_hpa",
            "visibility_km",
            "humidity_pct",
            "cloud_cover_pct",
        )
        if getattr(primary, field) is None and getattr(fallback, field) is not None
    }
    return dataclasses.replace(primary, **patch) if patch else primary


async def _fetch_open_meteo_range(
    lat: float,
    lon: float,
    *,
    start_date: str,
    end_date: str,
    archive: bool = False,
) -> WeatherForecast | None:
    """Hourly vent/température (+ houle/courant via marine) sur une plage de dates.

    ``archive=True`` → endpoint réanalyse ERA5 (dates anciennes) ; sinon endpoint
    forecast (couvre ~3 mois de passé récent et le futur). Le marine endpoint
    fournit houle + courant pour la même plage. Permet d'obtenir la météo
    **valide au moment** d'un point GPS historique (et pas une prévision future).
    """
    wind_url = OPEN_METEO_ARCHIVE_URL if archive else OPEN_METEO_FORECAST_URL
    hourly_vars = (
        "wind_speed_10m,wind_direction_10m,temperature_2m,"
        "surface_pressure,relative_humidity_2m,cloud_cover"
    )
    # ``visibility`` existe sur l'endpoint forecast mais pas sur l'archive ERA5.
    if not archive:
        hourly_vars += ",visibility"
    params_wind = {
        "latitude": lat,
        "longitude": lon,
        "hourly": hourly_vars,
        "wind_speed_unit": "kn",
        "start_date": start_date,
        "end_date": end_date,
    }
    params_marine = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "wave_height,wave_direction,wave_period,"
            "ocean_current_velocity,ocean_current_direction"
        ),
        "start_date": start_date,
        "end_date": end_date,
    }
    wind: dict = {}
    marine: dict = {}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r_wind = await client.get(wind_url, params=params_wind)
            r_wind.raise_for_status()
            wind = r_wind.json().get("hourly", {})
            try:
                r_marine = await client.get(OPEN_METEO_MARINE_URL, params=params_marine)
                r_marine.raise_for_status()
                marine = r_marine.json().get("hourly", {})
            except httpx.HTTPError as e:
                logger.info("Open-Meteo marine range failed (%s,%s): %s", lat, lon, e)
    except httpx.HTTPError as e:
        logger.warning("Open-Meteo range fetch failed (%s,%s): %s", lat, lon, e)
        return None

    times = wind.get("time", []) or marine.get("time", [])
    points = [
        WeatherPoint(
            time=t,
            wind_speed_kn=_safe(wind.get("wind_speed_10m"), i),
            wind_direction_deg=_safe(wind.get("wind_direction_10m"), i),
            wave_height_m=_safe(marine.get("wave_height"), i),
            wave_direction_deg=_safe(marine.get("wave_direction"), i),
            wave_period_s=_safe(marine.get("wave_period"), i),
            temperature_c=_safe(wind.get("temperature_2m"), i),
            current_speed_kn=_kmh_to_kn(_safe(marine.get("ocean_current_velocity"), i)),
            current_direction_deg=_safe(marine.get("ocean_current_direction"), i),
            pressure_hpa=_safe(wind.get("surface_pressure"), i),
            visibility_km=_m_to_km(_safe(wind.get("visibility"), i)),
            humidity_pct=_safe(wind.get("relative_humidity_2m"), i),
            cloud_cover_pct=_safe(wind.get("cloud_cover"), i),
        )
        for i, t in enumerate(times)
    ]
    return WeatherForecast(latitude=lat, longitude=lon, provider="open-meteo", points=points)


async def fetch_point_conditions(
    lat: float,
    lon: float,
    when,
    *,
    provider: Literal["open-meteo", "windy"] = "windy",
) -> WeatherPoint | None:
    """Conditions complètes (vent · houle · courant · température) **au moment** d'un point GPS.

    Stratégie sensible au temps (les positions satcom sont des relevés passés) :

    1. Quasi temps réel (point récent / leg actif) → on interroge en priorité
       Windy (forecast pertinent) ;
    2. dans tous les cas, on récupère la valeur exacte à l'heure du point via
       Open-Meteo (forecast ``past_days`` pour le passé récent, archive ERA5
       au-delà) — c'est aussi la source du **courant** que GFS/Windy ne donne pas ;
    3. fusion : Windy prime, Open-Meteo complète les champs manquants.

    Utilisé par Performance › Navigation (1 point échantillonné / 30 min).
    """
    when_utc = when.replace(tzinfo=_dt.UTC) if when.tzinfo is None else when
    now = _dt.datetime.now(_dt.UTC)
    age_days = (now - when_utc).total_seconds() / 86400.0  # >0 passé, <0 futur

    primary: WeatherPoint | None = None
    if provider == "windy" and settings.windy_api_key and -10 <= age_days <= 2:
        primary = await fetch_at(lat, lon, when_utc, provider="windy")

    day = when_utc.date()
    start = (day - _dt.timedelta(days=1)).isoformat()
    end = (day + _dt.timedelta(days=1)).isoformat()
    om_fc = await _fetch_open_meteo_range(
        lat, lon, start_date=start, end_date=end, archive=age_days > 5
    )
    om_point = _nearest_point(om_fc.points, when_utc) if om_fc else None

    if primary is None:
        return om_point
    return _merge_points(primary, om_point)


def summarize(point: WeatherPoint | None) -> str:
    """Phrase courte type 'NW 18 kn · houle 2.1 m'. None si pas de données."""
    if point is None:
        return "—"
    parts: list[str] = []
    if point.wind_speed_kn is not None and point.wind_direction_deg is not None:
        parts.append(f"{_compass(point.wind_direction_deg)} {point.wind_speed_kn:.0f} kn")
    elif point.wind_speed_kn is not None:
        parts.append(f"{point.wind_speed_kn:.0f} kn")
    if point.wave_height_m is not None:
        parts.append(f"houle {point.wave_height_m:.1f} m")
    return " · ".join(parts) if parts else "—"


def compass(deg: float | None) -> str:
    """Rose 16 directions publique (None → '')."""
    return _compass(deg) if deg is not None else ""


# Échelle de Beaufort (force, libellé FR) par seuil de vent en nœuds.
_BEAUFORT = (
    (1, "Calme"),
    (3, "Très légère brise"),
    (6, "Légère brise"),
    (10, "Petite brise"),
    (16, "Jolie brise"),
    (21, "Bonne brise"),
    (27, "Vent frais"),
    (33, "Grand frais"),
    (40, "Coup de vent"),
    (47, "Fort coup de vent"),
    (55, "Tempête"),
    (63, "Violente tempête"),
)


def beaufort(kn: float | None) -> tuple[int, str] | None:
    """(force Beaufort 0-12, libellé FR) à partir du vent en nœuds."""
    if kn is None:
        return None
    for force, (upper, label) in enumerate(_BEAUFORT):
        if kn < upper:
            return force, label
    return 12, "Ouragan"


def _compass(deg: float) -> str:
    """Convertit un cap décimal en rose 16 directions (N/NNE/NE/...)."""
    dirs = (
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    )
    idx = int(((deg % 360) + 11.25) // 22.5) % 16
    return dirs[idx]
