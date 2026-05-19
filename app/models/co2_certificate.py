"""CO2 avoidance certificate — emitted per booking on delivery."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CO2Certificate(Base):
    __tablename__ = "co2_certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id"), index=True
    )
    client_account_id: Mapped[int] = mapped_column(
        ForeignKey("client_accounts.id"), nullable=False, index=True
    )
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id"))

    tonnage_transported_t: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    distance_nm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    co2_emitted_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    co2_conventional_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    co2_avoided_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)

    pdf_url: Mapped[str | None] = mapped_column(String(500))

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
