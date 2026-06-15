"""Finance: LegFinance, OPEX parameters, port configs, KPI."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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


class LegFinance(Base):
    __tablename__ = "leg_finances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, unique=True)

    revenue_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    port_fees_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    docker_costs_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    opex_share_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    # FLX-09 — coût des sinistres affectés au leg (Σ règlement sinon provision).
    claims_cost_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    other_costs_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    margin_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OpexParameter(Base):
    __tablename__ = "opex_parameters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parameter_name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    parameter_value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PortConfig(Base):
    """Configuration opérationnelle d'un port — fees + contacts agent/pilote.

    Étendu V3.2 pour répondre à l'audit Persona 3 : l'écran "Prochaine
    escale" du commandant doit afficher agent portuaire, contacts pilote
    VHF, documents requis, restrictions douanières / quarantaine.
    """

    __tablename__ = "port_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False, unique=True)

    # Frais (existants)
    agency_fee_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    pilot_fee_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    berth_fee_per_day_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    docker_fee_per_palette_eur: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    notes: Mapped[str | None] = mapped_column(Text)

    # Contacts opérationnels (V3.2)
    agent_name: Mapped[str | None] = mapped_column(String(200))
    agent_phone: Mapped[str | None] = mapped_column(String(40))
    agent_email: Mapped[str | None] = mapped_column(String(200))
    pilot_vhf_channel: Mapped[str | None] = mapped_column(String(10))  # ex. "12", "16"
    pilot_phone: Mapped[str | None] = mapped_column(String(40))
    port_control_vhf_channel: Mapped[str | None] = mapped_column(String(10))

    # Documents requis & restrictions (texte libre, listés en lignes
    # côté template — séparateur \n)
    documents_required: Mapped[str | None] = mapped_column(Text)
    restrictions: Mapped[str | None] = mapped_column(Text)  # douane, quarantaine, ISPS
    notes_for_captain: Mapped[str | None] = mapped_column(Text)


class LegKPI(Base):
    __tablename__ = "leg_kpis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, unique=True)
    palettes_carried: Mapped[int] = mapped_column(Integer, default=0)
    tonnage_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    distance_nm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    avg_speed_kn: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    on_time: Mapped[bool] = mapped_column(Boolean, default=True)
    occupancy_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    co2_avoided_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # ── Carbone (Carbon Report CFOTE_09) — auto-calculé par services.carbon ──
    do_consumed_t: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    co2_emitted_kg: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    co2_per_nm_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    co2_per_t_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    co2_per_tnm_g: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    # Verrou « saisie manuelle » : si vrai, l'auto-calcul ne réécrit pas ce KPI.
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
