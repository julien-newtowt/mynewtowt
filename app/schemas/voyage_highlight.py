"""Schemas for VoyageHighlight model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.voyage_highlight import HIGHLIGHT_CATEGORIES


class VoyageHighlightBase(BaseModel):
    """Base schema for VoyageHighlight."""

    latitude: float = Field(..., description="Latitude du point remarquable")
    longitude: float = Field(..., description="Longitude du point remarquable")
    occurred_at: datetime = Field(..., description="Date et heure de l'événement")
    title: str = Field(..., max_length=200, description="Titre du point remarquable")
    description: str | None = Field(
        None, max_length=10000, description="Description du point remarquable"
    )
    category: str = Field(
        default="navigation",
        description=f"Catégorie du point. Choix: {', '.join(HIGHLIGHT_CATEGORIES)}",
    )
    photo_id: int | None = Field(None, description="ID de la photo associée")
    display_order: int = Field(default=0, description="Ordre d'affichage")


class VoyageHighlightCreate(VoyageHighlightBase):
    """Schema for creating a new VoyageHighlight."""

    pass


class VoyageHighlightUpdate(BaseModel):
    """Schema for updating a VoyageHighlight."""

    latitude: float | None = Field(None, description="Latitude du point remarquable")
    longitude: float | None = Field(None, description="Longitude du point remarquable")
    occurred_at: datetime | None = Field(None, description="Date et heure de l'événement")
    title: str | None = Field(None, max_length=200, description="Titre du point remarquable")
    description: str | None = Field(
        None, max_length=10000, description="Description du point remarquable"
    )
    category: str | None = Field(
        None,
        description=f"Catégorie du point. Choix: {', '.join(HIGHLIGHT_CATEGORIES)}",
    )
    photo_id: int | None = Field(None, description="ID de la photo associée")
    display_order: int | None = Field(None, description="Ordre d'affichage")


class VoyageHighlight(VoyageHighlightBase):
    """Full schema for VoyageHighlight (includes id and timestamps)."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="ID unique du point remarquable")
    leg_id: int = Field(..., description="ID du leg associé")
    created_at: datetime = Field(..., description="Date de création")
    created_by: str | None = Field(None, description="Créé par")
    updated_at: datetime | None = Field(None, description="Date de modification")
    updated_by: str | None = Field(None, description="Modifié par")


class VoyageHighlightList(BaseModel):
    """Schema for listing VoyageHighlights."""

    leg_id: int = Field(..., description="ID du leg")
    highlights: list[VoyageHighlight] = Field(default_factory=list)
    total: int = Field(default=0, description="Nombre total de points remarquables")
