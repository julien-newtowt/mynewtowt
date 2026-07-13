"""Rapports générés & workflow de validation MRV (LOT 5).

Couche 3 (restitution) de l'architecture événementielle : les **rapports sont
des documents générés** depuis le magasin d'événements (``nav_events``), jamais
ressaisis. Trois tables (plan §2.2) :

- ``env_reports`` — un document généré (Noon / Carbon / Stopover / Certificate).
  Le ``payload`` JSON est le **snapshot** des champs générés : le PDF est rendu
  depuis ce snapshot (reproductibilité d'audit), jamais recalculé au rendu.
  Workflow : ``brouillon`` → ``attente_validation_master`` → ``valide_master``
  (Master, bord) → ``valide_siege`` (siège, Carbon uniquement).
- ``env_report_event_links`` — traçabilité rapport ↔ événements sources (N:N).
- ``env_field_modifications`` — corrections post-validation tracées (R18) :
  double FK nullable (rapport OU événement — la modification d'une position
  manuelle d'événement, R05, s'y range aussi) ; taxonomie qualité 4 statuts
  (``conform``/``corrected``/``clarified``/``under_conformity``) — le pire
  statut des modifications d'un rapport bloque la consolidation dataset (lot 10).

Convention d'unités : le payload porte des valeurs déjà normalisées (masses t,
distances nm…) sérialisées en chaînes (précision Decimal préservée).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ════════════════════════════════════════════════════════ Vocabulaires

# Types de rapport générés (plan §2.2). ``certificate`` est réservé (émis par
# la chaîne Anemos existante) — présent dans l'énumération pour cohérence.
REPORT_TYPES: tuple[str, ...] = ("noon", "carbon", "stopover", "certificate")

# Cycle de vie du rapport (CDC §9) : la validation siège est un 2ᵉ niveau
# réservé au Carbon (les autres types s'arrêtent à ``valide_master``).
REPORT_STATUSES: tuple[str, ...] = (
    "brouillon",
    "attente_validation_master",
    "valide_master",
    "valide_siege",
)

# Statuts encore régénérables (le payload est remplacé) ; au-delà, immuable.
MUTABLE_STATUSES: frozenset[str] = frozenset({"brouillon", "attente_validation_master"})

# Taxonomie qualité transverse (dictionnaire §2.2, CFOTE_09 C143:C162).
QUALITY_STATUSES: tuple[str, ...] = (
    "conform",
    "corrected",
    "clarified",
    "under_conformity",
)

# Rang de gravité pour dériver le « pire » statut d'un rapport. ``under_conformity``
# est le plus grave : il bloque la consolidation du dataset réglementaire (lot 10).
_QUALITY_RANK: dict[str, int] = {
    "conform": 0,
    "corrected": 1,
    "clarified": 2,
    "under_conformity": 3,
}


def worst_quality_status(statuses: Iterable[str]) -> str | None:
    """Pire statut qualité d'une liste (``under_conformity`` domine) ; None si vide.

    Fonction pure — réutilisée par le service (dérivation du statut d'un
    rapport) et par le lot 10 (porte de consolidation : un rapport
    ``under_conformity`` est exclu du dataset).
    """
    ranked = [(s, _QUALITY_RANK.get(s, 0)) for s in statuses if s]
    if not ranked:
        return None
    return max(ranked, key=lambda pair: pair[1])[0]


# ════════════════════════════════════════════════════════ Table env_reports


class EnvReport(Base):
    """Document environnemental généré depuis les événements d'un voyage."""

    __tablename__ = "env_reports"
    __table_args__ = (
        Index("ix_env_reports_leg", "leg_id"),
        Index("ix_env_reports_type", "report_type"),
        Index("ix_env_reports_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Voyage rattaché (= Leg existant). Pour un Stopover, c'est le voyage
    # d'arrivée (l'escale conclut ce voyage) ; les deux événements sont liés.
    leg_id: Mapped[int] = mapped_column(ForeignKey("legs.id", ondelete="CASCADE"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="brouillon", server_default="brouillon"
    )
    # Snapshot des champs générés — source unique du rendu PDF (audit).
    payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    validated_master_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_master_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    validated_siege_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_siege_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    event_links: Mapped[list[EnvReportEventLink]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    modifications: Mapped[list[EnvFieldModification]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EnvFieldModification.timestamp_utc",
    )

    @property
    def quality_status(self) -> str | None:
        """Pire statut qualité de ses modifications (``champ dérivé``, non stocké).

        Lit la collection **déjà chargée** (``selectin``) sans jamais déclencher
        d'I/O paresseuse (``__dict__`` : absent = non chargé ⇒ None). Consommé
        par le lot 10 pour bloquer la consolidation d'un rapport
        ``under_conformity``.
        """
        mods = self.__dict__.get("modifications")
        if not mods:
            return None
        return worst_quality_status(m.resulting_quality_status for m in mods)

    @property
    def under_conformity(self) -> bool:
        return self.quality_status == "under_conformity"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EnvReport#{self.id} {self.report_type}/{self.status} leg={self.leg_id}>"


class EnvReportEventLink(Base):
    """Lien N:N rapport ↔ événement source (PK composite)."""

    __tablename__ = "env_report_event_links"
    __table_args__ = (Index("ix_env_report_links_event", "event_id"),)

    report_id: Mapped[int] = mapped_column(
        ForeignKey("env_reports.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), primary_key=True
    )

    report: Mapped[EnvReport] = relationship(back_populates="event_links")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EnvReportEventLink report={self.report_id} event={self.event_id}>"


class EnvFieldModification(Base):
    """Correction tracée d'un champ pré-rempli/généré (R18) — justification obligatoire."""

    __tablename__ = "env_field_modifications"
    __table_args__ = (
        Index("ix_env_field_mods_report", "report_id"),
        Index("ix_env_field_mods_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Double FK nullable : la modification cible un rapport OU un événement
    # (position manuelle R05) — au moins l'un des deux est renseigné.
    report_id: Mapped[int | None] = mapped_column(ForeignKey("env_reports.id", ondelete="CASCADE"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("nav_events.id", ondelete="SET NULL"))
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    initial_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    justification_text: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    timestamp_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resulting_quality_status: Mapped[str] = mapped_column(String(20), nullable=False)

    report: Mapped[EnvReport | None] = relationship(back_populates="modifications")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EnvFieldModification#{self.id} {self.field_name} → {self.resulting_quality_status}>"
        )
