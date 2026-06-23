"""Contrats & avenants des collaborateurs sédentaires (lot L2).

Modélise les contrats de travail (CDI/CDD, alternance, stage) et leurs
avenants (changement de poste, rémunération, temps de travail), avec un
suivi des échéances : fin de période d'essai et terme des contrats à durée
déterminée. Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.2.

Un avenant est un ``EmploymentContract`` avec ``is_amendment = True`` et
``parent_contract_id`` pointant vers le contrat initial — l'historique des
modifications se lit ainsi sur une même lignée.
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

# Types de contrat gérés (cf. cadrage : CDI/CDD, alternance, stages).
CONTRACT_TYPES: tuple[str, ...] = (
    "cdi",
    "cdd",
    "apprentissage",
    "professionnalisation",
    "stage",
)

# Types à durée déterminée → ``end_date`` obligatoire.
FIXED_TERM_TYPES: tuple[str, ...] = (
    "cdd",
    "apprentissage",
    "professionnalisation",
    "stage",
)

CONTRACT_STATUSES: tuple[str, ...] = ("draft", "active", "ended")

# Convention par défaut (cadrage : transport / maritime).
DEFAULT_CONVENTION = "transport_maritime"

# Seuil d'alerte d'échéance (jours).
ALERT_THRESHOLD_DAYS = 30


def _days_remaining(target: date | None) -> int | None:
    if not target:
        return None
    return (target - date.today()).days


class EmploymentContract(Base):
    """Contrat de travail ou avenant d'un collaborateur sédentaire."""

    __tablename__ = "employment_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)

    contract_type: Mapped[str] = mapped_column(String(30), nullable=False)
    parent_contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("employment_contracts.id"), nullable=True
    )
    is_amendment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    convention: Mapped[str] = mapped_column(String(60), default=DEFAULT_CONVENTION, nullable=False)
    classification: Mapped[str | None] = mapped_column(String(80))

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    trial_end_date: Mapped[date | None] = mapped_column(Date)

    weekly_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    gross_monthly: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    motive: Mapped[str | None] = mapped_column(String(255))
    document_path: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_employment_contracts_status", "status"),
        Index("ix_employment_contracts_end_date", "end_date"),
    )

    # ── Échéances (cf. pattern crew ``compliance_status``) ──────────────
    @property
    def trial_days_remaining(self) -> int | None:
        return _days_remaining(self.trial_end_date)

    @property
    def end_days_remaining(self) -> int | None:
        return _days_remaining(self.end_date)

    @property
    def trial_warning(self) -> bool:
        """Période d'essai se terminant sous le seuil (et pas encore passée)."""
        d = self.trial_days_remaining
        return d is not None and 0 <= d <= ALERT_THRESHOLD_DAYS

    @property
    def end_warning(self) -> bool:
        """Terme du contrat approchant sous le seuil."""
        d = self.end_days_remaining
        return d is not None and 0 <= d <= ALERT_THRESHOLD_DAYS

    @property
    def end_expired(self) -> bool:
        """Terme dépassé alors que le contrat est toujours actif (anomalie)."""
        d = self.end_days_remaining
        return d is not None and d < 0

    @property
    def alert_status(self) -> str:
        """``expired`` / ``warning`` / ``ok`` — pertinent pour un contrat actif."""
        if self.end_expired:
            return "expired"
        if self.end_warning or self.trial_warning:
            return "warning"
        return "ok"

    @property
    def has_alert(self) -> bool:
        return self.status == "active" and self.alert_status != "ok"

    def __repr__(self) -> str:  # pragma: no cover
        kind = "avenant" if self.is_amendment else self.contract_type
        return f"<EmploymentContract #{self.id} emp={self.employee_id} {kind} {self.status}>"
