"""Voyage segment (leg) — backbone of the planning and booking system."""
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
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Leg(Base):
    __tablename__ = "legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id"), nullable=False, index=True
    )
    departure_port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False)
    arrival_port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False)

    etd_ref: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eta_ref: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    etd: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    atd: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ata: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)

    # Booking platform fields
    is_bookable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    public_capacity_palettes: Mapped[int | None] = mapped_column(Integer)
    public_price_per_palette_eur: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    booking_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booking_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optional per-leg overrides for ETA computation. NULL => use the
    # vessel default (vessel.default_speed_kn / vessel.default_elongation).
    transit_speed_kn: Mapped[float | None] = mapped_column()
    elongation_coef: Mapped[float | None] = mapped_column()

    # Durée d'escale planifiée à l'arrivée (heures). Sert au planning :
    # le leg suivant du même navire commence après ETA + port_stay_planned_hours.
    port_stay_planned_hours: Mapped[int | None] = mapped_column(Integer)

    # Distance orthodromique POL→POD (milles nautiques). Calculée par
    # haversine et persistée pour alimenter le label Anemos (CO₂ évité).
    distance_nm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    # Voyage closure workflow (submitted by captain → reviewed by ops → approved by manager)
    closure_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_submitted_by: Mapped[str | None] = mapped_column(String(100))
    closure_reviewed_by: Mapped[str | None] = mapped_column(String(100))
    closure_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_legs_etd", "etd"),
        Index("ix_legs_status", "status"),
        Index("ix_legs_bookable", "is_bookable"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Leg {self.leg_code} {self.etd.date()}→{self.eta.date()}>"
