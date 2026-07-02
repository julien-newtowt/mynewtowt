"""Carnet de Bord ANEMOS — Router staff.

Endpoints pour :
- Générer le Carnet de Bord d'un leg (préversion HTML + PDF téléchargeable)
- Gérer les points remarquables (VoyageHighlight)
- Gérer les photos de voyage (VoyagePhoto)

RBAC : module ``captain`` — consultation (C) pour la génération, modification
(M) pour créer/mettre à jour points et photos, suppression (S) pour effacer.
Le pendant client (PDF personnalisé par booking) vit dans
``client_dashboard_router`` (``/me/bookings/{ref}/carnet.pdf``).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.user import User
from app.models.voyage_highlight import HIGHLIGHT_CATEGORIES, VoyageHighlight
from app.models.voyage_photo import BATCH_CATEGORIES, PHOTO_CATEGORIES, VoyagePhoto
from app.permissions import require_permission
from app.schemas.voyage_highlight import (
    VoyageHighlight as VoyageHighlightRead,
)
from app.schemas.voyage_highlight import (
    VoyageHighlightCreate,
    VoyageHighlightList,
    VoyageHighlightUpdate,
)
from app.schemas.voyage_photo import (
    VoyagePhoto as VoyagePhotoRead,
)
from app.schemas.voyage_photo import (
    VoyagePhotoCreate,
    VoyagePhotoList,
    VoyagePhotoUpdate,
)
from app.services.carnet_bord import (
    build_carnet_context,
    generate_carnet_bord_pdf,
    get_carnet_bord_data,
)

router = APIRouter(prefix="/carnet-bord", tags=["Carnet de Bord ANEMOS"])


# =============================================================================
# Carnet de Bord — Génération
# =============================================================================


@router.get("/legs/{leg_id}/preview")
async def preview_carnet_bord(
    leg_id: int,
    client_account_id: int | None = Query(None, description="ID du client pour personnalisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "C")),
) -> Response:
    """Prévisualise le Carnet de Bord d'un leg (HTML, même contexte que le PDF)."""
    from app.templating import templates

    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouvé")

    data = await get_carnet_bord_data(db, leg_id, client_account_id)
    html = templates.get_template("pdf/carnet_bord.html").render(**build_carnet_context(data))
    return Response(content=html, media_type="text/html")


@router.get("/legs/{leg_id}/pdf")
async def download_carnet_bord_pdf(
    leg_id: int,
    client_account_id: int | None = Query(None, description="ID du client pour personnalisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "C")),
) -> Response:
    """Génère et télécharge le Carnet de Bord d'un leg (PDF WeasyPrint)."""
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouvé")

    pdf_bytes = await generate_carnet_bord_pdf(db, leg_id, client_account_id)

    filename = f"CarnetBord_ANEMOS_{leg.leg_code}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "application/pdf",
        },
    )


# =============================================================================
# Points remarquables (VoyageHighlight)
# =============================================================================


@router.get("/legs/{leg_id}/highlights", response_model=VoyageHighlightList)
async def list_voyage_highlights(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "C")),
) -> VoyageHighlightList:
    """Liste les points remarquables d'un leg."""
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouvé")

    highlights = await db.execute(
        select(VoyageHighlight)
        .where(VoyageHighlight.leg_id == leg_id)
        .order_by(VoyageHighlight.display_order, VoyageHighlight.occurred_at)
    )
    highlights = highlights.scalars().all()

    return VoyageHighlightList(
        leg_id=leg_id,
        highlights=highlights,
        total=len(highlights),
    )


@router.post("/legs/{leg_id}/highlights", response_model=VoyageHighlightRead)
async def create_voyage_highlight(
    leg_id: int,
    highlight: VoyageHighlightCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "M")),
) -> VoyageHighlight:
    """Crée un nouveau point remarquable."""
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouvé")

    if highlight.category not in HIGHLIGHT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catégorie invalide. Choix possibles: {', '.join(HIGHLIGHT_CATEGORIES)}",
        )

    new_highlight = VoyageHighlight(
        leg_id=leg_id,
        latitude=highlight.latitude,
        longitude=highlight.longitude,
        occurred_at=highlight.occurred_at,
        title=highlight.title,
        description=highlight.description,
        category=highlight.category,
        photo_id=highlight.photo_id,
        display_order=highlight.display_order or 0,
        created_at=datetime.utcnow(),
        created_by=current_user.full_name or current_user.username,
    )

    db.add(new_highlight)
    await db.flush()
    await db.refresh(new_highlight)

    return new_highlight


@router.get("/highlights/{highlight_id}", response_model=VoyageHighlightRead)
async def get_voyage_highlight(
    highlight_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "C")),
) -> VoyageHighlight:
    """Récupère un point remarquable."""
    highlight = await db.get(VoyageHighlight, highlight_id)
    if not highlight:
        raise HTTPException(status_code=404, detail="Point remarquable non trouvé")
    return highlight


