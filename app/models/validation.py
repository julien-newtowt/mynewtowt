"""Socle du moteur de règles de validation MRV (LOT 2).

Couche Référentiel de l'architecture événementielle MRV :

- ``ValidationRule`` — catalogue des 31 règles (R01…R26 + IR01…IR05),
  seedé depuis ``Matrice_regles_validation.md``. La sévérité par défaut y
  est portée au niveau de la règle ; l'affinage fin (override de sévérité
  par seuil) arrive au lot 8.
- ``ValidationRuleThreshold`` — seuils numériques **paramétrables** (jamais
  codés en dur dans une règle), avec override optionnel par navire
  (``vessel_id``). ``provisional`` trace les valeurs non confirmées métier
  (décision Q8 : 14/16 paramètres provisoires).
- ``DashboardParameter`` — paramètres du dashboard Performance
  Environnementale (taux d'occupation, capacité de référence, EF
  comparateurs), également avec override par navire.
- ``QualityCheckResult`` — journal d'anomalies (``Controles_Qualite``) :
  une ligne par *outcome* de règle, avec le **snapshot des seuils
  consommés** dans ``details`` (reproductibilité de l'audit).

ÉCART ASSUMÉ vs plan §2.2 — référence polymorphe sans FK
--------------------------------------------------------
Le plan prévoyait ``event_id`` / ``report_id`` / ``bunker_id`` /
``flgo_reading_id`` en clés étrangères distinctes. Ces tables cibles
(``nav_events``, ``bunker_operations``, ``flgo_readings``…) arrivent aux
lots 3/5/6/7 — elles **n'existent pas encore**. On utilise donc une
référence polymorphe ``subject_type`` (str) + ``subject_id`` (int) **sans
contrainte FK**, ce qui permet au moteur de tourner (et d'être testé) dès
le lot 2 sur des sujets duck-typés, sans dépendre d'un modèle événementiel
absent. ``leg_id`` reste une vraie FK (``legs`` existe). Un durcissement en
contrainte pourra être posé au lot 8 une fois les tables cibles créées.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Valeurs autorisées (documentées ; non contraintes en base pour rester
# tolérant à l'évolution — validées applicativement).
RULE_SEVERITIES: tuple[str, ...] = ("bloquant", "warning", "info")
RULE_SCOPES: tuple[str, ...] = ("event", "report", "bunker", "flgo", "voyage", "qhse")
CHECK_RESULTS: tuple[str, ...] = ("pass", "fail")


class ValidationRule(Base):
    """Catalogue d'une règle de validation MRV (source : Matrice)."""

    __tablename__ = "validation_rules"

    # PK textuelle stable : R01…R26, IR01…IR05.
    rule_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    domain: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # bloquant / warning / info
    default_severity: Mapped[str] = mapped_column(
        String(12), nullable=False, default="warning", server_default="warning"
    )
    # event / report / bunker / flgo / voyage
    scope: Mapped[str] = mapped_column(String(12), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ValidationRule {self.rule_id} {self.scope}/{self.default_severity} active={self.active}>"


class ValidationRuleThreshold(Base):
    """Seuil numérique paramétrable d'une règle, override optionnel par navire.

    Résolution (moteur) : (rule, vessel) → (rule, NULL) → défaut codé
    fail-closed. ``value`` est toujours renseignée (les paramètres sans
    valeur métier confirmée reçoivent une proposition chiffrée marquée
    ``provisional``).
    """

    __tablename__ = "validation_rule_thresholds"
    __table_args__ = (
        UniqueConstraint("rule_id", "vessel_id", "parameter_name", name="uq_vrt_rule_vessel_param"),
        Index("ix_vrt_rule", "rule_id"),
        Index("ix_vrt_vessel", "vessel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("validation_rules.rule_id", ondelete="CASCADE"), nullable=False
    )
    # NULL = seuil global ; renseigné = override pour ce navire.
    vessel_id: Mapped[int | None] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=True
    )
    parameter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(15, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    # True tant que la valeur n'est pas confirmée par le métier (Q8/D6).
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        scope = f"vessel={self.vessel_id}" if self.vessel_id else "global"
        return (
            f"<ValidationRuleThreshold {self.rule_id}:{self.parameter_name}={self.value} {scope}>"
        )


class DashboardParameter(Base):
    """Paramètre du dashboard Performance Environnementale (override par navire)."""

    __tablename__ = "dashboard_parameters"
    __table_args__ = (
        UniqueConstraint("parameter_name", "vessel_id", name="uq_dashparam_name_vessel"),
        Index("ix_dashparam_vessel", "vessel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parameter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    vessel_id: Mapped[int | None] = mapped_column(
        ForeignKey("vessels.id", ondelete="CASCADE"), nullable=True
    )
    value: Mapped[Decimal] = mapped_column(Numeric(15, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        scope = f"vessel={self.vessel_id}" if self.vessel_id else "global"
        return f"<DashboardParameter {self.parameter_name}={self.value} {scope}>"


class QualityCheckResult(Base):
    """Journal d'anomalies — un enregistrement par *outcome* de règle.

    ``subject_type`` + ``subject_id`` : référence polymorphe SANS FK (cf.
    docstring du module). ``details`` embarque le snapshot des seuils
    consommés (``thresholds_used``) pour la reproductibilité de l'audit.
    """

    __tablename__ = "quality_check_results"
    __table_args__ = (
        Index("ix_qcr_rule_executed", "rule_id", "executed_at"),
        Index("ix_qcr_subject", "subject_type", "subject_id"),
        Index("ix_qcr_leg", "leg_id"),
        Index("ix_qcr_run", "run_id"),
        # LOT 14 — accélère la file « anomalies non acquittées » (tour de contrôle
        # qualité de la bascule) : filtres ``acknowledged_at IS NULL`` (dashboard
        # qualité, digest, resets en attente). Migration 20260709_0105.
        Index("ix_qcr_acknowledged_at", "acknowledged_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("validation_rules.rule_id", ondelete="CASCADE"), nullable=False
    )
    # Référence polymorphe (pas de FK — tables cibles aux lots 3/6/7).
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leg_id: Mapped[int | None] = mapped_column(
        ForeignKey("legs.id", ondelete="SET NULL"), nullable=True
    )
    # UUID hex du run (regroupe les résultats d'une même exécution).
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # pass / fail
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    severity_applied: Mapped[str | None] = mapped_column(String(12))
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # LOT 8 — acquittement d'un ``fail`` par le siège (écran /mrv/qualite).
    # Un fail acquitté ne re-déclenche plus d'alerte (dédup 24 h, cf.
    # ``services.validation_rules_catalog.route_alerts``) — la ligne QCR reste
    # (journal append-only) ; seule l'action de traitement est tracée + datée.
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<QualityCheckResult {self.rule_id} {self.result}/{self.severity_applied} "
            f"{self.subject_type}#{self.subject_id}>"
        )
