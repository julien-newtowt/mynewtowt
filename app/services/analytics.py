"""Instrumentation du tunnel de conversion (CONV-06) — service léger.

`record(db, event, ...)` pose un :class:`AnalyticsEvent` best-effort : un échec
ne doit JAMAIS casser la requête métier qui l'a déclenché. Pas d'outil tiers —
les événements sont exploités par le tableau de bord commercial.

Cibles produit (cf. fiche /devis + wizard §5) :
  - conversion `landing → booking` ≥ 5 %
  - taux `quote → booking` suivi
  - délai `submitted → confirmed` < 4 h
  - % self-service ≥ 30 % à 6 mois
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics_event import ANALYTICS_EVENTS, AnalyticsEvent

logger = logging.getLogger("analytics")


async def record(
    db: AsyncSession,
    event: str,
    *,
    reference: str | None = None,
    lang: str | None = None,
    channel: str | None = None,
    detail: str | None = None,
) -> None:
    """Enregistre un événement de tunnel. Best-effort : avale toute exception."""
    if event not in ANALYTICS_EVENTS:
        logger.warning("événement analytics inconnu ignoré: %r", event)
        return
    try:
        db.add(
            AnalyticsEvent(
                event=event,
                reference=(reference or None),
                lang=(lang or None),
                channel=(channel or None),
                detail=(detail[:200] if detail else None),
            )
        )
        await db.flush()
    except Exception:  # pragma: no cover - best-effort
        logger.warning("analytics.record(%s) a échoué", event, exc_info=True)


async def counts_since(db: AsyncSession, since) -> dict[str, int]:
    """Compte les événements par type depuis ``since`` (datetime aware)."""
    rows = (
        await db.execute(
            select(AnalyticsEvent.event, func.count(AnalyticsEvent.id))
            .where(AnalyticsEvent.created_at >= since)
            .group_by(AnalyticsEvent.event)
        )
    ).all()
    counts = dict.fromkeys(ANALYTICS_EVENTS, 0)
    for ev, n in rows:
        counts[ev] = n
    return counts