@router.put("/highlights/{highlight_id}", response_model=VoyageHighlightRead)
async def update_voyage_highlight(
    highlight_id: int,
    highlight: VoyageHighlightUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "M")),
) -> VoyageHighlight:
    """Met à jour un point remarquable."""
    existing = await db.get(VoyageHighlight, highlight_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Point remarquable non trouvé")

    if highlight.category and highlight.category not in HIGHLIGHT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catégorie invalide. Choix possibles: {', '.join(HIGHLIGHT_CATEGORIES)}",
        )

    for key, value in highlight.model_dump(exclude_unset=True).items():
        setattr(existing, key, value)

    existing.updated_at = datetime.utcnow()
    existing.updated_by = current_user.full_name or current_user.username

    await db.flush()
    await db.refresh(existing)

    return existing


@router.delete("/highlights/{highlight_id}", status_code=204, response_class=Response)
async def delete_voyage_highlight(
    highlight_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "S")),
) -> Response:
    """Supprime un point remarquable."""
    highlight = await db.get(VoyageHighlight, highlight_id)
    if not highlight:
        raise HTTPException(status_code=404, detail="Point remarquable non trouvé")

    await db.delete(highlight)
    await db.flush()
    return Response(status_code=204)


# =============================================================================
# Photos de voyage (VoyagePhoto)
# =============================================================================


@router.get("/legs/{leg_id}/photos", response_model=VoyagePhotoList)
async def list_voyage_photos(
    leg_id: int,
    batch_id: str | None = Query(None, description="Filtrer par batch"),
    category: str | None = Query(None, description="Filtrer par catégorie"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "C")),
) -> VoyagePhotoList:
    """Liste les photos d'un leg."""
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouvé")

    query = select(VoyagePhoto).where(VoyagePhoto.leg_id == leg_id)

    if batch_id:
        query = query.where(VoyagePhoto.batch_id == batch_id)
    if category:
        query = query.where(VoyagePhoto.category == category)

    query = query.order_by(VoyagePhoto.batch_id, VoyagePhoto.display_order)

    photos = await db.execute(query)
    photos = photos.scalars().all()

    return VoyagePhotoList(
        leg_id=leg_id,
        batch_id=batch_id,
        category=category,
        photos=photos,
        total=len(photos),
    )


@router.post("/legs/{leg_id}/photos", response_model=VoyagePhotoRead)
async def create_voyage_photo(
    leg_id: int,
    photo: VoyagePhotoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "M")),
) -> VoyagePhoto:
    """Ajoute une photo à un leg."""
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouvé")

    if photo.batch_id not in BATCH_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Batch invalide. Choix possibles: {', '.join(BATCH_CATEGORIES)}",
        )

    if photo.category not in PHOTO_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catégorie invalide. Choix possibles: {', '.join(PHOTO_CATEGORIES)}",
        )

    new_photo = VoyagePhoto(
        leg_id=leg_id,
        batch_id=photo.batch_id,
        category=photo.category,
        label=photo.label,
        file_path=photo.file_path,
        file_mime=photo.file_mime,
        file_size=photo.file_size,
        original_name=photo.original_name,
        taken_at=photo.taken_at,
        latitude=photo.latitude,
        longitude=photo.longitude,
        highlight_id=photo.highlight_id,
        crew_member_id=photo.crew_member_id,
        uploaded_by_id=current_user.id,
        uploaded_by_name=current_user.full_name or current_user.username,
        display_order=photo.display_order or 0,
    )

    db.add(new_photo)
    await db.flush()
    await db.refresh(new_photo)

    return new_photo


@router.get("/photos/{photo_id}", response_model=VoyagePhotoRead)
async def get_voyage_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "C")),
) -> VoyagePhoto:
    """Récupère une photo."""
    photo = await db.get(VoyagePhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo non trouvée")
    return photo


@router.put("/photos/{photo_id}", response_model=VoyagePhotoRead)
async def update_voyage_photo(
    photo_id: int,
    photo: VoyagePhotoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "M")),
) -> VoyagePhoto:
    """Met à jour une photo."""
    existing = await db.get(VoyagePhoto, photo_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Photo non trouvée")

    if photo.batch_id and photo.batch_id not in BATCH_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Batch invalide. Choix possibles: {', '.join(BATCH_CATEGORIES)}",
        )

    if photo.category and photo.category not in PHOTO_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catégorie invalide. Choix possibles: {', '.join(PHOTO_CATEGORIES)}",
        )

    for key, value in photo.model_dump(exclude_unset=True).items():
        setattr(existing, key, value)

    await db.flush()
    await db.refresh(existing)

    return existing


@router.delete("/photos/{photo_id}", status_code=204, response_class=Response)
async def delete_voyage_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("captain", "S")),
) -> Response:
    """Supprime une photo."""
    photo = await db.get(VoyagePhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo non trouvée")

    await db.delete(photo)
    await db.flush()
    return Response(status_code=204)
