"""Messagerie client ↔ équipe, rattachée à une réservation.

Distincte de ``PortalMessage`` (liée à une packing list du portail token)
et de ``OnboardMessage`` (équipage). Ici le fil est rattaché au
``Booking`` de l'espace client authentifié.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BookingMessage(Base):
    __tablename__ = "booking_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender: Mapped[str] = mapped_column(String(20), nullable=False)  # 'client' | 'staff'
    sender_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
