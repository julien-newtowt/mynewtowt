"""Historique des recalculs de planification (règle métier n°7).

Chaque modification de dates prévisionnelles d'un leg — édition planning,
drag-drop Gantt, déclaration d'ETA-shift capitaine, ou propagation en
cascade — écrit une ligne ici. Les lignes d'un même événement partagent un
``batch_id`` : on peut reconstituer « qui a déclenché quoi » (le leg source
porte la source d'origine, les legs aval portent ``source="cascade"`` +
``trigger_leg_id``).

La table est append-only et **survit à la suppression du leg** (FK déliées
à la suppression, snapshot ``leg_code`` conservé) — contrairement à
``eta_shifts`` qui suit le leg (ondelete CASCADE).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Origines possibles d'une révision de planification.
REVISION_SOURCES: tuple[str, ...] = ("planning_edit", "gantt_move", "eta_shift", "cascade")


class ScheduleRevision(Base):
    __tablename__ = "schedule_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int | None] = mapped_column(
        ForeignKey("legs.id", ondelete="SET NULL"), index=True
    )
    # Snapshot du code au moment de la révision (le code peut être
    # renuméroté ensuite, le leg supprimé — l'historique reste lisible).
    leg_code: Mapped[str | None] = mapped_column(String(20))
    vessel_id: Mapped[int | None] = mapped_column(Integer, index=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_leg_id: Mapped[int | None] = mapped_column(ForeignKey("legs.id", ondelete="SET NULL"))

    old_etd: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_etd: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    old_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Motif (repris de l'ETA-shift capitaine) + détail libre.
    reason: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)

    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_schedule_revisions_created", "created_at"),)
