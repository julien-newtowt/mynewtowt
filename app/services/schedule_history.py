"""Historisation des recalculs de planification (``schedule_revisions``).

Source unique d'écriture : ``update_leg`` (édition / drag-drop), la cascade
(``date_cascade``) et l'ETA-shift capitaine passent tous par ``record()``.
Lecture via ``list_for_leg`` pour l'écran d'historique
``/planning/legs/{id}/history``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.schedule_revision import ScheduleRevision


async def record(
    db: AsyncSession,
    *,
    leg: Leg,
    old_etd: datetime | None,
    new_etd: datetime | None,
    old_eta: datetime | None,
    new_eta: datetime | None,
    source: str,
    batch_id: str,
    trigger_leg_id: int | None = None,
    reason: str | None = None,
    detail: str | None = None,
    user_id: int | None = None,
    user_name: str | None = None,
) -> ScheduleRevision:
    rev = ScheduleRevision(
        leg_id=leg.id,
        leg_code=leg.leg_code,
        vessel_id=leg.vessel_id,
        source=source,
        batch_id=batch_id,
        trigger_leg_id=trigger_leg_id,
        old_etd=old_etd,
        new_etd=new_etd,
        old_eta=old_eta,
        new_eta=new_eta,
        reason=reason,
        detail=detail,
        user_id=user_id,
        user_name=user_name,
    )
    db.add(rev)
    await db.flush()
    return rev


async def list_for_leg(
    db: AsyncSession, leg_id: int, *, limit: int = 200
) -> list[ScheduleRevision]:
    """Révisions du leg — subies (leg_id) ET déclenchées (trigger_leg_id)."""
    stmt = (
        select(ScheduleRevision)
        .where(
            or_(
                ScheduleRevision.leg_id == leg_id,
                ScheduleRevision.trigger_leg_id == leg_id,
            )
        )
        .order_by(ScheduleRevision.created_at.desc(), ScheduleRevision.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())
