"""Trace réellement parcourue — agrégation des positions satcom par leg / période.

Les positions (`vessel_positions`) ne portent pas de ``leg_id`` : on associe
une position à un leg par **fenêtre temporelle** (même navire + ``recorded_at``
entre le départ et l'arrivée du leg). Ce module centralise :

- la résolution de la fenêtre d'un leg (``leg_window``) ;
- la récupération des positions d'un leg ou d'une période arbitraire ;
- les métriques de navigation (distance réelle, distance théorique, distance
  restante, durée depuis le départ) ;
- le sous-échantillonnage à 30 min pour la météo (1 appel Windy / point).

Lecture seule : aucune écriture en base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.port import Port
from app.services.ports import haversine_nm

# Pas d'échantillon météo plus rapproché que cet intervalle (minutes).
WEATHER_SAMPLE_MINUTES = 30


@dataclass(frozen=True)
class TrackMetrics:
    """Résumé chiffré d'une trace réelle (toutes distances en milles nautiques)."""

    point_count: int
    actual_nm: float  # distance réellement parcourue (somme des segments)
    theoretical_nm: float | None  # distance orthodromique POL→POD
    remaining_nm: float | None  # distance restante (dernier point → POD)
    duration_hours: float | None  # durée écoulée depuis le départ
    avg_speed_kn: float | None  # vitesse moyenne réelle
    is_active: bool  # leg en cours (pas encore arrivé)


def leg_window(leg: Leg, *, now: datetime | None = None) -> tuple[datetime, datetime, bool]:
    """(start, end, is_active) pour un leg.

    - start = ATD si disponible, sinon ETD (départ planifié) ;
    - end   = ATA si arrivé, sinon ``now`` (leg en cours) ;
    - is_active = leg non encore arrivé (ATA absente).
    """
    now = now or datetime.now(UTC)
    start = leg.atd or leg.etd
    is_active = leg.ata is None
    end = leg.ata or now
    # Garde-fou : si l'horloge serveur est en amont du départ planifié.
    if end < start:
        end = start
    return start, end, is_active


async def positions_in_window(
    db: AsyncSession,
    *,
    vessel_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[VesselPosition]:
    """Positions d'un navire dans une fenêtre [start, end], triées chronologiquement."""
    stmt = select(VesselPosition).where(VesselPosition.vessel_id == vessel_id)
    if start is not None:
        stmt = stmt.where(VesselPosition.recorded_at >= start)
    if end is not None:
        stmt = stmt.where(VesselPosition.recorded_at <= end)
    stmt = stmt.order_by(VesselPosition.recorded_at.asc())
    return list((await db.execute(stmt)).scalars().all())


async def positions_for_leg(
    db: AsyncSession, leg: Leg, *, now: datetime | None = None
) -> list[VesselPosition]:
    """Positions satcom rattachées à un leg (même navire, fenêtre départ→arrivée)."""
    start, end, _ = leg_window(leg, now=now)
    return await positions_in_window(db, vessel_id=leg.vessel_id, start=start, end=end)


def actual_distance_nm(positions: list[VesselPosition]) -> float:
    """Distance réellement parcourue = somme des sauts haversine entre points."""
    total = 0.0
    for a, b in pairwise(positions):
        total += haversine_nm(a.latitude, a.longitude, b.latitude, b.longitude)
    return total


def compute_metrics(
    positions: list[VesselPosition],
    leg: Leg,
    *,
    arr_port: Port | None = None,
    now: datetime | None = None,
) -> TrackMetrics:
    """Métriques de navigation d'un leg à partir de ses positions réelles."""
    now = now or datetime.now(UTC)
    start, _end, is_active = leg_window(leg, now=now)

    actual = actual_distance_nm(positions)

    theoretical: float | None = float(leg.distance_nm) if leg.distance_nm is not None else None

    # Distance restante : dernier point connu → port d'arrivée (0 si arrivé).
    remaining: float | None = None
    if leg.ata is not None:
        remaining = 0.0
    elif positions and arr_port and arr_port.latitude is not None and arr_port.longitude is not None:
        last = positions[-1]
        remaining = haversine_nm(
            last.latitude, last.longitude, arr_port.latitude, arr_port.longitude
        )

    # Durée depuis le départ : ATD/ETD → ATA si arrivé, sinon dernier point / maintenant.
    end_ref = leg.ata or (positions[-1].recorded_at if positions else now)
    duration_hours: float | None = None
    if end_ref and start:
        duration_hours = max((end_ref - start).total_seconds() / 3600.0, 0.0)

    avg_speed: float | None = None
    if duration_hours and duration_hours > 0 and actual > 0:
        avg_speed = actual / duration_hours

    return TrackMetrics(
        point_count=len(positions),
        actual_nm=actual,
        theoretical_nm=theoretical,
        remaining_nm=remaining,
        duration_hours=duration_hours,
        avg_speed_kn=avg_speed,
        is_active=is_active,
    )


def downsample_for_weather(
    positions: list[VesselPosition], *, minutes: int = WEATHER_SAMPLE_MINUTES
) -> list[VesselPosition]:
    """Garde au plus un point par intervalle de ``minutes`` (défaut 30 min).

    Le 1er et le dernier point sont toujours conservés. Sert à borner le nombre
    d'appels météo (1 appel Windy par point échantillonné).
    """
    if not positions:
        return []
    kept: list[VesselPosition] = [positions[0]]
    last_kept = positions[0].recorded_at
    threshold = minutes * 60
    for p in positions[1:]:
        if (p.recorded_at - last_kept).total_seconds() >= threshold:
            kept.append(p)
            last_kept = p.recorded_at
    if positions[-1] is not kept[-1]:
        kept.append(positions[-1])
    return kept


def positions_payload(positions: list[VesselPosition]) -> list[dict]:
    """Sérialise des positions pour la carte (lat/lon/temps/SOG/COG)."""
    return [
        {
            "lat": p.latitude,
            "lon": p.longitude,
            "t": p.recorded_at.isoformat(),
            "sog": float(p.sog_kn) if p.sog_kn is not None else None,
            "cog": float(p.cog_deg) if p.cog_deg is not None else None,
        }
        for p in positions
    ]
