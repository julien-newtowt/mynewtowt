"""EU MRV emissions tracking."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
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
    # MRV-04 (A1 hybride) — relevés des 4 compteurs DO (lecture cumulée du
    # navire). La consommation ME/AE est dérivée des deltas entre événements
    # consécutifs d'un même leg (cf. services.mrv_compute).
    port_me_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    stbd_me_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fwd_gen_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    aft_gen_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    bunkering_qty_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    # Valeurs calculées (chaînées par leg).
    me_consumption_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    ae_consumption_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    total_consumption_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    rob_calculated_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    # MRV-07 — position en DMS (exigée par l'export DNV).
    lat_deg: Mapped[int | None] = mapped_column(Integer)
    lat_min: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    lat_ns: Mapped[str | None] = mapped_column(CHAR(1))
    lon_deg: Mapped[int | None] = mapped_column(Integer)
    lon_min: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    lon_ew: Mapped[str | None] = mapped_column(CHAR(1))
    # MRV-05 — contrôle qualité : 'ok' | 'warning' | 'error' (bloquant export).
    quality_status: Mapped[str | None] = mapped_column(String(20))
    quality_notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(100))
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
