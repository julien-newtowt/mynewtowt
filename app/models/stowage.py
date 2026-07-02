"""Stowage plan — plan d'arrimage 18 zones par navire.

STRUCTURE NAVIRE (identique pour Anemos / Artemis / Atlantis / Atlas /
Archimedes / Astérias) :

    2 cales × 3 ponts × 3 blocs = 18 zones.

Convention de nommage : {DECK}_{HOLD}_{BLOCK}
    DECK  : INF (inférieure), MIL (intermédiaire), SUP (supérieure)
    HOLD  : AR (arrière), AV (avant)
    BLOCK : AR (arrière), MIL (milieu), AV (avant)

Ordre de chargement : arrière→avant, bas→haut (INF_AR_AR=1 → SUP_AV_AV=18)
Exception : marchandises dangereuses (IMO) et hors-gabarit → zones SUP_AV.

BASKET (panier de manutention standard) :
    Surface libre 380×150 cm, hauteur 2.2 m, CMU 5.1 t, tare 2.2 t.
    Toute palette hors-gabarit va automatiquement en SUP_AV.

RÉFÉRENTIEL PAR CLASSE (cf. ``StowageZoneSpec`` + ``services.stowage_specs``) :
    Chaque classe de navire (ex. "phoenix" — Anemos/Artemis/…) porte, pour
    chacune des 18 zones, une capacité (palettes EPAL-équivalentes), une
    résistance de pont (charge max en tonnes, poids palette max admis) et les
    règles de gerbage. Source : plan théorique de chargement café (Phoenix).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Zones standard d'un navire 978 palettes (3 ponts × 2 cales × 3 blocs)
DECKS = ("INF", "MIL", "SUP")
HOLDS = ("AR", "AV")
BLOCKS = ("AR", "MIL", "AV")

ZONE_LOADING_ORDER: list[str] = [
    f"{deck}_{hold}_{block}"
    for hold in HOLDS  # arrière → avant (cale)
    for deck in DECKS  # bas → haut (pont)
    for block in BLOCKS  # arrière → avant (bloc)
]

# Zones dédiées hors-gabarit / IMO
DANGEROUS_ZONES = ("SUP_AV_AR", "SUP_AV_MIL", "SUP_AV_AV")

# Spécifications panier (basket)
BASKET_LENGTH_CM = 380
BASKET_WIDTH_CM = 150
BASKET_HEIGHT_M = 2.2
BASKET_CMU_T = 5.1
BASKET_TARE_T = 2.2


class StowagePlan(Base):
    __tablename__ = "stowage_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # 'draft' | 'approved' | 'loaded' | 'locked'
    notes: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    items: Mapped[list[StowageItem]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="StowageItem.zone",
    )


class StowageItem(Base):
    """Une affectation : un lot de palettes → une zone du navire."""

    __tablename__ = "stowage_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("stowage_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("commercial_orders.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("packing_list_batches.id"))
    zone: Mapped[str] = mapped_column(String(20), nullable=False)
    pallet_format: Mapped[str] = mapped_column(String(20), default="EPAL", nullable=False)
    pallet_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    is_dangerous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_oversized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Remontée packing list (dimension, hauteur, classement) : recopiée du
    # batch à l'affectation pour figer la photo arrimage et alimenter les
    # contrôles de résistance / hors-gabarit / gerbage.
    description: Mapped[str | None] = mapped_column(Text)
    hs_code: Mapped[str | None] = mapped_column(String(20))
    imdg_class: Mapped[str | None] = mapped_column(String(20))
    un_number: Mapped[str | None] = mapped_column(String(10))
    length_cm: Mapped[float | None] = mapped_column(Float)
    width_cm: Mapped[float | None] = mapped_column(Float)
    height_cm: Mapped[float | None] = mapped_column(Float)
    cubage_m3: Mapped[float | None] = mapped_column(Float)
    stackable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Gerbé = palette posée en hauteur (stack) ; sinon base (au sol).
    is_stacked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    plan: Mapped[StowagePlan] = relationship(back_populates="items")


class StowageZoneSpec(Base):
    """Référentiel capacité & résistance d'une zone, par classe de navire.

    Une ligne par (``vessel_class``, ``zone``). Surcharge éditable en admin
    du référentiel théorique (cf. ``services.stowage_specs`` qui fournit les
    valeurs Phoenix par défaut quand aucune ligne DB n'existe).
    """

    __tablename__ = "stowage_zone_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_class: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    zone: Mapped[str] = mapped_column(String(20), nullable=False)
    # Capacité de référence en palettes EPAL-équivalentes (cf. PALETTE_COEFFICIENTS).
    capacity_epal: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    # Résistance pont : charge admissible (t) et poids palette max (kg).
    max_load_t: Mapped[float | None] = mapped_column(Float)
    max_pallet_weight_kg: Mapped[float | None] = mapped_column(Float)
    # Gerbage : possible géométriquement ? Et pour les palettes lourdes ?
    stack_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    heavy_stack_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Cale ségréguée (température & humidité contrôlées — denrées type café).
    segregated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("vessel_class", "zone", name="uq_zone_spec_class_zone"),)
