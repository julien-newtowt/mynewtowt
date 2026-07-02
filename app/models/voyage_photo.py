"""Voyage Photo — Photos associées à un leg pour le Carnet de Bord ANEMOS.

Les photos sont organisées en **batches** (lots) correspondant à des moments ou
catégories spécifiques de la traversée. Chaque batch peut contenir plusieurs photos.

Sources :
- Photos de chargement/déchargement (module Onboarding)
- Photos d'équipage (module Crew - organigramme)
- Photos de navigation (module Onboarding ou upload manuel)
- Photos de points remarquables (liées à VoyageHighlight)

Les fichiers sont stockés via le service `safe_files` avec validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.leg import Leg
    from app.models.voyage_highlight import VoyageHighlight

# Catégories de batches (lots de photos)
BATCH_CATEGORIES: tuple[str, ...] = (
    "loading",  # Chargement de la cargaison
    "unloading",  # Déchargement de la cargaison
    "departure",  # Départ du port
    "arrival",  # Arrivée au port
    "navigation",  # En mer (navigation)
    "crew",  # Équipage (organigramme)
    "vessel",  # Navire (général)
    "cargo",  # Cargaison (général)
    "port_pol",  # Port de départ (POL)
    "port_pod",  # Port d'arrivée (POD)
    "escale",  # Escale
    "highlight",  # Point remarquable (lié à VoyageHighlight)
    "meteorology",  # Météo
    "other",  # Autres
)

# Catégories de photos (plus précis que le batch)
PHOTO_CATEGORIES: tuple[str, ...] = (
    "crew_portrait",  # Portrait d'un membre d'équipage
    "crew_group",  # Photo de groupe
    "cargo_palettes",  # Palettes de chargement
    "cargo_loading",  # Opération de chargement
    "cargo_unloading",  # Opération de déchargement
    "vessel_exterior",  # Extérieur du navire
    "vessel_interior",  # Intérieur du navire
    "vessel_sails",  # Voiles
    "port_view",  # Vue du port
    "sea_landscape",  # Paysage marin
    "navigation",  # Navigation (mer, horizon)
    "meteorology",  # Phénomène météo
    "wildlife",  # Faune marine
    "event",  # Événement spécifique
    "document",  # Document (BL, etc.)
    "other",  # Autres
)


class VoyagePhoto(Base):
    """Photo associée à un leg et à un batch."""

    __tablename__ = "voyage_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Batch auquel appartient la photo
    batch_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Catégorie de la photo
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="other")

    # Légende/description
    label: Mapped[str | None] = mapped_column(String(200))

    # Fichier
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_mime: Mapped[str | None] = mapped_column(String(80))
    file_size: Mapped[int | None] = mapped_column(Integer)  # en octets
    original_name: Mapped[str | None] = mapped_column(String(255))

    # Date et lieu de prise de vue (si disponibles)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # Lien avec un point remarquable (optionnel)
    highlight_id: Mapped[int | None] = mapped_column(ForeignKey("voyage_highlights.id"))
    highlight: Mapped[VoyageHighlight | None] = relationship(
        "VoyageHighlight",
        back_populates="photo",
        uselist=False,
        foreign_keys="VoyagePhoto.highlight_id",
    )

    # Lien avec un membre d'équipage (pour les portraits)
    crew_member_id: Mapped[int | None] = mapped_column(ForeignKey("crew_members.id"))

    # Upload
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_by_name: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Ordre d'affichage dans le batch
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relation avec le leg
    leg: Mapped[Leg] = relationship("Leg", back_populates="photos")

    def __repr__(self) -> str:
        return f"<VoyagePhoto {self.batch_id}/{self.id} - {self.label or self.file_path}>"
