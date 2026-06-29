"""Service messagerie booking — fil client ↔ équipe."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking_message import BookingMessage
from app.models.packing_list import PortalMessage


async def post(
    db: AsyncSession,
    *,
    booking_id: int,
    sender: str,
    sender_name: str | None,
    body: str,
) -> BookingMessage:
    msg = BookingMessage(
        booking_id=booking_id,
        sender=sender,
        sender_name=sender_name,
        body=body.strip(),
    )
    db.add(msg)
    await db.flush()
    return msg


async def list_for_booking(db: AsyncSession, booking_id: int) -> list[BookingMessage]:
    res = await db.execute(
        select(BookingMessage)
        .where(BookingMessage.booking_id == booking_id)
        .order_by(BookingMessage.created_at.asc())
    )
    return list(res.scalars().all())


async def mark_thread_read(db: AsyncSession, booking_id: int, *, reader: str) -> None:
    """Marque lus les messages NON envoyés par ``reader`` (client|staff)."""
    other = "staff" if reader == "client" else "client"
    await db.execute(
        update(BookingMessage)
        .where(BookingMessage.booking_id == booking_id)
        .where(BookingMessage.sender == other)
        .where(BookingMessage.is_read.is_(False))
        .values(is_read=True)
    )
    await db.flush()


# ─────────────────────────── CARGO-14 — messagerie portail expéditeur ───────────────────────────
async def mark_portal_read(db: AsyncSession, packing_list_id: int, *, reader: str) -> None:
    """Marque lus les ``PortalMessage`` NON envoyés par ``reader`` (client|staff).

    Appelé à la consultation : le staff lit les messages du client, le portail
    expéditeur lit les messages du staff.
    """
    other = "staff" if reader == "client" else "client"
    await db.execute(
        update(PortalMessage)
        .where(PortalMessage.packing_list_id == packing_list_id)
        .where(PortalMessage.sender == other)
        .where(PortalMessage.is_read.is_(False))
        .values(is_read=True)
    )
    await db.flush()


async def portal_unread_counts(
    db: AsyncSession, packing_list_ids: list[int], *, reader: str
) -> dict[int, int]:
    """Nombre de ``PortalMessage`` non lus (envoyés par l'autre partie) par PL."""
    if not packing_list_ids:
        return {}
    other = "staff" if reader == "client" else "client"
    rows = await db.execute(
        select(PortalMessage.packing_list_id, func.count())
        .where(PortalMessage.packing_list_id.in_(packing_list_ids))
        .where(PortalMessage.sender == other)
        .where(PortalMessage.is_read.is_(False))
        .group_by(PortalMessage.packing_list_id)
    )
    return dict(rows.all())
