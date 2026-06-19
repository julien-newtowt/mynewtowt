"""Éléments variables de paie (EVP) — lot L4 du SIRH.

Collecte mensuelle, par collaborateur, des éléments variables destinés à la
paie (heures supp., primes, tickets resto, frais, absences décomptées…),
puis verrouillage de la période avant export vers Silae (lot L5). Voir
``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.4 / module RH-4.

Cycle d'une ligne : ``draft`` (saisie) → ``locked`` (période figée) →
``exported`` (transmise à Silae, immuable).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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

# Catalogue des types d'EVP (clé technique → libellé). Paramétrable plus
# tard ; figé en constante pour la v1.
EVP_TYPES: dict[str, str] = {
    "heures_supp": "Heures supplémentaires",
    "heures_comp": "Heures complémentaires",
    "prime_anciennete": "Prime d'ancienneté",
    "prime_exceptionnelle": "Prime exceptionnelle",
    "prime_objectifs": "Prime sur objectifs",
    "tickets_resto": "Tickets restaurant",
    "frais_pro": "Frais professionnels",
    "indemnite_transport": "Indemnité transport",
    "indemnite_teletravail": "Indemnité télétravail",
    "acompte": "Acompte",
    "astreinte": "Astreinte",
    "absence": "Absence (jours décomptés)",
}

EVP_SOURCES: tuple[str, ...] = ("manual", "absence", "import")
EVP_STATUSES: tuple[str, ...] = ("draft", "locked", "exported")


class PayrollVariable(Base):
    """Une ligne d'élément variable de paie pour un collaborateur/période."""

    __tablename__ = "payroll_variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), nullable=False, index=True
    )
    # Période de paie au format AAAA-MM.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    evp_type: Mapped[str] = mapped_column(String(40), nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    comment: Mapped[str | None] = mapped_column(String(255))

    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    # Ligne auto-générée depuis une absence approuvée (déduplication).
    absence_id: Mapped[int | None] = mapped_column(
        ForeignKey("hr_absences.id"), nullable=True
    )
    # Lot d'export Silae (alimenté au lot L5).
    export_batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_payroll_variables_period", "period"),
        Index("ix_payroll_variables_status", "status"),
    )

    @property
    def type_label(self) -> str:
        return EVP_TYPES.get(self.evp_type, self.evp_type)

    @property
    def is_editable(self) -> bool:
        return self.status == "draft"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PayrollVariable #{self.id} emp={self.employee_id} {self.period} "
            f"{self.evp_type} {self.status}>"
        )
