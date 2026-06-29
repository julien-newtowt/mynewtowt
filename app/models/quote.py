"""Devis (quote) — généré par l'outil public de cotation.

Un devis est calculé sur la grille tarifaire applicable (grille client si
le demandeur est connu, grille par défaut de la route sinon) et fige un
instantané des lignes (fret + options) : la grille peut évoluer ensuite
sans altérer les devis émis.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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

QUOTE_STATUSES = ("issued", "accepted", "expired", "cancelled")


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="issued", nullable=False)

    # Route + voyage visé (le leg est optionnel : un devis peut être
    # demandé sur une route sans date arrêtée).
    pol_locode: Mapped[str] = mapped_column(String(5), nullable=False, index=True)
    pod_locode: Mapped[str] = mapped_column(String(5), nullable=False, index=True)
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id", ondelete="SET NULL"))
    etd_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Grille appliquée (snapshot de la référence : la FK peut disparaître).
    grid_id: Mapped[int | None] = mapped_column(ForeignKey("rate_grids.id", ondelete="SET NULL"))
    grid_reference: Mapped[str | None] = mapped_column(String(20))

    # Demandeur : compte client authentifié et/ou coordonnées libres (invité).
    client_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_accounts.id", ondelete="SET NULL"), index=True
    )
    contact_name: Mapped[str | None] = mapped_column(String(160))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    contact_company: Mapped[str | None] = mapped_column(String(200))

    palettes_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tonnage_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    hazardous: Mapped[bool | None] = mapped_column(default=False)

    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    freight_subtotal_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    options_total_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    total_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)

    # Ajustement commercial (remise négative / majoration positive) appliqué par
    # le commercial sur le total calculé, avec commentaire justificatif.
    adjustment_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    adjustment_comment: Mapped[str | None] = mapped_column(Text)

    valid_until: Mapped[_date | None] = mapped_column(Date)
    lang: Mapped[str] = mapped_column(String(5), default="fr", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # Lignes palettes [[format, count], …] — JSON, pour pré-remplir le wizard
    # de réservation lors de la conversion devis → booking.
    items_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Relance J+1 sur devis non converti (nurturing avant-vente) : horodatage
    # de l'envoi (NULL = jamais relancé). Une seule relance par devis.
    followup_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[QuoteLine]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="QuoteLine.position"
    )
    views: Mapped[list[QuoteView]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", order_by="QuoteView.viewed_at.desc()"
    )

    @property
    def net_total_eur(self) -> Decimal:
        """Total final = total calculé + ajustement commercial (signé)."""
        return (self.total_eur or Decimal("0")) + (self.adjustment_eur or Decimal("0"))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Quote {self.reference} {self.pol_locode}->{self.pod_locode}>"


class QuoteLine(Base):
    __tablename__ = "quote_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # freight | surcharge | option
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"), nullable=False)
    unit_price_eur: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    total_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)

    quote: Mapped[Quote] = relationship(back_populates="lines")


class QuoteView(Base):
    """Historique des consultations d'un devis (ouverture du lien /devis/{ref}).

    Permet au commercial de savoir si — et combien de fois — le client a
    consulté son devis. ``viewer`` distingue client authentifié / invité / staff.
    """

    __tablename__ = "quote_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    viewer: Mapped[str] = mapped_column(String(20), default="client", nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))

    quote: Mapped[Quote] = relationship(back_populates="views")
