"""Soutage (Bunker Report / BDN) — capture bord + allocations par cuve (LOT 6).

Réplique le formulaire officiel ``VESSEL_YYYYMMDD_BR.xlsx`` (Bunker Delivery
Note) dissection au rapport d'inventaire : en-tête BDN (numéro, port, date de
livraison UTC, propriétés carburant) + répartition par cuve du navire
(``vessel_tanks``, lot 1).

Cycle de vie : ``brouillon`` (modifiable par son auteur uniquement, cf.
``services.bunkering.update_draft``) → ``valide_master`` (verrouillé côté
bord ; seul le siège peut encore corriger — avec trace — via
``services.bunkering.apply_review_correction``).

Rattachement voyage : ``leg_id`` est **calculé** (jamais saisi en dur par le
bord) — le voyage qui suit chronologiquement l'escale de livraison, dans une
fenêtre paramétrable (``services.bunkering.resolve_leg_for_bunker``). Un
choix manuel reste possible (override) et une valeur ``NULL`` est un état
normal (hors fenêtre, ou aucun voyage suivant connu encore).

Contrôles de cohérence (masse vs Σ(volume×densité) des cuves, densité BDN
dans la plage attendue, volumes vs capacités cuves) sont des **méthodes de
service** (``services.bunkering``), pas des colonnes persistées : ce lot ne
code aucune nouvelle règle dans le registre ``R*`` (``validation_engine`` —
réservé au lot 8), il consomme seulement ``get_threshold`` pour les seuils
existants (R16, R23).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# brouillon = modifiable par l'auteur ; valide_master = verrouillé côté bord
# (correction siège encore possible, cf. services.bunkering, avec trace).
BUNKER_STATUSES: tuple[str, ...] = ("brouillon", "valide_master")

DEFAULT_FUEL_TYPE = "MDO"


class BunkerOperation(Base):
    """En-tête d'un soutage (Bunker Delivery Note) — un enregistrement par BDN."""

    __tablename__ = "bunker_operations"
    __table_args__ = (
        Index("ix_bunkerop_vessel", "vessel_id"),
        Index("ix_bunkerop_leg", "leg_id"),
        Index("ix_bunkerop_status", "status"),
        Index("ix_bunkerop_delivery", "delivery_datetime_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Voyage SUIVANT l'escale de livraison — rattachement CALCULÉ (jamais saisi
    # en dur), cf. services.bunkering.resolve_leg_for_bunker. NULL = hors
    # fenêtre de rattachement (choix manuel restant possible côté écran).
    leg_id: Mapped[int | None] = mapped_column(
        ForeignKey("legs.id", ondelete="SET NULL"), nullable=True
    )
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=False
    )
    bdn_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    port_locode: Mapped[str] = mapped_column(String(5), nullable=False)
    delivery_datetime_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fuel_type: Mapped[str] = mapped_column(
        String(20), default=DEFAULT_FUEL_TYPE, server_default=DEFAULT_FUEL_TYPE, nullable=False
    )
    mass_t: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    sulfur_content_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    density_15c_t_m3: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    viscosity_cst: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    water_content_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    lower_heating_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    higher_heating_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    # Facteur TtW CO2 (g CO2 / g fuel) ressaisi depuis le BDN, si le fournisseur
    # le fournit — sinon le grand livre (lot 9) retombe sur emission_factors.
    ef_ttw_co2: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(20), default="brouillon", server_default="brouillon", nullable=False
    )
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    validated_master_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_master_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    allocations: Mapped[list[BunkerTankAllocation]] = relationship(
        "BunkerTankAllocation",
        back_populates="bunker",
        cascade="all, delete-orphan",
        order_by="BunkerTankAllocation.id",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BunkerOperation {self.bdn_number} vessel={self.vessel_id} status={self.status}>"


class BunkerTankAllocation(Base):
    """Répartition d'un soutage sur une cuve du navire (``vessel_tanks``)."""

    __tablename__ = "bunker_tank_allocations"
    __table_args__ = (
        UniqueConstraint("bunker_id", "tank_id", name="uq_bunkertank_bunker_tank"),
        Index("ix_bunkertank_bunker", "bunker_id"),
        Index("ix_bunkertank_tank", "tank_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bunker_id: Mapped[int] = mapped_column(
        ForeignKey("bunker_operations.id", ondelete="CASCADE"), nullable=False
    )
    tank_id: Mapped[int] = mapped_column(ForeignKey("vessel_tanks.id"), nullable=False)
    volume_m3: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    density_t_m3: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)

    bunker: Mapped[BunkerOperation] = relationship("BunkerOperation", back_populates="allocations")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BunkerTankAllocation bunker={self.bunker_id} tank={self.tank_id} vol={self.volume_m3}>"
