"""Carnet de Bord ANEMOS - Router.

Endpoints pour :
- Gnrer le Carnet de Bord pour un leg
- Prvisualiser le Carnet de Bord
- Grer les points remarquables
- Grer les photos de voyage
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.leg import Leg
from app.models.user import User
from app.models.voyage_highlight import VoyageHighlight, HIGHLIGHT_CATEGORIES
from app.models.voyage_photo import VoyagePhoto, BATCH_CATEGORIES, PHOTO_CATEGORIES
from app.permissions import get_current_user, require_permission
from app.schemas.voyage_highlight import (
    VoyageHighlightCreate,
    VoyageHighlightList,
    VoyageHighlightUpdate,
)
from app.schemas.voyage_photo import (
    VoyagePhotoCreate,
    VoyagePhotoList,
    VoyagePhotoUpdate,
)
from app.services.carnet_bord import generate_carnet_bord_pdf, get_carnet_bord_data

router = APIRouter(prefix="/carnet-bord", tags=["Carnet de Bord ANEMOS"])


# =============================================================================
# Carnet de Bord - Gnration
# =============================================================================

@router.get("/legs/{leg_id}/preview")
async def preview_carnet_bord(
    leg_id: int,
    client_account_id: int | None = Query(None, description="ID du client pour personnalisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Prvisualise le Carnet de Bord pour un leg (HTML)."""
    from app.templating import render_template
    from app.services.carnet_bord import get_carnet_bord_data

    # Vrifier que le leg existe
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouv")

    # Rcuprer les donnes
    data = await get_carnet_bord_data(db, leg_id, client_account_id)

    # Prparer le contexte
    context = {
        "leg": data.leg,
        "vessel": data.vessel,
        "pol": data.pol,
        "pod": data.pod,
        "client": data.client,
        "generated_at": data.generated_at,
        # Ajouter les donnes ncessaires pour chaque chapitre
        "cover_photo": data.cover_photo,
        "route_map_image": data.route_map_image,
        "anemos_logo": data.anemos_logo,
        "gps_trace": data.gps_trace,
        "highlights": data.highlights,
        "distance_nm": data.distance_nm,
        "duration_days": data.duration_days,
        "sog_avg": data.sog_avg,
        "sog_max": data.sog_max,
        "propulsion_stats": data.propulsion_stats,
        "crew_members": data.crew_members,
        "crew_photos": data.crew_photos,
        "crew_description": data.crew_description,
        "crew_org_chart": data.crew_org_chart,
        "total_palettes": data.total_palettes,
        "client_palettes": data.client_palettes,
        "total_weight_kg": data.total_weight_kg,
        "client_weight_kg": data.client_weight_kg,
        "fill_rate_surface": data.fill_rate_surface,
        "fill_rate_weight": data.fill_rate_weight,
        "products": data.products,
        "loading_photos": data.loading_photos,
        "hold_data": data.hold_data,
        "temp_avg": data.temp_avg,
        "temp_min": data.temp_min,
        "temp_max": data.temp_max,
        "humidity_avg": data.humidity_avg,
        "humidity_min": data.humidity_min,
        "humidity_max": data.humidity_max,
        "temp_chart": data.temp_chart,
        "hold_comments": data.hold_comments,
        "co2_avoided_kg": data.co2_avoided_kg,
        "co2_emitted_kg": data.co2_emitted_kg,
        "co2_conventional_kg": data.co2_conventional_kg,
        "decarbonation_rate": data.decarbonation_rate,
        "fuel_consumed_l": data.fuel_consumed_l,
        "emission_rate": data.emission_rate,
        "towt_factor": data.towt_factor,
        "conventional_factor": data.conventional_factor,
        "method": data.method,
        "distance_source": data.distance_source,
        "verification_statement": data.verification_statement,
        "sailing_hours": data.sailing_hours,
        "assisted_hours": data.assisted_hours,
        "motor_hours": data.motor_hours,
        "total_hours": data.total_hours,
        "sail_pct": data.sail_pct,
        "assisted_pct": data.assisted_pct,
        "motor_pct": data.motor_pct,
        "engine_data": data.engine_data,
        "sail_trim_data": data.sail_trim_data,
        "weather_images": data.weather_images,
        "weather_stats": data.weather_stats,
        "weather_events": data.weather_events,
        "timeline_events": data.timeline_events,
        "timeline_stats": data.timeline_stats,
        "etd_eta_info": data.etd_eta_info,
        "conclusion_message": data.conclusion_message,
        "upcoming_legs": data.upcoming_legs,
        "contacts": data.contacts,
        "qr_album": data.qr_album,
        "qr_album_image": data.qr_album_image,
        "qr_anemos": data.qr_anemos,
        "qr_anemos_image": data.qr_anemos_image,
    }

    html = await render_template("pdf/carnet_bord.html", **context)
    return Response(content=html, media_type="text/html")


