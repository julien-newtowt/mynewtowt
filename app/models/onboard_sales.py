"""Vente à bord — catalogue, inventaire par navire, ventes & registre douanier.

Le commandant vend des biens et services aux collaborateurs embarqués. Ce
module porte quatre entités :

- ``OnboardProduct`` : catalogue **global** (prix unitaire, devise, unité). Le
  stock, lui, est **par navire**.
- ``OnboardStockMovement`` : grand livre de stock **append-only** — c'est le
  **cœur du registre douanier de vente détaxée (avitaillement / franchise)**.
  Quantité **signée** : positif = entrée (avitaillement / retour / ajustement +),
  négatif = sortie (vente / ajustement −). Le solde par (navire, produit) se
  calcule en direct par ``SUM(qty)`` — même philosophie que la caisse de bord.
- ``OnboardSale`` + ``OnboardSaleLine`` : ventes historisées, réglées en espèces
  (→ caisse de bord) ou par carte (Stripe Checkout). ``cashbox_movement_id`` sert
  de **verrou d'idempotence** : posé une seule fois au règlement, il garantit
  qu'un rejeu de webhook Stripe ne crée jamais un second mouvement de caisse.

Régime : toutes les ventes à bord relèvent du régime d'**avitaillement /
franchise** (``regime = "franchise"``). Le champ est porté sur la vente pour
tracer le régime au registre.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ── Vocabulaire ──────────────────────────────────────────────────────────────

# Devises supportées : alignées sur la caisse de bord (onboard_cashbox).
SUPPORTED_CURRENCIES: tuple[str, ...] = ("EUR", "USD", "VND")

PRODUCT_KINDS: tuple[str, ...] = ("bien", "service")
PRODUCT_KIND_LABELS: dict[str, str] = {
    "bien": "Bien",
    "service": "Service",
}

# Sens/motif d'un mouvement de stock. Les motifs « + » ajoutent au stock,
# les motifs « − » retranchent ; ``ajustement`` peut aller dans les deux sens
# (la quantité signée fait foi).
STOCK_REASONS: tuple[str, ...] = (
    "avitaillement",  # entrée : embarquement de marchandises en franchise
    "vente",  # sortie : vendu à bord (rattaché à une OnboardSale)
    "ajustement",  # correction manuelle (+/−)
    "inventaire",  # recalage sur comptage physique (+/−)
    "retour",  # entrée : reprise / annulation de vente
)
STOCK_REASON_LABELS: dict[str, str] = {
    "avitaillement": "Avitaillement (entrée)",
    "vente": "Vente (sortie)",
    "ajustement": "Ajustement",
    "inventaire": "Inventaire",
    "retour": "Retour / reprise",
}

# Statuts d'une vente.
SALE_STATUSES: tuple[str, ...] = (
    "draft",  # brouillon : lignes en cours de saisie
    "pending_payment",  # lien Stripe généré, en attente du webhook
    "paid",  # réglée (espèces confirmées ou webhook Stripe reçu)
    "cancelled",  # annulée avant règlement
    "refunded",  # remboursée après règlement
)
SALE_STATUS_LABELS: dict[str, str] = {
    "draft": "Brouillon",
    "pending_payment": "En attente de paiement",
    "paid": "Payée",
    "cancelled": "Annulée",
    "refunded": "Remboursée",
}

PAYMENT_METHODS: tuple[str, ...] = ("cash", "card")
PAYMENT_METHOD_LABELS: dict[str, str] = {
    "cash": "Espèces",
    "card": "Carte bancaire (Stripe)",
}

# Régime douanier — une seule valeur pour l'instant (décision : toujours détaxé).
REGIME_FRANCHISE = "franchise"


class OnboardProduct(Base):
    """Catalogue de biens/services vendables à bord (référentiel global)."""

    __tablename__ = "onboard_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="bien", nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), default="EUR", nullable=False)
    # Unité de vente affichée (« pièce », « kg », « bouteille »…).
    unit: Mapped[str] = mapped_column(String(20), default="pièce", nullable=False)
    # Les services ne sont pas suivis en stock (tracks_stock=False).
    tracks_stock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OnboardProduct {self.sku} {self.label!r}>"


class OnboardStockMovement(Base):
    """Mouvement de stock par navire — registre douanier append-only."""

    __tablename__ = "onboard_stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("onboard_products.id"), nullable=False, index=True
    )
    # Quantité signée : + = entrée, − = sortie.
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    # Rattachement à une vente pour les sorties « vente » (traçabilité registre).
    sale_id: Mapped[int | None] = mapped_column(
        ForeignKey("onboard_sales.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    product: Mapped[OnboardProduct] = relationship()

    __table_args__ = (
        Index("ix_onboard_stock_vessel_product", "vessel_id", "product_id"),
        Index("ix_onboard_stock_occurred", "occurred_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OnboardStockMovement v={self.vessel_id} p={self.product_id} {self.qty}>"


class OnboardSale(Base):
    """Vente à bord — historisée, réglée en espèces ou par carte (Stripe)."""

    __tablename__ = "onboard_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False, index=True)
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"), index=True)
    # Acheteur : collaborateur embarqué (texte libre — pas de lien RH obligatoire).
    buyer_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)
    payment_method: Mapped[str | None] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(CHAR(3), default="EUR", nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    # Régime douanier tracé au registre (toujours « franchise » aujourd'hui).
    regime: Mapped[str] = mapped_column(String(20), default=REGIME_FRANCHISE, nullable=False)

    # ── Stripe Checkout ──────────────────────────────────────────────────────
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))

    # ── Verrou d'idempotence du règlement ────────────────────────────────────
    # Posé UNE seule fois par settle_sale : garantit un unique mouvement de
    # caisse même si le webhook Stripe est rejoué.
    cashbox_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("cashbox_movements.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    lines: Mapped[list[OnboardSaleLine]] = relationship(
        back_populates="sale",
        cascade="all, delete-orphan",
        order_by="OnboardSaleLine.id",
    )

    @property
    def status_label(self) -> str:
        return SALE_STATUS_LABELS.get(self.status, self.status)

    @property
    def payment_method_label(self) -> str:
        return PAYMENT_METHOD_LABELS.get(self.payment_method or "", "—")

    @property
    def is_settled(self) -> bool:
        """Vrai dès qu'un mouvement de caisse a été rattaché (règlement acté)."""
        return self.cashbox_movement_id is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OnboardSale {self.reference} {self.status} {self.total} {self.currency}>"


class OnboardSaleLine(Base):
    """Ligne de vente — snapshot du libellé et du prix au moment de la vente."""

    __tablename__ = "onboard_sale_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[int] = mapped_column(
        ForeignKey("onboard_sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Produit d'origine (nullable : snapshot conservé même si produit supprimé).
    product_id: Mapped[int | None] = mapped_column(ForeignKey("onboard_products.id"))
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    sale: Mapped[OnboardSale] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("sale_id", "product_id", name="uq_sale_line_product"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OnboardSaleLine sale={self.sale_id} {self.label!r} x{self.qty}>"
