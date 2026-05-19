"""Tracking — ingestion satcom CSV (Power Automate compatible).

Endpoint :
    POST /api/tracking/upload   header X-API-Token: <TRACKING_API_TOKEN>

Body : CSV — colonnes attendues (tolérant aux variantes) :
    vessel_code, date, lat, lon, sog, cog [, source]

Si TRACKING_API_TOKEN n'est pas défini en .env, retour 503.
Public (pas d'auth utilisateur) — protégé par X-API-Token uniquement.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.claim import VesselPosition
from app.models.vessel import Vessel

router = APIRouter(prefix="/api/tracking", tags=["tracking-api"])


def _expected_token() -> str | None:
    return (os.getenv("TRACKING_API_TOKEN") or "").strip() or None


def _parse_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_dt(v: str) -> datetime | None:
    if not v:
        return None
    v = v.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        # Try common alt format dd/mm/YYYY HH:MM
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


@router.post("/upload")
async def upload_positions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TRACKING_API_TOKEN non configuré",
        )
    received = request.headers.get("x-api-token") or ""
    if received != expected:
        raise HTTPException(status_code=403, detail="X-API-Token invalide")

    raw = (await request.body()).decode("utf-8", errors="replace")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="corps vide")

    reader = csv.DictReader(io.StringIO(raw), delimiter=_detect_delimiter(raw))
    inserted = 0
    skipped = 0

    vessels = {v.code: v for v in (await db.execute(select(Vessel))).scalars().all()}

    for row in reader:
        if not row:
            continue
        code = (row.get("vessel_code") or row.get("vessel") or row.get("code") or "").strip()
        v = vessels.get(code)
        if not v:
            skipped += 1
            continue
        dt = _parse_dt(row.get("date") or row.get("datetime") or row.get("recorded_at") or "")
        lat = _parse_float(row.get("lat") or row.get("latitude"))
        lon = _parse_float(row.get("lon") or row.get("longitude") or row.get("lng"))
        if dt is None or lat is None or lon is None:
            skipped += 1
            continue
        # Idempotent : skip duplicates (same vessel + same recorded_at)
        existing = (await db.execute(
            select(VesselPosition).where(VesselPosition.vessel_id == v.id)
            .where(VesselPosition.recorded_at == dt)
        )).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        db.add(VesselPosition(
            vessel_id=v.id,
            recorded_at=dt,
            latitude=lat, longitude=lon,
            sog_kn=_parse_float(row.get("sog") or row.get("speed")),
            cog_deg=_parse_float(row.get("cog") or row.get("heading")),
            source=(row.get("source") or "satcom")[:40],
        ))
        inserted += 1

    await db.flush()
    return JSONResponse({"inserted": inserted, "skipped": skipped})


def _detect_delimiter(text: str) -> str:
    head = text[:2048]
    counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get) or ","
