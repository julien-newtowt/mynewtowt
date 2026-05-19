"""Client-facing invoice — emitted on booking confirmation."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClientInvoice(Base):
    __tablename__ = "client_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id"), nullable=True, index=True
    )
    client_account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), nullable=False, index=True
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    amount_excl_vat_eur: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    vat_amount_eur: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    amount_incl_vat_eur: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(CHAR(3), default="EUR", nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default="draft", nullable=False
    )  # draft / issued / paid / overdue / cancelled / refunded

    pdf_url: Mapped[str | None] = mapped_column(String(500))

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_invoices_status", "status"),)
