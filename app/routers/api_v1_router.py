"""Public REST API v1 — for B2B integrations.

Authentication is API-key based (header `X-API-Key`). For V3.0 we expose
read-only routes; write endpoints will land in V3.1 with HMAC-signed
webhooks back to the client.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.schemas.booking import CapacityOut
from app.schemas.leg import LegPublic
from app.services.capacity import get_available_capacity, NotBookable

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "env": settings.app_env}


@router.get("/ports/nearby")
async def ports_nearby(
    lat: float,
    lon: float,
    radius_km: float = 50,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return active ports near a (lat, lon) within radius_km, sorted by distance."""
    from app.models.port import Port
    from app.services.ports import nearby_ports

    results = await nearby_ports(db, lat=lat, lon=lon, radius_km=radius_km, limit=limit)
    # Filter to active ports only (admins can hide entries)
    return [
        {
            "id": p.id,
            "locode": p.locode,
            "name": p.name,
            "country": p.country,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "distance_km": round(d, 2),
        }
        for p, d in results
        if getattr(p, "is_active", True)
    ]


@router.get("/ports/search")
async def ports_search(
    q: str | None = None,
    country: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Search active ports by name or locode prefix (case-insensitive)."""
    from app.models.port import Port

    stmt = (
        select(Port)
        .where(Port.latitude.is_not(None))
        .where(Port.is_active.is_(True))
    )
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func
        stmt = stmt.where(
            (func.lower(Port.name).like(like)) | (func.lower(Port.locode).like(like))
        )
    if country:
        stmt = stmt.where(Port.country == country.upper())
    stmt = stmt.order_by(Port.country, Port.locode).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "id": p.id, "locode": p.locode, "name": p.name,
            "country": p.country,
            "latitude": p.latitude, "longitude": p.longitude,
        } for p in rows
    ]


@router.get("/ports/bbox")
async def ports_bbox(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    limit: int = 2000,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return active ports inside a bounding box as GeoJSON FeatureCollection.

    Used by the map UI to render clickable port markers within the current
    viewport.
    """
    from app.models.port import Port

    stmt = (
        select(Port)
        .where(Port.latitude.is_not(None))
        .where(Port.longitude.is_not(None))
        .where(Port.is_active.is_(True))
        .where(Port.latitude.between(min_lat, max_lat))
        .where(Port.longitude.between(min_lon, max_lon))
        .limit(limit)
    )
    ports = list((await db.execute(stmt)).scalars().all())
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p.longitude, p.latitude]},
                "properties": {
                    "id": p.id, "locode": p.locode, "name": p.name, "country": p.country,
                },
            }
            for p in ports
        ],
    }


@router.get("/spec")
async def spec_link() -> dict[str, str]:
    return {"openapi": f"{settings.site_url}/openapi.json", "docs": f"{settings.site_url}/docs"}


@router.get("/legs/{leg_id}", response_model=LegPublic)
async def get_leg_public(leg_id: int, db: AsyncSession = Depends(get_db)) -> LegPublic:
    stmt = (
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.id == leg_id)
        .where(Leg.is_bookable.is_(True))
    )
    row = (await db.execute(stmt)).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not found")
    leg, vessel = row
    pol = await db.get(Port, leg.departure_port_id)
    pod = await db.get(Port, leg.arrival_port_id)
    try:
        cap = await get_available_capacity(db, leg.id)
        available = cap.available_palettes
    except NotBookable:
        available = 0
    return LegPublic(
        leg_id=leg.id,
        leg_code=leg.leg_code,
        vessel_name=vessel.name,
        departure_locode=pol.locode if pol else "",
        departure_name=pol.name if pol else "",
        arrival_locode=pod.locode if pod else "",
        arrival_name=pod.name if pod else "",
        etd=leg.etd,
        eta=leg.eta,
        public_capacity_palettes=leg.public_capacity_palettes,
        available_palettes=available,
        public_price_per_palette_eur=leg.public_price_per_palette_eur,
        booking_close_at=leg.booking_close_at,
    )


@router.get("/legs/{leg_id}/capacity", response_model=CapacityOut)
async def get_capacity(leg_id: int, db: AsyncSession = Depends(get_db)) -> CapacityOut:
    try:
        info = await get_available_capacity(db, leg_id)
    except NotBookable as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Leg not bookable"
        ) from e
    return CapacityOut(
        leg_id=info.leg_id,
        capacity_palettes=info.capacity_palettes,
        reserved_palettes=info.reserved_palettes,
        available_palettes=info.available_palettes,
        occupancy_pct=info.occupancy_pct,
    )


@router.get("/routes")
async def list_routes(
    from_country: str | None = None,
    to_country: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[LegPublic]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.is_bookable.is_(True))
        .where(Leg.etd > now)
        .order_by(Leg.etd.asc())
        .limit(200)
    )
    rows = (await db.execute(stmt)).all()
    out: list[LegPublic] = []
    for leg, vessel in rows:
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        if from_country and pol and pol.country.upper() != from_country.upper():
            continue
        if to_country and pod and pod.country.upper() != to_country.upper():
            continue
        try:
            cap = await get_available_capacity(db, leg.id)
            available = cap.available_palettes
        except NotBookable:
            available = 0
        out.append(
            LegPublic(
                leg_id=leg.id,
                leg_code=leg.leg_code,
                vessel_name=vessel.name,
                departure_locode=pol.locode if pol else "",
                departure_name=pol.name if pol else "",
                arrival_locode=pod.locode if pod else "",
                arrival_name=pod.name if pod else "",
                etd=leg.etd,
                eta=leg.eta,
                public_capacity_palettes=leg.public_capacity_palettes,
                available_palettes=available,
                public_price_per_palette_eur=leg.public_price_per_palette_eur,
                booking_close_at=leg.booking_close_at,
            )
        )
    return out
