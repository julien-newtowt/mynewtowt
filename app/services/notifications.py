"""Service notifications — création + lecture + archive.

Reprise V3.0.0 :
- notify_cargo_progress, notify_order_confirmed, notify_eosp/sosp
- notify_claim, notify_eta_shift

Le user-cible peut être nominatif (target_user_id) ou par rôle (target_role).
Le dashboard charge les notifications "actives" (is_archived=False) pour
l'utilisateur courant ou son rôle.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NOTIFICATION_TYPES, Notification


async def create(
    db: AsyncSession,
    *,
    type: str,
    title: str,
    detail: str | None = None,
    link: str | None = None,
    target_user_id: int | None = None,
    target_role: str | None = None,
    target_client_id: int | None = None,
) -> Notification:
    if type not in NOTIFICATION_TYPES:
        raise ValueError(f"unknown notification type: {type}")
    n = Notification(
        type=type, title=title, detail=detail, link=link,
        target_user_id=target_user_id, target_role=target_role,
        target_client_id=target_client_id,
    )
    db.add(n)
    await db.flush()
    return n


async def list_for(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    user_role: str | None = None,
    client_id: int | None = None,
    include_archived: bool = False,
    limit: int = 50,
) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.created_at.desc())
    if not include_archived:
        stmt = stmt.where(Notification.is_archived.is_(False))
    conds = []
    if user_id is not None:
        conds.append(Notification.target_user_id == user_id)
    if user_role is not None:
        conds.append(Notification.target_role == user_role)
    if client_id is not None:
        conds.append(Notification.target_client_id == client_id)
    if conds:
        stmt = stmt.where(or_(*conds))
    stmt = stmt.limit(max(5, min(limit, 200)))
    return list((await db.execute(stmt)).scalars().all())


async def count_unread(
    db: AsyncSession, *, user_id: int | None = None, user_role: str | None = None,
    client_id: int | None = None,
) -> int:
    from sqlalchemy import func
    stmt = select(func.count(Notification.id)).where(Notification.is_read.is_(False)).where(
        Notification.is_archived.is_(False),
    )
    conds = []
    if user_id is not None:
        conds.append(Notification.target_user_id == user_id)
    if user_role is not None:
        conds.append(Notification.target_role == user_role)
    if client_id is not None:
        conds.append(Notification.target_client_id == client_id)
    if conds:
        stmt = stmt.where(or_(*conds))
    return int((await db.scalar(stmt)) or 0)


async def mark_read(db: AsyncSession, notif: Notification) -> None:
    notif.is_read = True
    await db.flush()


async def archive(db: AsyncSession, notif: Notification) -> None:
    notif.is_archived = True
    notif.is_read = True
    await db.flush()


# ────────────── Convenience helpers — called from business routers ──────────

async def notify_new_order(db: AsyncSession, order_reference: str, order_id: int) -> Notification:
    return await create(
        db, type="new_order",
        title=f"Nouvelle commande {order_reference}",
        link=f"/commercial/orders/{order_id}",
        target_role="commercial",
    )


async def notify_new_cargo_message(db: AsyncSession, packing_list_id: int, sender_name: str) -> Notification:
    return await create(
        db, type="new_cargo_message",
        title=f"Nouveau message client ({sender_name})",
        link=f"/cargo/packing-lists/{packing_list_id}",
        target_role="operation",
    )


async def notify_eosp(db: AsyncSession, leg_code: str, leg_id: int) -> Notification:
    return await create(
        db, type="eosp",
        title=f"Fin de navigation — {leg_code}",
        link=f"/captain?leg_id={leg_id}",
        target_role="operation",
    )


async def notify_sosp(db: AsyncSession, leg_code: str, leg_id: int) -> Notification:
    return await create(
        db, type="sosp",
        title=f"Début de navigation — {leg_code}",
        link=f"/captain?leg_id={leg_id}",
        target_role="operation",
    )


async def notify_new_claim(db: AsyncSession, reference: str, claim_id: int) -> Notification:
    return await create(
        db, type="new_claim",
        title=f"Nouveau claim {reference}",
        link=f"/claims/{claim_id}",
        target_role="manager_maritime",
    )


async def notify_eta_shift(db: AsyncSession, leg_code: str, leg_id: int, reason: str) -> Notification:
    return await create(
        db, type="eta_shift",
        title=f"Décalage ETA — {leg_code}",
        detail=f"Motif : {reason}",
        link=f"/captain?leg_id={leg_id}",
        target_role="commercial",
    )


# ──────────────────────── Notifications côté client (espace /me) ─────────────

async def notify_client(
    db: AsyncSession,
    *,
    client_id: int,
    type: str,
    title: str,
    link: str | None = None,
    detail: str | None = None,
) -> Notification:
    """Crée une notification in-app destinée à un compte client."""
    return await create(
        db, type=type, title=title, detail=detail, link=link,
        target_client_id=client_id,
    )


async def notify_new_booking_message(
    db: AsyncSession, *, booking_reference: str, booking_id: int,
) -> Notification:
    """Alerte le staff (rôle operation) d'un nouveau message client sur un booking."""
    return await create(
        db, type="new_booking_message",
        title=f"Nouveau message client — {booking_reference}",
        link=f"/staff/bookings/{booking_reference}",
        target_role="operation",
    )
