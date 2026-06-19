"""Journal des lots d'export Silae — lot L5 du SIRH.

Chaque lot fige l'export d'une catégorie (EVP) pour une période donnée vers
Silae (logiciel de paie). Le contenu CSV généré est conservé dans la ligne
(``content``) pour être re-téléchargeable depuis le journal, y compris en
conteneur éphémère. Voir ``docs/strategy/CAHIER_DES_CHARGES_SIRH.md`` §4.7 / §10.

Cycle d'un lot : ``generated`` (CSV produit) → ``sent`` (transmis à Silae) →
``acknowledged`` (accusé reçu) ; ``error`` en cas d'échec.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

EXPORT_BATCH_STATUSES: tuple[str, ...] = ("generated", "sent", "acknowledged", "error")
EXPORT_BATCH_KINDS: tuple[str, ...] = ("evp", "absences")


class SilaeExportBatch(Base):
    """Lot d'export vers Silae (journal des flux de paie)."""

    __tablename__ = "silae_export_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="evp", nullable=False)
    format: Mapped[str] = mapped_column(String(10), default="csv", nullable=False)

    file_path: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str | None] = mapped_column(Text)
    line_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="generated", nullable=False)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_silae_export_batches_period", "period"),)

    @property
    def filename(self) -> str:
        return f"silae_{self.kind}_{self.period}_{self.id}.csv"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SilaeExportBatch #{self.id} {self.kind} {self.period} "
            f"{self.line_count} lignes {self.status}>"
        )
