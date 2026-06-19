"""Coffre-fort des bulletins de paie — lot L6 du SIRH.

Archive les bulletins (PDF) renvoyés par Silae, diffusés en self-service au
collaborateur. Le contenu binaire est conservé en base (``content``) pour
rester disponible en conteneur éphémère et servi via un endpoint
permissionné + audité. Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.5.

RGPD : un bulletin n'est pas une donnée « ultra-sensible » au sens RIB/NIR
(qui restent dans Silae) ; l'accès est néanmoins tracé dans ``activity_logs``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Payslip(Base):
    """Bulletin de paie archivé d'un collaborateur."""

    __tablename__ = "payslips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_payslips_period", "period"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Payslip #{self.id} emp={self.employee_id} {self.period}>"
