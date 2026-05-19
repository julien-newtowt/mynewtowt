"""Noon reports — captain's daily 24h navigation snapshot.

Saisi quotidiennement à 12:00 UTC bord. Couvre la traversée précédente
(distance 24h, fuel, météo, mer). Sert au reporting MRV et aux KPI.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NoonReport(Base):
    __tablename__ = "noon_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    sog_avg: Mapped[float | None] = mapped_column(Float)          # SOG moyen 24h
    cog_avg: Mapped[float | None] = mapped_column(Float)          # COG moyen 24h
    wind_speed_kn: Mapped[float | None] = mapped_column(Float)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float)
    sea_state_bf: Mapped[int | None] = mapped_column(Integer)     # Beaufort 0-12
    visibility_nm: Mapped[float | None] = mapped_column(Float)
    barometric_hpa: Mapped[float | None] = mapped_column(Float)

    fuel_consumed_24h_l: Mapped[float | None] = mapped_column(Float)
    distance_24h_nm: Mapped[float | None] = mapped_column(Float)
    rob_fuel_l: Mapped[float | None] = mapped_column(Float)       # Remaining On Board

    remarks: Mapped[str | None] = mapped_column(Text)

    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Signature commandant — rend le noon report immuable
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signed_by_name: Mapped[str | None] = mapped_column(String(200))
    signature_hash: Mapped[str | None] = mapped_column(String(64))
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
