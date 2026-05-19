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
WINDY_POINT_FORECAST_URL = "https://api.windy.com/api/point-forecast/v2"


@dataclass(frozen=True)
class WeatherPoint:
    time: str
    wind_speed_kn: float | None
    wind_direction_deg: float | None
    wave_height_m: float | None
    wave_direction_deg: float | None
    wave_period_s: float | None


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


async def _fetch_open_meteo(
    lat: float, lon: float, hours: int
) -> WeatherForecast | None:
    params_wind = {
        "latitude": lat, "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kn",
        "forecast_hours": hours,
    }
    params_marine = {
        "latitude": lat, "longitude": lon,
        "hourly": "wave_height,wave_direction,wave_period",
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
        )
        for i, t in enumerate(times)
    ]
    return WeatherForecast(latitude=lat, longitude=lon, provider="open-meteo", points=points)


async def _fetch_windy(lat: float, lon: float, hours: int) -> WeatherForecast | None:
    if not settings.windy_api_key:
        return None
    payload = {
        "lat": lat, "lon": lon,
        "model": "gfs",
        "parameters": ["wind", "waves"],
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
    points: list[WeatherPoint] = []
    for i, t in enumerate(times[:hours]):
        u = _safe(wind_u, i) or 0.0
        v = _safe(wind_v, i) or 0.0
        speed_ms = math.sqrt(u * u + v * v)
        speed_kn = speed_ms * 1.9438
        dir_deg = (math.degrees(math.atan2(-u, -v)) + 360) % 360
        iso = _dt.datetime.utcfromtimestamp(t / 1000).isoformat() + "Z"
        points.append(WeatherPoint(
            time=iso,
            wind_speed_kn=round(speed_kn, 1),
            wind_direction_deg=round(dir_deg, 1),
            wave_height_m=_safe(waves_h, i),
            wave_direction_deg=_safe(waves_d, i),
            wave_period_s=_safe(waves_p, i),
        ))
    return WeatherForecast(latitude=lat, longitude=lon, provider="windy", points=points)


def _safe(arr, i):
    try:
        return arr[i] if arr else None
    except (IndexError, TypeError):
        return None


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
    lat: float, lon: float, when, *, window_hours: int = 72,
) -> WeatherPoint | None:
    """Renvoie le point forecast le plus proche d'une datetime cible.

    ``when`` est aware UTC. On charge un forecast couvrant ``window_hours``
    et on pioche l'index dont le timestamp est le plus proche. Utilisé par
    leg_detail (POL @ ETD, POD @ ETA) et next-port (ETA arrivée).
    """
    import datetime as _dt

    fc = await fetch_forecast(lat, lon, hours=window_hours)
    if not fc or not fc.points:
        return None
    target = when.replace(tzinfo=_dt.timezone.utc) if when.tzinfo is None else when
    best: WeatherPoint | None = None
    best_delta = float("inf")
    for p in fc.points:
        try:
            t = _dt.datetime.fromisoformat(p.time.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=_dt.timezone.utc)
        except (ValueError, AttributeError):
            continue
        delta = abs((t - target).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best = p
    return best


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


def _compass(deg: float) -> str:
    """Convertit un cap décimal en rose 16 directions (N/NNE/NE/...)."""
    dirs = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    idx = int(((deg % 360) + 11.25) // 22.5) % 16
    return dirs[idx]
