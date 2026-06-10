"""Contrats d'assurance — P&I, Hull/DIV, War Risk, cargo."""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

INSURANCE_KINDS = ("p_i", "hull", "div", "war_risk", "cargo", "other")


class InsuranceContract(Base):
    __tablename__ = "insurance_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    insurer: Mapped[str] = mapped_column(String(200), nullable=False)
    broker: Mapped[str | None] = mapped_column(String(200))
    valid_from: Mapped[_date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[_date] = mapped_column(Date, nullable=False)
    premium_eur: Mapped[float | None] = mapped_column()
    deductible_eur: Mapped[float | None] = mapped_column()
    coverage_amount_eur: Mapped[float | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
