"""Historisation météo — snapshot Windy du dernier point GPS de chaque navire.

Appelé toutes les 30 min (POST /api/weather/refresh, cron Power Automate) :
pour chaque navire, on récupère sa dernière position satcom et on persiste la
météo (Windy + complément Open-Meteo) à ce point dans ``vessel_weather``.

Idempotent : une observation par (vessel_id, recorded_at du point GPS). Si le
dernier point n'a pas changé depuis le dernier passage, on ne réécrit pas.

Lecture : ``observations_for_leg`` / ``observations_in_window`` alimentent la
page Performance › Navigation — y compris pour des legs déjà réalisés, dont la
météo a été capturée en direct pendant le voyage.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.models.weather import VesselWeather
from app.services import weather as wx
from app.services.voyage_track import leg_window

logger = logging.getLogger("weather")


def active_provider() -> str:
    """``windy`` si la clé est configurée, sinon repli ``open-meteo``."""
    return "windy" if settings.windy_api_key else "open-meteo"


async def _latest_position(db: AsyncSession, vessel_id: int) -> VesselPosition | None:
    return (
        await db.execute(
            select(VesselPosition)
            .where(VesselPosition.vessel_id == vessel_id)
            .order_by(VesselPosition.recorded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def snapshot_latest(db: AsyncSession) -> dict:
    """Historise la météo au dernier point GPS connu de chaque navire.

    Renvoie un récapitulatif ``{saved, skipped, errors, provider, details}``.
    """
    provider = active_provider()
    vessels = list((await db.execute(select(Vessel))).scalars().all())

    saved = 0
    skipped = 0
    errors = 0
    details: list[str] = []

    for v in vessels:
        last = await _latest_position(db, v.id)
        if last is None:
            skipped += 1
            continue

        # Idempotence : ce point GPS est-il déjà historisé ?
        existing = (
            await db.execute(
                select(VesselWeather.id)
                .where(VesselWeather.vessel_id == v.id)
                .where(VesselWeather.recorded_at == last.recorded_at)
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue

        try:
            wp = await wx.fetch_point_conditions(
                last.latitude, last.longitude, last.recorded_at, provider=provider
            )
        except Exception as e:  # un navire en échec ne doit pas bloquer les autres
            errors += 1
            if len(details) < 10:
                details.append(f"{v.code}: fetch failed ({e})")
            continue

        if wp is None:
            errors += 1
            if len(details) < 10:
                details.append(f"{v.code}: aucune donnée météo")
            continue

        db.add(
            VesselWeather(
                vessel_id=v.id,
                recorded_at=last.recorded_at,
                latitude=last.latitude,
                longitude=last.longitude,
                wind_speed_kn=wp.wind_speed_kn,
                wind_direction_deg=wp.wind_direction_deg,
                current_speed_kn=wp.current_speed_kn,
                current_direction_deg=wp.current_direction_deg,
                wave_height_m=wp.wave_height_m,
                wave_direction_deg=wp.wave_direction_deg,
                wave_period_s=wp.wave_period_s,
                temperature_c=wp.temperature_c,
                provider=wp_provider(provider),
            )
        )
        saved += 1

    await db.flush()
    result = {
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "provider": provider,
        "details": details,
    }
    logger.info("Weather snapshot: %s", result)
    return result


def wp_provider(default: str) -> str:
    """Borne la longueur du provider (colonne String(20))."""
    return (default or "windy")[:20]


async def observations_in_window(
    db: AsyncSession,
    *,
    vessel_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[VesselWeather]:
    """Observations météo historisées d'un navire dans une fenêtre, triées."""
    stmt = select(VesselWeather).where(VesselWeather.vessel_id == vessel_id)
    if start is not None:
        stmt = stmt.where(VesselWeather.recorded_at >= start)
    if end is not None:
        stmt = stmt.where(VesselWeather.recorded_at <= end)
    stmt = stmt.order_by(VesselWeather.recorded_at.asc())
    return list((await db.execute(stmt)).scalars().all())


async def observations_for_leg(
    db: AsyncSession, leg: Leg, *, now: datetime | None = None
) -> list[VesselWeather]:
    """Observations météo historisées rattachées à un leg (fenêtre départ→arrivée)."""
    start, end, _ = leg_window(leg, now=now)
    return await observations_in_window(db, vessel_id=leg.vessel_id, start=start, end=end)
