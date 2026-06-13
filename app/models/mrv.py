"""EU MRV emissions tracking."""

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
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MRVEvent(Base):
    __tablename__ = "mrv_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    event_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # 'bunkering', 'noon_consumption', 'arrival_rob', 'departure_rob'
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fuel_type: Mapped[str] = mapped_column(String(20), default="MDO", nullable=False)
    fuel_volume_l: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fuel_mass_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    rob_l: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    distance_nm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    time_at_sea_h: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    cargo_carried_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    # FLX-03 — liens vers la source du bord (noon report = référence n°1,
    # SOF mappé via SOF_TO_MRV_MAP). Uniques → sync idempotente.
    noon_report_id: Mapped[int | None] = mapped_column(ForeignKey("noon_reports.id"), unique=True)
    sof_event_id: Mapped[int | None] = mapped_column(ForeignKey("sof_events.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MRVParameter(Base):
    __tablename__ = "mrv_parameters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
