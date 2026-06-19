"""Entretiens & évolution de poste — lot L6 du SIRH.

Planification et suivi des entretiens (annuel, professionnel, mi-parcours)
avec rappel d'échéance (``next_due_date`` — l'entretien professionnel est
légalement biennal). Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.6.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

REVIEW_TYPES: dict[str, str] = {
    "annuel": "Entretien annuel",
    "professionnel": "Entretien professionnel",
    "mi_parcours": "Entretien mi-parcours",
}

ALERT_THRESHOLD_DAYS = 30


class HrReview(Base):
    """Entretien d'un collaborateur."""

    __tablename__ = "hr_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), nullable=False, index=True
    )
    review_type: Mapped[str] = mapped_column(String(20), nullable=False)
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str | None] = mapped_column(Text)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_hr_reviews_next_due", "next_due_date"),)

    @property
    def type_label(self) -> str:
        return REVIEW_TYPES.get(self.review_type, self.review_type)

    @property
    def due_days_remaining(self) -> int | None:
        if not self.next_due_date:
            return None
        return (self.next_due_date - date.today()).days

    @property
    def is_due_soon(self) -> bool:
        d = self.due_days_remaining
        return d is not None and d <= ALERT_THRESHOLD_DAYS

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HrReview #{self.id} emp={self.employee_id} {self.review_type} {self.review_date}>"
