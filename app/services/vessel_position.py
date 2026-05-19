"""Helpers position navire — lecture de la dernière position satcom.

Power Automate alimente ``vessel_positions`` ~ toutes les heures via
``POST /api/tracking/upload``. Ces helpers permettent aux écrans
commandant (noon report, prochaine escale) de pré-remplir les saisies
manuelles avec la position la plus récente.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import VesselPosition


async def get_latest_position(
    db: AsyncSession, vessel_id: int, *, max_age_hours: int = 6,
) -> VesselPosition | None:
    """Renvoie la dernière position connue d'un navire, ``None`` si trop vieille.

    Au-delà de ``max_age_hours`` (default 6h) on considère que pré-remplir
    serait trompeur (le navire a peut-être bougé de ~120 NM en 6h). Côté
    UI on affiche alors juste "Position satcom indisponible".
    """
    if vessel_id is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    stmt = (
        select(VesselPosition)
        .where(VesselPosition.vessel_id == vessel_id)
        .where(VesselPosition.recorded_at >= cutoff)
        .order_by(VesselPosition.recorded_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
