"""Commercial — clients, rate grids, offers, orders.

Reprises de la V3.0.0 ("charte Nouvelle Étoile") pour rouvrir le pipeline
commercial complet : grilles tarifaires dégressives par bracket de
volume, génération d'offres, conversion en commandes, intégration
Pipedrive (pipedrive_org_id, pipedrive_deal_id).

Identifiants de référence :
- Client       — id auto
- RateGrid     — `RG-YYYY-NNNN`
- RateOffer    — `RO-YYYY-NNNN`
- Order        — `ORD-YYYY-NNNN`

Brackets dégressifs (DEFAULT_BRACKETS_SHIPPER) :
  lt50 (×1.10), 100 (×1.00), 200 (×0.80), 300 (×0.80), 400 (×0.80),
  500 (×0.70), full ship 850 (×0.60).
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

CLIENT_TYPES = ("freight_forwarder", "shipper")
RATE_GRID_STATUSES = ("draft", "active", "expired", "superseded")
RATE_OFFER_STATUSES = ("draft", "sent", "accepted", "declined", "expired")
ORDER_STATUSES = ("draft", "confirmed", "loaded", "delivered", "cancelled")

# Unités de tarification des options de grille.
# per_palette      — appliqué × nombre de palettes (ex. manutention)
# per_tonne        — appliqué × tonnage chargé
# per_booking      — forfait par réservation
# per_booking_note — forfait par booking note émise (frais documentaires)
RATE_OPTION_UNITS = ("per_palette", "per_tonne", "per_booking", "per_booking_note")

RATE_OPTION_UNIT_LABELS: dict[str, str] = {
    "per_palette": "par palette",
    "per_tonne": "par tonne chargée",
    "per_booking": "par réservation",
    "per_booking_note": "par booking note",
}


# Brackets dégressifs (volume → coefficient)
DEFAULT_BRACKETS_SHIPPER: list[dict] = [
    {"key": "lt50", "label": "< 50 palettes", "max_qty": 49, "coeff": 1.10},
    {"key": "100", "label": "100 palettes", "max_qty": 100, "coeff": 1.00},
    {"key": "200", "label": "200 palettes", "max_qty": 200, "coeff": 0.80},
    {"key": "300", "label": "300 palettes", "max_qty": 300, "coeff": 0.80},
    {"key": "400", "label": "400 palettes", "max_qty": 400, "coeff": 0.80},
    {"key": "500", "label": "500 palettes", "max_qty": 500, "coeff": 0.70},
    {"key": "full", "label": "Full ship (850 pal.)", "max_qty": 850, "coeff": 0.60},
]

DEFAULT_BRACKETS_FF: list[dict] = [
    {"key": "flat", "label": "Tarif unique", "max_qty": 850, "coeff": 1.00},
]

PALETTE_COEFFICIENTS: dict[str, float] = {
    "EPAL": 1.00,
    "USPAL": 1.20,
    "PORTPAL": 1.20,
    "IBC": 1.30,
    "BIGBAG": 1.25,
    "BARRIQUE120": 1.50,
    "BARRIQUE140": 2.00,
}


class Client(Base):
    """Commercial customer — Freight Forwarder or direct Shipper."""

    __tablename__ = "commercial_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_type: Mapped[str] = mapped_column(String(30), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    vat_number: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    pipedrive_org_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    rate_grids: Mapped[list[RateGrid]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    rate_offers: Mapped[list[RateOffer]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    orders: Mapped[list[Order]] = relationship(back_populates="client")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Client {self.name} ({self.client_type})>"


class RateGrid(Base):
    """Grille tarifaire = 1 route POL/POD + 1 période.

    Deux familles :
    - grille **client** (`client_id` renseigné) — s'applique au client connu ;
      une grille client sans route (pol/pod NULL) vaut pour toutes ses routes.
    - grille **par défaut** (`client_id` NULL, `is_default=True`) — s'applique
      à tout demandeur inconnu sur la route, et sert de repli.
    """

    __tablename__ = "rate_grids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("commercial_clients.id"), nullable=True, index=True
    )
    # Route couverte par la grille (UN/LOCODE 5 car.) — NULL = toutes routes
    # (uniquement pertinent pour une grille client).
    pol_locode: Mapped[str | None] = mapped_column(String(5), index=True)
    pod_locode: Mapped[str | None] = mapped_column(String(5), index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    valid_from: Mapped[_date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[_date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    base_rate_per_palette: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    adjustment_index: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=Decimal("1.0000"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped[Client | None] = relationship(back_populates="rate_grids")
    lines: Mapped[list[RateGridLine]] = relationship(
        back_populates="grid", cascade="all, delete-orphan", order_by="RateGridLine.max_qty"
    )
    options: Mapped[list[RateGridOption]] = relationship(
        back_populates="grid", cascade="all, delete-orphan", order_by="RateGridOption.id"
    )


class RateGridLine(Base):
    """Une bracket d'une grille tarifaire."""

    __tablename__ = "rate_grid_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_id: Mapped[int] = mapped_column(
        ForeignKey("rate_grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bracket_key: Mapped[str] = mapped_column(String(20), nullable=False)
    bracket_label: Mapped[str] = mapped_column(String(80), nullable=False)
    max_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    coeff: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)

    grid: Mapped[RateGrid] = relationship(back_populates="lines")


