"""Public REST API v1 — for B2B integrations.

Authentication is API-key based (header `X-API-Key`). For V3.0 we expose
read-only routes; write endpoints will land in V3.1 with HMAC-signed
webhooks back to the client.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.auth import get_optional_staff
from app.config import settings
from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.schemas.booking import CapacityOut
from app.schemas.leg import LegPublic
from app.services.capacity import NotBookable, get_available_capacity

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """SEC-06 — auth des routes B2B de l'API v1 (header ``X-API-Key``).

    Secure-by-default : sans ``public_api_key`` configurée, l'API renvoie 503
    (cohérent avec les autres endpoints machine). Clé absente/invalide → 401.
    Comparaison en temps constant (``hmac.compare_digest``).
    """
    configured = settings.public_api_key
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Public API not configured",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


async def require_api_key_or_staff(
    x_api_key: str | None = Header(default=None),
    staff=Depends(get_optional_staff),
) -> None:
    """Ports en lecture : consommés par l'UI planning **interne** (JS) ET par
    les intégrations B2B. Une session staff authentifiée (cookie) suffit ;
    sinon on retombe sur la clé API B2B (SEC-06). On ne réexpose donc pas
    publiquement ces endpoints, mais le planning interne charge la liste des
    ports sans clé (régression SEC-06 : le JS staff n'envoyait aucune clé →
    503, cascade Zone/Pays/Port vide).
    """
    if staff is not None:
        return
    await require_api_key(x_api_key=x_api_key)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "env": settings.app_env}


@router.get("/ports/nearby", dependencies=[Depends(require_api_key_or_staff)])
async def ports_nearby(
    lat: float,
    lon: float,
    radius_km: float = 50,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return active ports near a (lat, lon) within radius_km, sorted by distance."""
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


@router.get("/ports/search", dependencies=[Depends(require_api_key_or_staff)])
async def ports_search(
    q: str | None = None,
    country: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Search active ports by name or locode prefix (case-insensitive)."""
    from app.models.port import Port

    stmt = select(Port).where(Port.latitude.is_not(None)).where(Port.is_active.is_(True))
    if q:
        like = f"%{q.lower()}%"
        from sqlalchemy import func

        stmt = stmt.where((func.lower(Port.name).like(like)) | (func.lower(Port.locode).like(like)))
    if country:
        stmt = stmt.where(Port.country == country.upper())
    stmt = stmt.order_by(Port.country, Port.locode).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        {
            "id": p.id,
            "locode": p.locode,
            "name": p.name,
            "country": p.country,
            "latitude": p.latitude,
            "longitude": p.longitude,
        }
        for p in rows
    ]


@router.get("/ports/bbox", dependencies=[Depends(require_api_key_or_staff)])
async def ports_bbox(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
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
                    "id": p.id,
                    "locode": p.locode,
                    "name": p.name,
                    "country": p.country,
                },
            }
            for p in ports
        ],
    }


@router.get("/ports/next-clocks", dependencies=[Depends(require_api_key)])
async def ports_next_clocks(
    limit: int = 3,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Next arrival ports of the fleet — used by the sidebar clock widget.

    Returns, for each vessel currently at sea or about to sail, the next
    arrival port with its IANA timezone (when known). Falls back to the
    immediate upcoming arrivals if no vessel is currently in transit.
    """
    now = datetime.now(UTC)
    # Upcoming arrivals: legs whose ETA is in the future, ordered ASAP first.
    stmt = (
        select(Leg, Port, Vessel)
        .join(Port, Port.id == Leg.arrival_port_id)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.eta > now)
        .where(Leg.status.in_(("planned", "in_progress")))
        .order_by(Leg.eta.asc())
        .limit(max(1, min(limit, 5)))
    )
    rows = list((await db.execute(stmt)).all())
    out: list[dict] = []
    seen: set[str] = set()
    for leg, port, vessel in rows:
        if not port or not port.timezone:
            continue
        if port.locode in seen:
            continue
        seen.add(port.locode)
        out.append(
            {
                "locode": port.locode,
                "port_name": port.name,
                "country": port.country,
                "timezone": port.timezone,
                "label": port.locode,
                "vessel_code": vessel.code,
                "eta": leg.eta.isoformat(),
            }
        )
    return out


@router.get("/spec")
async def spec_link() -> dict[str, str]:
    return {"openapi": f"{settings.site_url}/openapi.json", "docs": f"{settings.site_url}/docs"}


@router.get("/legs/{leg_id}", response_model=LegPublic, dependencies=[Depends(require_api_key)])
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


@router.get(
    "/legs/{leg_id}/capacity", response_model=CapacityOut, dependencies=[Depends(require_api_key)]
)
async def get_capacity(leg_id: int, db: AsyncSession = Depends(get_db)) -> CapacityOut:
    try:
        info = await get_available_capacity(db, leg_id)
    except NotBookable as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not bookable") from e
    return CapacityOut(
        leg_id=info.leg_id,
        capacity_palettes=info.capacity_palettes,
        reserved_palettes=info.reserved_palettes,
        available_palettes=info.available_palettes,
        occupancy_pct=info.occupancy_pct,
    )


@router.get("/routes", dependencies=[Depends(require_api_key)])
async def list_routes(
    from_country: str | None = None,
    to_country: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[LegPublic]:
    now = datetime.now(UTC)
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
