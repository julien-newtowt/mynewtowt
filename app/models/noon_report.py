"""Noon reports — captain's daily 24h navigation snapshot.

Saisi quotidiennement à 12:00 UTC bord. Couvre la traversée précédente
(distance 24h, fuel, météo, mer). Sert au reporting MRV et aux KPI.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Moteurs standard du formulaire officiel TOWT (CFOTE_05) — ordre figé.
NOON_ENGINES: tuple[str, ...] = (
    "Port Main Engine",
    "Starboard Main Engine",
    "FWD Generator",
    "AFT Generator",
    "Port Shaft Generator",
    "Starboard Shaft Generator",
)

# Créneaux horaires (4 h) des relevés météo / voilure du noon report.
NOON_TIME_SLOTS: tuple[str, ...] = ("16:00", "20:00", "00:00", "04:00", "08:00", "12:00")

# Listes de validation du formulaire officiel (onglet « Data »).
NOON_REPORT_TYPES: tuple[str, ...] = ("Noon report", "Arrival Report", "Departure report")
NOON_VESSEL_CONDITIONS: tuple[str, ...] = ("Laden", "Ballast", "Partly laden")

# Emplacements des relevés température/humidité des cales (CFOTE_05) — ordre
# figé du formulaire officiel (cellier + 6 cales FWD/Aft).
NOON_HOLD_LOCATIONS: tuple[str, ...] = (
    "Cellar",
    "Upper FWD hold",
    "Middle FWD hold",
    "Lower FWD hold",
    "Upper Aft hold",
    "Middle Aft hold",
    "Lower Aft hold",
)


class NoonReport(Base):
    __tablename__ = "noon_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    sog_avg: Mapped[float | None] = mapped_column(Float)  # SOG moyen 24h
    sog_max: Mapped[float | None] = mapped_column(Float, comment="SOG maximum 24h")
    propulsion_mode: Mapped[str | None] = mapped_column(String(20), comment="Mode de propulsion: sail/assisted/motor")
    cog_avg: Mapped[float | None] = mapped_column(Float)  # COG moyen 24h
    wind_speed_kn: Mapped[float | None] = mapped_column(Float)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float)
    sea_state_bf: Mapped[int | None] = mapped_column(Integer)  # Beaufort 0-12
    visibility_nm: Mapped[float | None] = mapped_column(Float)
    barometric_hpa: Mapped[float | None] = mapped_column(Float)

    fuel_consumed_24h_l: Mapped[float | None] = mapped_column(Float)
    distance_24h_nm: Mapped[float | None] = mapped_column(Float)
    rob_fuel_l: Mapped[float | None] = mapped_column(Float)  # Remaining On Board

    # ── Alignement formulaire officiel TOWT (CFOTE_05) ──────────────────────
    # En-tête / voyage
    report_type: Mapped[str | None] = mapped_column(String(30))  # cf. NOON_REPORT_TYPES
    previous_port: Mapped[str | None] = mapped_column(String(10))  # UNCODE
    next_port: Mapped[str | None] = mapped_column(String(10))  # UNCODE
    vessel_condition: Mapped[str | None] = mapped_column(String(20))  # cf. NOON_VESSEL_CONDITIONS
    deadweight_t: Mapped[float | None] = mapped_column(Float)
    draft_fwd_m: Mapped[float | None] = mapped_column(Float)
    draft_aft_m: Mapped[float | None] = mapped_column(Float)
    trim_m: Mapped[float | None] = mapped_column(Float)
    # Depuis le dernier report / depuis SOSP (Start Of Sea Passage)
    time_since_last_h: Mapped[float | None] = mapped_column(Float)
    distance_since_last_nm: Mapped[float | None] = mapped_column(Float)
    speed_since_last_kn: Mapped[float | None] = mapped_column(Float)
    time_since_sosp_h: Mapped[float | None] = mapped_column(Float)
    distance_since_sosp_nm: Mapped[float | None] = mapped_column(Float)
    speed_since_sosp_kn: Mapped[float | None] = mapped_column(Float)
    distance_to_go_nm: Mapped[float | None] = mapped_column(Float)
    # ETA / ETB
    announced_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etb: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_70_kt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_75_kt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_80_kt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_85_kt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta_90_kt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Conso / ROB (le formulaire officiel exprime le fuel en tonnes)
    total_consumption_t: Mapped[float | None] = mapped_column(Float)
    go_density: Mapped[float | None] = mapped_column(Float)  # t/m³
    rob_do_t: Mapped[float | None] = mapped_column(Float)
    rob_uree_t: Mapped[float | None] = mapped_column(Float)
    rob_fw_t: Mapped[float | None] = mapped_column(Float)
    production_fw_t: Mapped[float | None] = mapped_column(Float)

    remarks: Mapped[str | None] = mapped_column(Text)

    # Dédoublonnage PWA offline — UUID généré côté navigateur (onboard-offline.js)
    client_uuid: Mapped[str | None] = mapped_column(String(36), unique=True)

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

    engines: Mapped[list[NoonReportEngine]] = relationship(
        back_populates="noon_report", cascade="all, delete-orphan", order_by="NoonReportEngine.id"
    )
    weather_rows: Mapped[list[NoonReportWeather]] = relationship(
        back_populates="noon_report", cascade="all, delete-orphan", order_by="NoonReportWeather.id"
    )
    sail_rows: Mapped[list[NoonReportSail]] = relationship(
        back_populates="noon_report", cascade="all, delete-orphan", order_by="NoonReportSail.id"
    )
    hold_rows: Mapped[list[NoonReportHold]] = relationship(
        back_populates="noon_report", cascade="all, delete-orphan", order_by="NoonReportHold.id"
    )


class NoonReportEngine(Base):
    """Relevé machine par moteur d'un noon report (running hours + conso DO)."""

    __tablename__ = "noon_report_engines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    noon_report_id: Mapped[int] = mapped_column(
        ForeignKey("noon_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine: Mapped[str] = mapped_column(String(40), nullable=False)  # cf. NOON_ENGINES
    running_hours_h: Mapped[float | None] = mapped_column(Float)
    do_consumption_t: Mapped[float | None] = mapped_column(Float)
    running_hours_d: Mapped[float | None] = mapped_column(Float)  # compteur jour J
    running_hours_d1: Mapped[float | None] = mapped_column(Float)  # compteur jour J-1

    noon_report: Mapped[NoonReport] = relationship(back_populates="engines")


class NoonReportWeather(Base):
    """Relevé météo horaire (4 h) d'un noon report."""

    __tablename__ = "noon_report_weather"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    noon_report_id: Mapped[int] = mapped_column(
        ForeignKey("noon_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot_time: Mapped[str | None] = mapped_column(String(5))  # cf. NOON_TIME_SLOTS
    tws_kn: Mapped[float | None] = mapped_column(Float)  # True Wind Speed
    awa_deg: Mapped[float | None] = mapped_column(Float)  # Apparent Wind Angle
    aws_kn: Mapped[float | None] = mapped_column(Float)  # Apparent Wind Speed
    sea_state: Mapped[int | None] = mapped_column(Integer)
    sea_direction_deg: Mapped[float | None] = mapped_column(Float)
    ship_speed_kn: Mapped[float | None] = mapped_column(Float)

    noon_report: Mapped[NoonReport] = relationship(back_populates="weather_rows")


class NoonReportSail(Base):
    """Relevé voilure horaire (4 h) d'un noon report (ON/OFF + charge ME)."""

    __tablename__ = "noon_report_sails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    noon_report_id: Mapped[int] = mapped_column(
        ForeignKey("noon_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot_time: Mapped[str | None] = mapped_column(String(5))  # cf. NOON_TIME_SLOTS
    j0: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fwd_j1: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fwd_ms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    aft_j1: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    aft_ms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sail_boost: Mapped[float | None] = mapped_column(Float)
    me_ps_load_pct: Mapped[float | None] = mapped_column(Float)  # ME port-side load %
    me_sb_load_pct: Mapped[float | None] = mapped_column(Float)  # ME starboard load %

    noon_report: Mapped[NoonReport] = relationship(back_populates="sail_rows")


class NoonReportHold(Base):
    """Relevé température / humidité d'une cale (minuit & midi).

    Reprend la section « Hold conditions » du formulaire officiel TOWT
    (CFOTE_05) : cellier + cales FWD/Aft, température (°C) et humidité
    relative (%) relevées à minuit et à midi.
    """

    __tablename__ = "noon_report_holds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    noon_report_id: Mapped[int] = mapped_column(
        ForeignKey("noon_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(String(40), nullable=False)  # cf. NOON_HOLD_LOCATIONS
    temp_midnight_c: Mapped[float | None] = mapped_column(Float)
    humidity_midnight_pct: Mapped[float | None] = mapped_column(Float)
    temp_midday_c: Mapped[float | None] = mapped_column(Float)
    humidity_midday_pct: Mapped[float | None] = mapped_column(Float)

    noon_report: Mapped[NoonReport] = relationship(back_populates="hold_rows")
