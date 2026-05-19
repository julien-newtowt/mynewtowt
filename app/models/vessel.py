"""Cargo sailing vessel — referenced by Leg, Booking and capacity rules."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Vessel(Base):
    __tablename__ = "vessels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(4), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    imo_number: Mapped[str | None] = mapped_column(String(20))
    flag: Mapped[str | None] = mapped_column(String(2))
    dwt: Mapped[float | None] = mapped_column(Float)
    capacity_palettes: Mapped[int] = mapped_column(Integer, default=850, nullable=False)
    default_speed_kn: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    default_elongation: Mapped[float] = mapped_column(Float, default=1.15, nullable=False)
    opex_daily_sea_eur: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Vessel {self.code} {self.name}>"
