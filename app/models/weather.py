"""Historique météo au point GPS — snapshots Windy (cron 30 min).

Toutes les 30 min, un job (POST /api/weather/refresh, cron Power Automate)
relève la météo Windy au **dernier point GPS connu** de chaque navire et
persiste ici une observation. Constituer cette série dans le temps permet de
retrouver les conditions le long d'un trajet **après coup** — y compris pour
les legs déjà réalisés (Windy ne fournissant pas d'archive historique).

Une observation est rattachée à une position par couple (vessel_id,
recorded_at), où ``recorded_at`` est l'horodatage du point GPS observé →
idempotence : un même point n'est historisé qu'une fois.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VesselWeather(Base):
    __tablename__ = "vessel_weather"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False, index=True)
    # Horodatage du point GPS observé (= VesselPosition.recorded_at).
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    wind_speed_kn: Mapped[float | None] = mapped_column(Float)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float)
    current_speed_kn: Mapped[float | None] = mapped_column(Float)
    current_direction_deg: Mapped[float | None] = mapped_column(Float)
    wave_height_m: Mapped[float | None] = mapped_column(Float)
    wave_direction_deg: Mapped[float | None] = mapped_column(Float)
    wave_period_s: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    # V3.9 — bloc « conditions actuelles » : pression, visibilité, humidité, nébulosité.
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    visibility_km: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    cloud_cover_pct: Mapped[float | None] = mapped_column(Float)

    provider: Mapped[str] = mapped_column(String(20), default="windy", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("vessel_id", "recorded_at", name="uq_vessel_weather_vessel_time"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VesselWeather v{self.vessel_id} @{self.recorded_at:%Y-%m-%d %H:%M}>"