class RateGridOption(Base):
    """Option tarifaire d'une grille (coûts annexes au fret).

    Exemples : manutention par palette, contribution à la tonne chargée,
    forfait par réservation, frais de booking note. Les options actives
    sont reprises dans tout devis généré sur la grille.
    """

    __tablename__ = "rate_grid_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_id: Mapped[int] = mapped_column(
        ForeignKey("rate_grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)  # RATE_OPTION_UNITS
    amount_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    grid: Mapped[RateGrid] = relationship(back_populates="options")


class RateOffer(Base):
    """Offre commerciale envoyée à un client (DOCX gen. possible)."""

    __tablename__ = "rate_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_clients.id"), nullable=False, index=True
    )
    grid_id: Mapped[int | None] = mapped_column(ForeignKey("rate_grids.id"))
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    estimated_palettes: Mapped[int | None] = mapped_column(Integer)
    proposed_rate_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    valid_until: Mapped[_date | None] = mapped_column(Date)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    pipedrive_deal_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped[Client] = relationship(back_populates="rate_offers")


class Order(Base):
    """Commande ferme (issue d'une offre acceptée ou créée directement)."""

    __tablename__ = "commercial_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_clients.id"), nullable=False, index=True
    )
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("rate_offers.id"))
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    # Back-link de reprise (B2.2) : quand renseigné, la commande héritée du
    # rail A a été migrée en réservation (rail B). Sert d'idempotence au
    # script de reprise et de dé-doublonnage capacité (la palette est alors
    # comptée via le booking, plus via la commande).
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    booked_palettes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_per_palette_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cargo_description: Mapped[str | None] = mapped_column(Text)
    description_of_goods: Mapped[str | None] = mapped_column(Text)
    # Adresses structurées
    shipper_name: Mapped[str | None] = mapped_column(String(200))
    shipper_address: Mapped[str | None] = mapped_column(Text)
    consignee_name: Mapped[str | None] = mapped_column(String(200))
    consignee_address: Mapped[str | None] = mapped_column(Text)
    notify_name: Mapped[str | None] = mapped_column(String(200))
    notify_address: Mapped[str | None] = mapped_column(Text)
    pipedrive_deal_id: Mapped[int | None] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    client: Mapped[Client] = relationship(back_populates="orders")
    assignments: Mapped[list[OrderAssignment]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderAssignment(Base):
    """Affectation d'une commande à un leg (pour ventiler une ord. sur plusieurs voyages)."""

    __tablename__ = "order_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("commercial_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id"), nullable=False, index=True)
    palettes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pallet_format: Mapped[str] = mapped_column(String(20), default="EPAL", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="assignments")
