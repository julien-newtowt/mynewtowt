"""Page publique de voyage — destination du QR B2B2C (« scannez, suivez »).

``/voyage/{ref}`` raconte au consommateur final la traversée réelle d'une
expédition : navire, route suivie (trace GPS), dates, conditions de cale
(température / humidité relevées à bord), CO₂ évité (certificat Anemos
vérifiable) et récit d'origine. C'est le dernier maillon de la cascade
B2B2C : le torréfacteur imprime le QR sur le paquet, le consommateur lit
la preuve.

Publication sur opt-in explicite du client (``Booking.voyage_public``,
espace ``/me``) et uniquement pour une traversée commencée. Dépubliable à
tout moment.

Sécurité / vie privée :
- AUCUNE PII : ni nom de client, ni adresses, ni prix — seul le co-branding
  (nom de marque + logo, configurés volontairement dans ``/me/brand``) est
  affiché.
- Rate-limit par IP (scope ``voyage_page``) — page et médias — pour freiner
  l'énumération de références ; référence inconnue ou non publiée → rendu
  neutre 404 (aucune différence observable entre « inexistant » et « non
  publié »).
- Événement ``voyage_page_view`` (analytics_events) : compte les scans QR
  consommateur — la North Star marketing B2B2C.
"""

from __future__ import annotations

import math
import mimetypes
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.claim import VesselPosition
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_photo import VoyagePhoto
from app.services import analytics, coffee_stories, rate_limit, safe_files
from app.services import hold_conditions as hold_conditions_svc
from app.templating import templates

router = APIRouter(prefix="/voyage", tags=["voyage-public"])

# La page n'existe que pour une traversée commencée (rien à raconter avant).
_PUBLIC_STATUSES = ("loaded", "at_sea", "discharged", "delivered")

# Rate-limit par IP : une consultation charge la page + jusqu'à ~10 médias.
_RATE_SCOPE = "voyage_page"
_RATE_MAX_ATTEMPTS = 240
_RATE_WINDOW_MINUTES = 10

# Trace GPS sous-échantillonnée (payload page raisonnable).
_MAX_TRACK_POINTS = 200
_MAX_PHOTOS = 8


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def _rate_limited(db: AsyncSession, request: Request) -> bool:
    ip = _client_ip(request)
    if await rate_limit.exceeded(
        db,
        scope=_RATE_SCOPE,
        identifier=ip,
        max_attempts=_RATE_MAX_ATTEMPTS,
        window_minutes=_RATE_WINDOW_MINUTES,
    ):
        return True
    await rate_limit.record(db, scope=_RATE_SCOPE, identifier=ip)
    return False


async def _published_booking(db: AsyncSession, ref: str) -> Booking | None:
    """Réservation publiée uniquement — sinon ``None`` (rendu neutre)."""
    reference = (ref or "").strip().upper()
    if not reference or len(reference) > 40:
        return None
    booking = (
        await db.execute(select(Booking).where(Booking.reference == reference))
    ).scalar_one_or_none()
    if booking is None or not booking.voyage_public or booking.status not in _PUBLIC_STATUSES:
        return None
    return booking


async def _leg_track(
    db: AsyncSession, leg: Leg | None
) -> tuple[list[list[float]], VesselPosition | None]:
    """Trace GPS du voyage : positions du navire dans la fenêtre du leg."""
    if leg is None:
        return [], None
    window_start = leg.atd or leg.etd
    window_end = leg.ata or datetime.now(UTC)
    query = (
        select(VesselPosition)
        .where(VesselPosition.vessel_id == leg.vessel_id)
        .order_by(VesselPosition.recorded_at)
    )
    if window_start is not None:
        query = query.where(VesselPosition.recorded_at >= window_start)
    if window_end is not None:
        query = query.where(VesselPosition.recorded_at <= window_end)
    positions = list((await db.execute(query)).scalars().all())
    last = positions[-1] if positions else None
    if len(positions) > _MAX_TRACK_POINTS:
        step = math.ceil(len(positions) / _MAX_TRACK_POINTS)
        sampled = positions[::step]
        if sampled[-1] is not positions[-1]:
            sampled.append(positions[-1])
        positions = sampled
    track = [[round(p.longitude, 4), round(p.latitude, 4)] for p in positions]
    return track, last


def _is_public_photo(photo: VoyagePhoto) -> bool:
    """Filtre vie privée : jamais de portraits d'équipage sur la page publique."""
    return photo.batch_id != "crew" and photo.crew_member_id is None


