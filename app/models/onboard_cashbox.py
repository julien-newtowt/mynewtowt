"""Onboard cashbox — one per vessel, multi-currency (EUR/USD/VND).

Tracks day-to-day cash movements made by the captain or crew while at
sea or in port (crew advances, victualling, taxis, small repairs, etc.).

Currency is per-movement, not per-cashbox: a single vessel can hold
multiple currencies at the same time. The balance per (vessel, currency)
is computed live from the movements ledger; we also persist a
``CashboxClosure`` row when the captain closes a period to freeze the
balance there.
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

# Supported currencies (ISO 4217). Add more by extending this tuple +
# the dropdown in the template; the schema is currency-agnostic.
SUPPORTED_CURRENCIES: tuple[str, ...] = ("EUR", "USD", "VND")

CURRENCY_LABELS: dict[str, str] = {
    "EUR": "EUR · Euro",
    "USD": "USD · US Dollar",
    "VND": "VND · Đồng vietnamien",
}

# Common cashbox movement categories (free text on top, these are
# suggestions in the dropdown).
MOVEMENT_CATEGORIES: tuple[str, ...] = (
    "avance_equipage",
    "avitaillement",
    "transport_terrestre",
    "urgence_medicale",
    "petit_entretien",
    "representation",
    "frais_portuaire",
    "douane",
    "carburant_annexe",
    "depot_recharge",
    "autre",
)

CATEGORY_LABELS: dict[str, str] = {
    "avance_equipage": "Avance équipage",
    "avitaillement": "Avitaillement (eau, vivres)",
    "transport_terrestre": "Transport terrestre",
    "urgence_medicale": "Urgence médicale",
    "petit_entretien": "Petit entretien",
    "representation": "Représentation / hospitalité",
    "frais_portuaire": "Frais portuaire",
    "douane": "Formalité douanière",
    "carburant_annexe": "Carburant annexe",
    "depot_recharge": "Dépôt / recharge de caisse",
    "autre": "Autre",
}


class OnboardCashbox(Base):
    __tablename__ = "onboard_cashboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    movements: Mapped[list[CashboxMovement]] = relationship(
        back_populates="cashbox",
        cascade="all, delete-orphan",
        order_by="CashboxMovement.occurred_at.desc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OnboardCashbox vessel={self.vessel_id}>"


class CashboxMovement(Base):
    __tablename__ = "cashbox_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cashbox_id: Mapped[int] = mapped_column(
        ForeignKey("onboard_cashboxes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Signed amount: positive = income (recharge), negative = expense.
    # We store the signed value directly (no separate `kind` column) so
    # SUM() over movements returns the balance.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)

    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"))
    port_id: Mapped[int | None] = mapped_column(ForeignKey("ports.id"))

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    receipt_url: Mapped[str | None] = mapped_column(String(500))

    cashbox: Mapped[OnboardCashbox] = relationship(back_populates="movements")

    __table_args__ = (
        Index("ix_cashbox_mov_cb_date", "cashbox_id", "occurred_at"),
        Index("ix_cashbox_mov_currency", "currency"),
    )


class CashboxClosure(Base):
    __tablename__ = "cashbox_closures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cashbox_id: Mapped[int] = mapped_column(
        ForeignKey("onboard_cashboxes.id", ondelete="CASCADE"), nullable=False
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counted_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    computed_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    variance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        UniqueConstraint("cashbox_id", "currency", "period_end", name="uq_closure_period"),
    )
