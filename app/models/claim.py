"""Claims (cargo / crew / hull / war risk)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

CLAIM_TYPES = ("cargo", "crew", "hull", "war_risk", "third_party", "other")
CLAIM_STATUSES = ("open", "in_review", "provisioned", "settled", "rejected", "closed")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)

    claim_type: Mapped[str] = mapped_column(String(20), nullable=False)
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"))

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    provision_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    settled_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    insurer: Mapped[str | None] = mapped_column(String(200))
    insurer_claim_ref: Mapped[str | None] = mapped_column(String(80))

    cargo_position: Mapped[str | None] = mapped_column(String(40))  # if cargo claim
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    timeline: Mapped[list[ClaimTimelineEntry]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="ClaimTimelineEntry.at",
    )


class ClaimTimelineEntry(Base):
    __tablename__ = "claim_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    author_name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30), default="note", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    claim: Mapped[Claim] = relationship(back_populates="timeline")


class VesselPosition(Base):
    __tablename__ = "vessel_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    latitude: Mapped[float] = mapped_column()
    longitude: Mapped[float] = mapped_column()
    sog_kn: Mapped[float | None] = mapped_column()
    cog_deg: Mapped[float | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
