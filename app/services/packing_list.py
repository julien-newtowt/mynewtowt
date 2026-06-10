"""Packing list — service métier (audit, lock, completion, token resolution).

Convention :
- Le token est stocké en clair dans `packing_lists.token` mais SHA-256
  côté `portal_access_logs.token_hash` (pas de fuite dans les logs).
- 90 jours de validité (`default_token_expiry`). Le router public renvoie
  410 GONE quand l'expiration est dépassée.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PortalAccessLog,
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_by_token(db: AsyncSession, token: str) -> PackingList | None:
    if not token:
        return None
    pl = (
        await db.execute(select(PackingList).where(PackingList.token == token))
    ).scalar_one_or_none()
    if pl is None:
        return None
    if pl.token_expires_at is not None and pl.token_expires_at < datetime.now(UTC):
        return None
    return pl


async def log_portal_access(
    db: AsyncSession,
    *,
    token: str,
    packing_list_id: int | None,
    ip_address: str | None,
    user_agent: str | None,
    path: str | None,
) -> None:
    db.add(
        PortalAccessLog(
            portal_type="cargo",
            token_hash=hash_token(token),
            packing_list_id=packing_list_id,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:400],
            path=(path or "")[:200],
        )
    )
    await db.flush()


async def record_audit(
    db: AsyncSession,
    *,
    packing_list_id: int,
    batch_id: int | None,
    actor: str,
    actor_name: str | None,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> None:
    if old_value == new_value:
        return
    db.add(
        PackingListAudit(
            packing_list_id=packing_list_id,
            batch_id=batch_id,
            actor=actor,
            actor_name=actor_name,
            field=field,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
        )
    )
    await db.flush()


def can_modify(pl: PackingList) -> bool:
    return pl.status != "locked"


async def lock(db: AsyncSession, pl: PackingList, *, locked_by: str) -> PackingList:
    pl.status = "locked"
    pl.locked_at = datetime.now(UTC)
    pl.locked_by = locked_by
    await db.flush()
    return pl


async def unlock(db: AsyncSession, pl: PackingList) -> PackingList:
    pl.status = "submitted" if pl.batches else "draft"
    pl.locked_at = None
    pl.locked_by = None
    await db.flush()
    return pl
