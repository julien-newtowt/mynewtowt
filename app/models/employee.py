"""Collaborateur sédentaire — socle du SIRH (lot L1).

Dossier RH des salariés à terre (par opposition aux navigants gérés par
``crew_members``). Conformément au cahier des charges SIRH
(``docs/strategy/CAHIER_DES_CHARGES_SIRH.md``, §4.1) :

- Aucune donnée sensible (RIB, NIR, identité) n'est stockée ici : elle
  reste dans Silae (logiciel de paie). On ne conserve que les données
  professionnelles nécessaires à l'opérationnel RH.
- ``user_id`` relie la fiche au compte staff (self-service à venir, L3/L5).
- ``crew_member_id`` rattache, le cas échéant, un salarié également marin
  pour mutualiser contrats / EVP sans dupliquer sa fiche navigante.
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Statuts d'un collaborateur dans l'effectif.
EMPLOYEE_STATUSES: tuple[str, ...] = ("active", "suspended", "left")


class Employee(Base):
    """Fiche collaborateur sédentaire."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Liens optionnels vers le compte staff et la fiche marin.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=True
    )
    crew_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("crew_members.id"), nullable=True
    )

    matricule: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Coordonnées professionnelles uniquement (pas de données personnelles).
    email_pro: Mapped[str | None] = mapped_column(String(255))
    phone_pro: Mapped[str | None] = mapped_column(String(40))

    # Conservée volontairement (cf. cahier §4.1) pour l'âge / la pyramide
    # des âges du reporting RH — ce n'est pas une pièce d'identité ni un NIR.
    birth_date: Mapped[date | None] = mapped_column(Date)

    job_title: Mapped[str | None] = mapped_column(String(150))
    department: Mapped[str | None] = mapped_column(String(100))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    work_location: Mapped[str | None] = mapped_column(String(100))

    entry_date: Mapped[date | None] = mapped_column(Date)
    exit_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    # NB : les soldes de congés (CP) sont gérés dans Silae (source de vérité) —
    # non stockés ici (décision de cadrage). Pas de RTT dans la convention.

    # Clé de rapprochement avec Silae (export EVP, import bulletins).
    silae_id: Mapped[str | None] = mapped_column(String(60))

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
        Index("ix_employees_status", "status"),
        Index("ix_employees_department", "department"),
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def seniority_years(self) -> float | None:
        """Ancienneté en années depuis ``entry_date`` (None si inconnue)."""
        if not self.entry_date:
            return None
        end = self.exit_date or date.today()
        return round((end - self.entry_date).days / 365.25, 1)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Employee {self.matricule} {self.full_name!r} status={self.status}>"