@router.get("/legs/{leg_id}/pdf")
async def generate_carnet_bord_pdf(
    leg_id: int,
    client_account_id: int | None = Query(None, description="ID du client pour personnalisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Gnre et tlcharge le Carnet de Bord pour un leg (PDF)."""
    # Vrifier que le leg existe
    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouv")

    # Gnrer le PDF
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
# Points Remarquables (Voyage Highlights)
# =============================================================================

@router.get("/legs/{leg_id}/highlights", response_model=VoyageHighlightList)
async def list_voyage_highlights(
    leg_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyageHighlightList:
    """Liste les points remarquables pour un leg."""
    from sqlalchemy import select

    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouv")

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


@router.post("/legs/{leg_id}/highlights", response_model=VoyageHighlight)
async def create_voyage_highlight(
    leg_id: int,
    highlight: VoyageHighlightCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyageHighlight:
    """Cre un nouveau point remarquable."""
    from sqlalchemy import select

    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouv")

    # Vrifier la catgorie
    if highlight.category not in HIGHLIGHT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catgorie invalide. Choix possibles: {', '.join(HIGHLIGHT_CATEGORIES)}",
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
        created_by=current_user.name or current_user.email,
    )

    db.add(new_highlight)
    await db.flush()
    await db.refresh(new_highlight)

    return new_highlight


@router.get("/highlights/{highlight_id}", response_model=VoyageHighlight)
async def get_voyage_highlight(
    highlight_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyageHighlight:
    """Rcupre un point remarquable."""
    highlight = await db.get(VoyageHighlight, highlight_id)
    if not highlight:
        raise HTTPException(status_code=404, detail="Point remarquable non trouv")
    return highlight


@router.put("/highlights/{highlight_id}", response_model=VoyageHighlight)
async def update_voyage_highlight(
    highlight_id: int,
    highlight: VoyageHighlightUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyageHighlight:
    """Met  jour un point remarquable."""
    existing = await db.get(VoyageHighlight, highlight_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Point remarquable non trouv")

    # Vrifier la catgorie
    if highlight.category and highlight.category not in HIGHLIGHT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catgorie invalide. Choix possibles: {', '.join(HIGHLIGHT_CATEGORIES)}",
        )

    for key, value in highlight.model_dump(exclude_unset=True).items():
        setattr(existing, key, value)

    existing.updated_at = datetime.utcnow()
    existing.updated_by = current_user.name or current_user.email

    await db.flush()
    await db.refresh(existing)

    return existing


@router.delete("/highlights/{highlight_id}", status_code=204)
async def delete_voyage_highlight(
    highlight_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Supprime un point remarquable."""
    highlight = await db.get(VoyageHighlight, highlight_id)
    if not highlight:
        raise HTTPException(status_code=404, detail="Point remarquable non trouv")

    await db.delete(highlight)
    await db.flush()


# =============================================================================
# Photos de Voyage
# =============================================================================

@router.get("/legs/{leg_id}/photos", response_model=VoyagePhotoList)
async def list_voyage_photos(
    leg_id: int,
    batch_id: str | None = Query(None, description="Filtrer par batch"),
    category: str | None = Query(None, description="Filtrer par catgorie"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyagePhotoList:
    """Liste les photos pour un leg."""
    from sqlalchemy import select

    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouv")

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


@router.post("/legs/{leg_id}/photos", response_model=VoyagePhoto)
async def create_voyage_photo(
    leg_id: int,
    photo: VoyagePhotoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyagePhoto:
    """Ajoute une photo  un leg."""
    from sqlalchemy import select

    leg = await db.get(Leg, leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail="Leg non trouv")

    # Vrifier la catgorie de batch
    if photo.batch_id not in BATCH_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Batch invalide. Choix possibles: {', '.join(BATCH_CATEGORIES)}",
        )

    # Vrifier la catgorie de photo
    if photo.category not in PHOTO_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catgorie invalide. Choix possibles: {', '.join(PHOTO_CATEGORIES)}",
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
        uploaded_by_name=current_user.name or current_user.email,
        display_order=photo.display_order or 0,
    )

    db.add(new_photo)
    await db.flush()
    await db.refresh(new_photo)

    return new_photo


@router.get("/photos/{photo_id}", response_model=VoyagePhoto)
async def get_voyage_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyagePhoto:
    """Rcupre une photo."""
    photo = await db.get(VoyagePhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo non trouve")
    return photo


@router.put("/photos/{photo_id}", response_model=VoyagePhoto)
async def update_voyage_photo(
    photo_id: int,
    photo: VoyagePhotoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VoyagePhoto:
    """Met  jour une photo."""
    existing = await db.get(VoyagePhoto, photo_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Photo non trouve")

    # Vrifier la catgorie de batch
    if photo.batch_id and photo.batch_id not in BATCH_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Batch invalide. Choix possibles: {', '.join(BATCH_CATEGORIES)}",
        )

    # Vrifier la catgorie de photo
    if photo.category and photo.category not in PHOTO_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Catgorie invalide. Choix possibles: {', '.join(PHOTO_CATEGORIES)}",
        )

    for key, value in photo.model_dump(exclude_unset=True).items():
        setattr(existing, key, value)

    await db.flush()
    await db.refresh(existing)

    return existing


@router.delete("/photos/{photo_id}", status_code=204)
async def delete_voyage_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Supprime une photo."""
    photo = await db.get(VoyagePhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo non trouve")

    await db.delete(photo)
    await db.flush()
