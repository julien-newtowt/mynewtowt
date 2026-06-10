"""Service messagerie booking — fil client ↔ équipe."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking_message import BookingMessage


async def post(
    db: AsyncSession, *, booking_id: int, sender: str, sender_name: str | None, body: str,
) -> BookingMessage:
    msg = BookingMessage(
        booking_id=booking_id, sender=sender, sender_name=sender_name, body=body.strip(),
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
