"""Archivage des documents PDF générés serveur (trombinoscope, futurs rapports).

Contrairement aux modèles métier (crew, booking...), cette table ne stocke
que des métadonnées de fichiers déjà générés — jamais de données métier
dupliquées. Cf. docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md §4.2.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeneratedReport(Base):
    __tablename__ = "generated_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "trombinoscope" pour l'instant — champ libre pour rester extensible à
    # d'autres rapports générés à l'avenir sans nouvelle table.
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "YYYY-MM"
    # Chemin relatif sous settings.upload_dir, résolu via services.safe_files.resolve_path.
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL = génération automatique (cron externe), sinon utilisateur à l'origine
    # d'une génération manuelle.
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
