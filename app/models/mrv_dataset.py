"""Sorties réglementaires OVDLA / OVDBR — entrées gelées (LOT 10).

Couche 3 (restitution) de l'architecture événementielle : les deux datasets
déposés chez DNV remplacent l'ancien export CSV 18 colonnes.

- ``MrvLogAbstractEntry`` — 1:1 avec un événement de navigation **validé**
  (``nav_events``) : une ligne OVDLA (*Log Abstract*) **gelée** dans ``payload``
  (le snapshot est la source du fichier déposé — reproductibilité d'audit).
- ``MrvBunkeringEntry`` — 1:1 avec un soutage **validé Master**
  (``bunker_operations``) : une ligne OVDBR (*Bunker Report*) gelée.

``verification_status`` porte la taxonomie qualité transverse (dictionnaire
§2.2) ; ``under_conformity`` **bloque la consolidation** (l'événement/soutage
n'entre pas dans le dataset et déclenche une alerte — cf.
``services.mrv_dataset``). ``source_system`` vaut ``MyTOWT`` (décision Q10 :
l'émetteur du format est MyTOWT, non « OVDAdmin » observé dans les échantillons
2025 produits par l'ancien outil).

Le ``payload`` est le **dict de la ligne** (en-têtes OVDLA/OVDBR exacts →
valeurs), sérialisé JSON (Decimals en chaînes, précision préservée). Il est
gelé au snapshot : régénérer ne réécrit le payload que si l'entrée n'est pas
déjà figée par une vérification (``verification_status`` conservé).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Taxonomie qualité (identique à ``env_report.QUALITY_STATUSES``) — dupliquée
# ici comme vocabulaire documenté du dataset (``under_conformity`` = porte).
VERIFICATION_STATUSES: tuple[str, ...] = (
    "conform",
    "corrected",
    "clarified",
    "under_conformity",
)

# Système émetteur du format (Q10). Constante pour éviter toute divergence.
SOURCE_SYSTEM_DEFAULT = "MyTOWT"


class MrvLogAbstractEntry(Base):
    """Ligne OVDLA gelée — 1:1 avec un événement de navigation validé."""

    __tablename__ = "mrv_log_abstract_entries"
    __table_args__ = (
        Index("ix_mrv_log_abstract_event", "event_id"),
        Index("ix_mrv_log_abstract_status", "verification_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("nav_events.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    source_system: Mapped[str] = mapped_column(
        String(40), nullable=False,
        default=SOURCE_SYSTEM_DEFAULT, server_default=SOURCE_SYSTEM_DEFAULT,
    )
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="conform", server_default="conform"
    )
    # Ligne OVDLA gelée (en-têtes exacts → valeurs). Source du fichier déposé.
    payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MrvLogAbstractEntry event={self.event_id} {self.verification_status}>"


class MrvBunkeringEntry(Base):
    """Ligne OVDBR gelée — 1:1 avec un soutage validé Master."""

    __tablename__ = "mrv_bunkering_entries"
    __table_args__ = (
        Index("ix_mrv_bunkering_bunker", "bunker_id"),
        Index("ix_mrv_bunkering_status", "verification_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bunker_id: Mapped[int] = mapped_column(
        ForeignKey("bunker_operations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    source_system: Mapped[str] = mapped_column(
        String(40), nullable=False,
        default=SOURCE_SYSTEM_DEFAULT, server_default=SOURCE_SYSTEM_DEFAULT,
    )
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="conform", server_default="conform"
    )
    payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MrvBunkeringEntry bunker={self.bunker_id} {self.verification_status}>"
