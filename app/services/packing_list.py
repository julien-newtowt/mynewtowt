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

from app.models.booking import Booking
from app.models.leg import Leg
from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PortalAccessLog,
    default_token_expiry,
    generate_token,
)
from app.services.activity import record as activity_record


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


async def create_for_booking(db: AsyncSession, booking: Booking) -> PackingList:
    """Crée (ou retourne) la packing list d'un booking client — rail B.

    Jumeau de la création rail A (``cargo_packing_router.create_for_order``),
    mais rattaché à un ``booking_id`` au lieu d'un ``order_id``. Idempotent :
    si une PL existe déjà pour ce booking, on la retourne sans rien recréer
    (sûr à rappeler à chaque passage en ``confirmed``).

    Le client remplit ses batches via le portail ``/p/{token}`` — on ne crée
    donc que la coquille (token 24 hex / 90 j, statut ``draft``). On préremplit
    ce qui est cheap : ``loading_date`` = ETD du leg (alimente la cascade de
    dates). POL/POD/navire/référence vivent sur le leg et le booking, résolus
    à l'affichage côté portail — pas de colonnes dédiées sur PackingList.
    """
    existing = (
        await db.execute(
            select(PackingList).where(PackingList.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    leg = await db.get(Leg, booking.leg_id) if booking.leg_id else None

    pl = PackingList(
        booking_id=booking.id,
        order_id=None,
        token=generate_token(),
        token_expires_at=default_token_expiry(),
        status="draft",
        loading_date=leg.etd if leg is not None else None,
    )
    db.add(pl)
    await db.flush()

    await activity_record(
        db,
        action="packing_list_created",
        module="cargo",
        entity_type="packing_list",
        entity_id=pl.id,
        entity_label=f"PL for {booking.reference}",
    )
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
