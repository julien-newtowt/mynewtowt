"""Voyage Highlight  Points remarquables d'une traverse pour le Carnet de Bord ANEMOS.

Ces points sont slectionns manuellement (MAN) pour mettre en valeur des
vnements, lieux ou moments marquants de la traverse. Ils sont associs  des
photos (via VoyagePhoto) et apparaissent dans le Chapitre 1 (La Traverse).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.leg import Leg
    from app.models.voyage_photo import VoyagePhoto

# Catgories de points remarquables
HIGHLIGHT_CATEGORIES: tuple[str, ...] = (
    "port",  # Port de dpart, arrive, escale
    "meteorology",  # vnement mto remarquable
    "navigation",  # Manuvre, changement de cap, etc.
    "cargo",  # Opration de chargement/dchargement
    "crew",  # vnement li  l'quipage
    "vessel",  # Incident ou particularit sur le navire
    "wildlife",  # Observation faune marine
    "landscape",  # Paysage remarquable
)


class VoyageHighlight(Base):
    """Point remarquable d'une traverse."""

    __tablename__ = "voyage_highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[int] = mapped_column(
        ForeignKey("legs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Position gographique
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # Horodatage
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Contenu
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="navigation")

    # Photo associe (optionnelle) - relation avec VoyagePhoto
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("voyage_photos.id"))
    photo: Mapped[VoyagePhoto | None] = relationship(
        "VoyagePhoto",
        back_populates="highlight",
        uselist=False,
        foreign_keys="VoyagePhoto.highlight_id",
    )

    # Ordre d'affichage dans le carnet
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    # Mtadonnes de cration
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))

    # Mtadonnes de modification
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(100))

    # Relation avec le leg
    leg: Mapped[Leg] = relationship("Leg", back_populates="highlights")

    def __repr__(self) -> str:
        return f"<VoyageHighlight {self.title} @ {self.occurred_at}>"
