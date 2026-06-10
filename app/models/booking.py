"""Booking — the platform's flagship new feature.

Workflow status:
  draft → submitted → confirmed → loaded → at_sea → discharged → delivered
  draft → cancelled
  submitted → cancelled (free until X days)
  confirmed → cancelled (with cancellation fee)
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)

    client_account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), nullable=False, index=True
    )
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    total_palettes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    total_cubage_m3: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=0, nullable=False)
    hazardous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    oversize: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    estimated_price_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    confirmed_price_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    pickup_address: Mapped[str | None] = mapped_column(Text)
    delivery_address: Mapped[str | None] = mapped_column(Text)
    shipper_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    signed_terms_version: Mapped[str | None] = mapped_column(String(20))
    signed_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at_sea_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discharged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    items: Mapped[list["BookingItem"]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        # ``selectin`` : eager-load une seule query supplémentaire pour
        # tous les items du booking dès qu'on charge le booking. Évite
        # le MissingGreenlet typique d'un lazy-load implicite dans un
        # template async (cf. /me/bookings/{ref} 500 — V3.5).
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_bookings_status", "status"),
        Index("ix_bookings_client_status", "client_account_id", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Booking {self.reference} status={self.status}>"


class BookingItem(Base):
    __tablename__ = "booking_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    pallet_format: Mapped[str] = mapped_column(String(20), nullable=False)
    pallet_count: Mapped[int] = mapped_column(Integer, nullable=False)

    cargo_description: Mapped[str] = mapped_column(String(500), nullable=False)
    unit_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    stackable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    hazardous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    imdg_class: Mapped[str | None] = mapped_column(String(20))
    un_number: Mapped[str | None] = mapped_column(String(10))

    hs_code: Mapped[str | None] = mapped_column(String(20))
    temperature_min: Mapped[int | None] = mapped_column(Integer)
    temperature_max: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    booking: Mapped[Booking] = relationship(back_populates="items")
