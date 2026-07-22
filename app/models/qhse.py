"""QHSE — safety/quality/environmental incident reports & CAPA workflow.

Phase 0 (fondations données) : le module ne référence aucune entité en
doublon — ``vessel_id``/``leg_id``/``reporter_user_id``/``crew_member_id``/
``claim_id`` pointent toutes vers les tables existantes (``vessels``,
``legs``, ``users``, ``crew_members``, ``claims``). Il n'existe pas de table
``port_call`` dans ce dépôt : le contexte voyage/escale se résout via
``leg_id`` (cf. ``app.models.leg.Leg``), jamais via une entité inventée.

Les workflows Corrective Action et Root-Cause Evaluation sont modélisés en
tables séparées (1:0..1 avec ``QhseReport``) plutôt qu'en colonnes plates,
pour rendre explicite la distinction containment/prévention (cahier des
charges §3.1) et laisser la place à de futures actions multiples par rapport.

Délibérément absent : les champs ``*BeforeDue``/``*BeforeDueType`` de
l'export source — leur sémantique n'est pas confirmée auprès de l'éditeur
FMS (cahier des charges §15, risque #3). Le statut "à temps" se calcule
uniquement depuis ``limit_date``/``finished_date``.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

QHSE_GRADES: tuple[str, ...] = (
    "accident",
    "non_conformity",
    "near_miss",
    "observation",
    "deficiency",
    "casualty",
)
# operational = rapport terrain ad hoc ; internal_audit = audit ISM/ISPS
# programmé ; external_audit = PSC/Class/Flag (cahier des charges §3.3).
QHSE_REPORT_SOURCES: tuple[str, ...] = ("operational", "internal_audit", "external_audit")
# Type d'organisme quand le rapporteur n'est pas résolu vers un user/crew_member.
QHSE_REPORTER_ORG_TYPES: tuple[str, ...] = (
    "flag_state",
    "classification_society",
    "port_state_control",
    "vendor",
    "other",
)
QHSE_ACTION_STATUSES: tuple[str, ...] = ("open", "proposed", "approved", "implemented", "closed")


class QhseReport(Base):
    __tablename__ = "qhse_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vessel_id: Mapped[int] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=False
    )
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id", ondelete="SET NULL"))

    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[str] = mapped_column(String(20), nullable=False)
    report_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="operational", server_default="operational"
    )

    issued_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Texte libre nettoyé (artefacts [Sync] retirés) — pas de résolution vers
    # `ports`/`legs` en Phase 0, aucun champ fiable dans l'export pour le
    # faire proprement (cf. plan Phase 0).
    issued_place: Mapped[str | None] = mapped_column(String(200))

    # Rapporteur : résolu vers users/crew_members quand possible, sinon repli
    # texte libre (tiers externes — USCG, Class, MaraSoft...).
    issued_by_raw: Mapped[str | None] = mapped_column(String(200))
    reporter_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reporter_crew_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("crew_members.id", ondelete="SET NULL")
    )
    reporter_organization_type: Mapped[str | None] = mapped_column(String(30))
    contact: Mapped[str | None] = mapped_column(String(200))

    description_added_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description_added_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # Lien optionnel vers un sinistre existant plutôt qu'une duplication de
    # champs de coût (cahier des charges §2.1.C).
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    corrective_action: Mapped[CorrectiveAction | None] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )
    root_cause_evaluation: Mapped[RootCauseEvaluation | None] = relationship(
        back_populates="report", cascade="all, delete-orphan", uselist=False
    )
    deficiency_codes: Mapped[list[QhseReportDeficiencyCode]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_qhse_reports_vessel", "vessel_id"),
        Index("ix_qhse_reports_leg", "leg_id"),
        Index("ix_qhse_reports_grade", "grade"),
        Index("ix_qhse_reports_issued_date", "issued_date"),
    )


class DeficiencyCode(Base):
    """Référentiel des codes de déficience externes (Paris MoU / USCG / Class)."""

    __tablename__ = "deficiency_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    authority: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)


class QhseReportDeficiencyCode(Base):
    """Association many-to-many : une inspection peut produire plusieurs codes."""

    __tablename__ = "qhse_report_deficiency_codes"
    __table_args__ = (
        UniqueConstraint("report_id", "deficiency_code_id", name="uq_qhse_report_defcode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("qhse_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deficiency_code_id: Mapped[int] = mapped_column(
        ForeignKey("deficiency_codes.id", ondelete="CASCADE"), nullable=False
    )

    report: Mapped[QhseReport] = relationship(back_populates="deficiency_codes")


class CorrectiveAction(Base):
    """Workflow "containment" — corriger le problème immédiat (1:0..1 avec QhseReport)."""

    __tablename__ = "qhse_corrective_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("qhse_reports.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    description: Mapped[str | None] = mapped_column(Text)
    limit_date: Mapped[_date | None] = mapped_column(Date)
    postponed_date: Mapped[_date | None] = mapped_column(Date)
    finished_date: Mapped[_date | None] = mapped_column(Date)

    proposed_date: Mapped[_date | None] = mapped_column(Date)
    proposed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_date: Mapped[_date | None] = mapped_column(Date)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    implemented_date: Mapped[_date | None] = mapped_column(Date)
    implemented_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    responsible_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    responsible_rank: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open", nullable=False
    )

    report: Mapped[QhseReport] = relationship(back_populates="corrective_action")


class RootCauseEvaluation(Base):
    """Workflow "prévention" — analyse de cause racine (1:0..1 avec QhseReport).

    Miroir structurel de ``CorrectiveAction`` : mêmes champs de workflow,
    complété par ``root_cause_text``/``root_cause_category``. La complétion
    de ce workflow est historiquement ~40% moins fréquente que celle du
    correctif (cahier des charges §4.3) — c'est un indicateur de gestion à
    part entière (C2), pas juste des métadonnées.
    """

    __tablename__ = "qhse_root_cause_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("qhse_reports.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    root_cause_text: Mapped[str | None] = mapped_column(Text)
    # Taxonomie structurée pas encore adoptée (§3.6/§4.2) — nullable en attendant.
    root_cause_category: Mapped[str | None] = mapped_column(String(40))
    preventative_action: Mapped[str | None] = mapped_column(Text)

    limit_date: Mapped[_date | None] = mapped_column(Date)
    postponed_date: Mapped[_date | None] = mapped_column(Date)
    finished_date: Mapped[_date | None] = mapped_column(Date)

    proposed_date: Mapped[_date | None] = mapped_column(Date)
    proposed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_date: Mapped[_date | None] = mapped_column(Date)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    implemented_date: Mapped[_date | None] = mapped_column(Date)
    implemented_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    responsible_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    responsible_rank: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        String(20), default="open", server_default="open", nullable=False
    )

    report: Mapped[QhseReport] = relationship(back_populates="root_cause_evaluation")
