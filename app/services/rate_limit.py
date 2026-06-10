"""Persistent rate-limiter — backed by ``rate_limit_attempts``.

Usage :

    if await rate_limit.exceeded(db, scope="client_login", identifier=ip,
                                 max_attempts=10, window_minutes=10):
        raise HTTPException(429, "Too many attempts")

    await rate_limit.record(db, scope="client_login", identifier=ip)

`scope` est un libellé court (``client_login``, ``portal_access``…),
``identifier`` est typiquement une IP ou un email *normalisé*. Pour ne
pas stocker de PII en clair, ``identifier`` est hashé SHA-256 avant
d'être inséré (on garde quand même un index sur le hash).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rate_limit import RateLimitAttempt


def _hash(identifier: str) -> str:
    return hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()[:48]


async def exceeded(
    db: AsyncSession,
    *,
    scope: str,
    identifier: str,
    max_attempts: int = 10,
    window_minutes: int = 10,
) -> bool:
    """Renvoie True si ``identifier`` a dépassé ``max_attempts`` dans la fenêtre."""
    if not identifier:
        return False
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    h = _hash(identifier)
    stmt = (
        select(func.count(RateLimitAttempt.id))
        .where(RateLimitAttempt.scope == scope)
        .where(RateLimitAttempt.identifier == h)
        .where(RateLimitAttempt.attempted_at >= since)
    )
    count = int((await db.scalar(stmt)) or 0)
    return count >= max_attempts


async def record(
    db: AsyncSession,
    *,
    scope: str,
    identifier: str,
) -> None:
    """Enregistre une tentative — l'identifiant est hashé pour éviter de stocker la PII."""
    if not identifier:
        return
    db.add(RateLimitAttempt(scope=scope, identifier=_hash(identifier)))
    await db.flush()


async def purge_older_than(
    db: AsyncSession, *, days: int = 30,
) -> int:
    """Tâche de maintenance — supprime les attempts anciens."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    res = await db.execute(
        delete(RateLimitAttempt).where(RateLimitAttempt.attempted_at < cutoff)
    )
    return res.rowcount or 0