async def _public_photos(db: AsyncSession, leg_id: int) -> list[VoyagePhoto]:
    rows = (
        await db.execute(
            select(VoyagePhoto)
            .where(VoyagePhoto.leg_id == leg_id)
            .where(VoyagePhoto.batch_id != "crew")
            .where(VoyagePhoto.crew_member_id.is_(None))
            .order_by(VoyagePhoto.display_order, VoyagePhoto.id)
            .limit(_MAX_PHOTOS)
        )
    ).scalars()
    return list(rows)


@router.get("/{ref}", response_class=HTMLResponse)
async def voyage_page(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Le voyage d'un lot, raconté au consommateur final (multilingue)."""
    if await _rate_limited(db, request):
        return templates.TemplateResponse(
            "public/voyage.html",
            {"request": request, "found": False, "rate_limited": True},
            status_code=429,
        )

    booking = await _published_booking(db, ref)
    if booking is None:
        return templates.TemplateResponse(
            "public/voyage.html",
            {"request": request, "found": False},
            status_code=404,
        )

    leg = await db.get(Leg, booking.leg_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None

    track, last_position = await _leg_track(db, leg)
    photos = await _public_photos(db, booking.leg_id)
    conditions = await hold_conditions_svc.for_leg(db, booking.leg_id)

    cert = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    co2_kg = int(cert.co2_avoided_kg) if cert and cert.co2_avoided_kg else None

    lang = getattr(request.state, "lang", "fr")
    origin = (
        booking.coffee_origin if coffee_stories.is_valid_origin(booking.coffee_origin) else None
    )
    story = None
    if origin:
        story = coffee_stories.render_story(
            origin,
            lang,
            "long",
            region=booking.coffee_region,
            producer=booking.coffee_producer,
            vessel=vessel.name if vessel else None,
            co2_kg=co2_kg,
        )

    # Co-branding : uniquement la marque configurée volontairement (/me/brand).
    brand_name = None
    has_brand_logo = False
    if booking.client_account_id:
        account = await db.get(ClientAccount, booking.client_account_id)
        if account is not None:
            brand_name = account.brand_name or None
            has_brand_logo = bool(account.brand_logo_path)

    duration_days = None
    if leg and leg.atd and leg.ata:
        duration_days = round((leg.ata - leg.atd).total_seconds() / 86400, 1)

    ports_json = [
        {"name": p.name, "lat": p.latitude, "lon": p.longitude, "kind": kind}
        for p, kind in ((pol, "pol"), (pod, "pod"))
        if p is not None and p.latitude is not None and p.longitude is not None
    ]

    # North Star B2B2C : chaque consultation ≈ un scan du QR imprimé.
    await analytics.record(
        db, "voyage_page_view", reference=booking.reference, lang=lang, channel="public"
    )

    return templates.TemplateResponse(
        "public/voyage.html",
        {
            "request": request,
            "found": True,
            "booking": booking,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "track": track,
            "ports_json": ports_json,
            "last_position": last_position,
            "photos": photos,
            "cert": cert,
            "co2_kg": co2_kg,
            "conditions": conditions,
            "story": story,
            "brand_name": brand_name,
            "has_brand_logo": has_brand_logo,
            "duration_days": duration_days,
            "maptiler_token": settings.map_token,
        },
    )


@router.get("/{ref}/photos/{photo_id}")
async def voyage_photo(
    ref: str,
    photo_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Sert une photo de bord de la page publique (images uniquement)."""
    if await _rate_limited(db, request):
        raise HTTPException(status_code=429, detail="Trop de requêtes")
    booking = await _published_booking(db, ref)
    if booking is None:
        raise HTTPException(status_code=404, detail="Not found")
    photo = await db.get(VoyagePhoto, photo_id)
    if photo is None or photo.leg_id != booking.leg_id or not _is_public_photo(photo):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        path = safe_files.resolve_path(photo.file_path)
    except (safe_files.UploadRejected, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Not found") from None
    mime = photo.file_mime or mimetypes.guess_type(path.name)[0] or ""
    if not mime.startswith("image/"):
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=path.read_bytes(),
        media_type=mime,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{ref}/brand-logo")
async def voyage_brand_logo(
    ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Sert le logo de marque du client pour le co-branding de la page."""
    if await _rate_limited(db, request):
        raise HTTPException(status_code=429, detail="Trop de requêtes")
    booking = await _published_booking(db, ref)
    if booking is None or not booking.client_account_id:
        raise HTTPException(status_code=404, detail="Not found")
    account = await db.get(ClientAccount, booking.client_account_id)
    if account is None or not account.brand_logo_path:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        path = safe_files.resolve_path(account.brand_logo_path)
    except (safe_files.UploadRejected, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Not found") from None
    mime = mimetypes.guess_type(path.name)[0] or ""
    if not mime.startswith("image/"):
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=path.read_bytes(),
        media_type=mime,
        headers={"Cache-Control": "public, max-age=3600"},
    )
