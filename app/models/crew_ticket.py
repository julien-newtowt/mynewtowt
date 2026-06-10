"""Billets transport équipage (avion, train, taxi…)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


TRANSPORT_MODES = ("flight", "train", "bus", "taxi", "ferry", "car", "other")


class CrewTicket(Base):
    __tablename__ = "crew_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crew_member_id: Mapped[int] = mapped_column(
        ForeignKey("crew_members.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("crew_assignments.id"))
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"))
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    carrier: Mapped[str | None] = mapped_column(String(100))
    departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    departure_location: Mapped[str | None] = mapped_column(String(200))
    arrival_location: Mapped[str | None] = mapped_column(String(200))
    cost_eur: Mapped[float | None] = mapped_column()
    file_path: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
