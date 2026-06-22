"""Planification provisoire — scénarios « what-if » isolés du planning réel.

Un :class:`PlanningScenario` regroupe des :class:`ScenarioLeg` qui sont une
**copie de travail** totalement séparée de la table ``legs`` : créer, modifier
ou supprimer un scénario n'a aucun effet sur la planification en cours
(aucune FK des bookings / escales / MRV / finance ne pointe vers eux).

L'outil est volontairement **consultatif** : on n'applique jamais un scénario
au réel automatiquement (cf. CLAUDE.md / décision produit). La validation est
souple — seules les dates incohérentes bloquent ; le reste (chevauchement,
continuité géographique) remonte en avertissements pour laisser libre cours
aux hypothèses.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlanningScenario(Base):
    """En-tête d'une planification provisoire (scénario nommé)."""

    __tablename__ = "planning_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # draft = brouillon de travail ; archived = mis de côté (lecture seule UI).
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PlanningScenario {self.id} {self.name!r}>"


class ScenarioLeg(Base):
    """Traversée provisoire rattachée à un scénario.

    Mirroir des champs « cœur planning » d'un :class:`~app.models.leg.Leg`
    (navire, ports, ETD/ETA, escale, vitesse) — sans les champs réglementaires
    / booking / closure qui n'ont pas de sens hors production.
    """

    __tablename__ = "scenario_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("planning_scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vessel_id: Mapped[int] = mapped_column(ForeignKey("vessels.id"), nullable=False)
    departure_port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False)
    arrival_port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), nullable=False)

    etd: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Étiquette libre (reprend le leg_code d'origine en cas de clonage).
    label: Mapped[str | None] = mapped_column(String(40))
    # Statut purement cosmétique (colorisation Gantt) — "planned" par défaut.
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)

    port_stay_planned_hours: Mapped[int | None] = mapped_column(Integer)
    transit_speed_kn: Mapped[float | None] = mapped_column(Float)
    elongation_coef: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_scenario_legs_etd", "etd"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScenarioLeg {self.id} sc={self.scenario_id} {self.etd.date()}→{self.eta.date()}>"
