"""Schemas for VoyagePhoto model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.voyage_photo import BATCH_CATEGORIES, PHOTO_CATEGORIES


class VoyagePhotoBase(BaseModel):
    """Base schema for VoyagePhoto."""

    batch_id: str = Field(
        ...,
        max_length=50,
        description=f"ID du batch. Choix: {', '.join(BATCH_CATEGORIES)}",
    )
    category: str = Field(
        default="other",
        max_length=50,
        description=f"Catégorie de la photo. Choix: {', '.join(PHOTO_CATEGORIES)}",
    )
    label: str | None = Field(None, max_length=200, description="Légende de la photo")
    file_path: str = Field(..., max_length=500, description="Chemin du fichier")
    file_mime: str | None = Field(None, max_length=80, description="Type MIME du fichier")
    file_size: int | None = Field(None, description="Taille du fichier en octets")
    original_name: str | None = Field(None, max_length=255, description="Nom original du fichier")
    taken_at: datetime | None = Field(None, description="Date et heure de prise de vue")
    latitude: float | None = Field(None, description="Latitude de prise de vue")
    longitude: float | None = Field(None, description="Longitude de prise de vue")
    highlight_id: int | None = Field(None, description="ID du point remarquable associé")
    crew_member_id: int | None = Field(None, description="ID du membre d'équipage")
    display_order: int = Field(default=0, description="Ordre d'affichage dans le batch")


class VoyagePhotoCreate(VoyagePhotoBase):
    """Schema for creating a new VoyagePhoto."""

    pass


class VoyagePhotoUpdate(BaseModel):
    """Schema for updating a VoyagePhoto."""

    batch_id: str | None = Field(
        None,
        max_length=50,
        description=f"ID du batch. Choix: {', '.join(BATCH_CATEGORIES)}",
    )
    category: str | None = Field(
        None,
        max_length=50,
        description=f"Catégorie de la photo. Choix: {', '.join(PHOTO_CATEGORIES)}",
    )
    label: str | None = Field(None, max_length=200, description="Légende de la photo")
    file_path: str | None = Field(None, max_length=500, description="Chemin du fichier")
    file_mime: str | None = Field(None, max_length=80, description="Type MIME du fichier")
    file_size: int | None = Field(None, description="Taille du fichier en octets")
    original_name: str | None = Field(None, max_length=255, description="Nom original du fichier")
    taken_at: datetime | None = Field(None, description="Date et heure de prise de vue")
    latitude: float | None = Field(None, description="Latitude de prise de vue")
    longitude: float | None = Field(None, description="Longitude de prise de vue")
    highlight_id: int | None = Field(None, description="ID du point remarquable associé")
    crew_member_id: int | None = Field(None, description="ID du membre d'équipage")
    display_order: int | None = Field(None, description="Ordre d'affichage dans le batch")


class VoyagePhoto(VoyagePhotoBase):
    """Full schema for VoyagePhoto (includes id and timestamps)."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="ID unique de la photo")
    leg_id: int = Field(..., description="ID du leg associé")
    uploaded_by_id: int | None = Field(None, description="ID de l'utilisateur qui a uploadé")
    uploaded_by_name: str | None = Field(None, description="Nom de l'utilisateur qui a uploadé")
    uploaded_at: datetime = Field(..., description="Date de l'upload")


class VoyagePhotoList(BaseModel):
    """Schema for listing VoyagePhotos."""

    leg_id: int = Field(..., description="ID du leg")
    batch_id: str | None = Field(None, description="Batch filtré")
    category: str | None = Field(None, description="Catégorie filtrée")
    photos: list[VoyagePhoto] = Field(default_factory=list)
    total: int = Field(default=0, description="Nombre total de photos")
