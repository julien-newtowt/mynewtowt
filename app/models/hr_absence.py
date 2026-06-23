"""Congés & absences des collaborateurs sédentaires (lot L3).

Table distincte de ``crew_leaves`` (congés marins) : les sédentaires ont
leur propre cycle de demande/validation, centralisé côté RH, avec décompte
en jours ouvrés. Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.3.

Workflow : ``requested`` (demande, depuis le self-service ou saisie RH) →
``approved`` / ``rejected`` (décision RH) ; ``cancelled`` si le
collaborateur annule sa demande avant décision.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
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

ABSENCE_KINDS: tuple[str, ...] = (
    "cp",
    "maladie",
    "maternite",
    "paternite",
    "sans_solde",
    "formation",
    "autre",
)

ABSENCE_STATUSES: tuple[str, ...] = ("requested", "approved", "rejected", "cancelled")


class HrAbsence(Base):
    """Congé / absence d'un collaborateur sédentaire."""

    __tablename__ = "hr_absences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    half_day_start: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    half_day_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    business_days: Mapped[Decimal] = mapped_column(Numeric(5, 1), default=0, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Marqueur d'envoi à la paie (alimenté au lot L4/L5).
    silae_exported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_hr_absences_status", "status"),
        Index("ix_hr_absences_dates", "start_date", "end_date"),
    )

    @property
    def is_pending(self) -> bool:
        return self.status == "requested"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<HrAbsence #{self.id} emp={self.employee_id} {self.kind} "
            f"{self.start_date}->{self.end_date} {self.status}>"
        )
