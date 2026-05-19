"""Tracking — ingestion satcom CSV (Power Automate compatible).

Endpoint :
    POST /api/tracking/upload   header X-API-Token: <TRACKING_API_TOKEN>

Body accepté (négociation automatique selon le Content-Type) :
  - **text/csv** ou body brut : CSV directement dans le body
  - **multipart/form-data** : un champ `file` (nom courant utilisé par Power
    Automate / OneDrive) qui contient le CSV en pièce jointe
  - **application/x-www-form-urlencoded** avec un champ `csv` ou `data`

Colonnes CSV attendues (tolérantes aux variantes) :
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
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


async def _extract_csv_body(request: Request) -> str:
    """Extrait le CSV du body, quel que soit son enveloppage.

    Power Automate (action HTTP avec "Create file" en upstream) envoie en
    multipart/form-data avec un champ `file`. D'autres flux peuvent
    envoyer du CSV brut. On gère les deux + le cas form-urlencoded.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    # 1. multipart/form-data — Power Automate "Upload file" / upstream OneDrive
    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"multipart parsing failed: {e}",
            )
        # Cherche un champ fichier, puis n'importe quel champ avec du contenu
        for key in ("file", "files", "csv", "data", "body", "upload", "attachment"):
            if key in form:
                value = form[key]
                if hasattr(value, "read"):  # UploadFile
                    raw = await value.read()
                    return raw.decode("utf-8", errors="replace")
                return str(value)
        # Fallback : prendre le premier champ qui ressemble à un fichier
        for k, v in form.items():
            if hasattr(v, "read"):
                raw = await v.read()
                return raw.decode("utf-8", errors="replace")
        # Sinon prendre la 1re valeur textuelle non vide
        for k, v in form.items():
            if isinstance(v, str) and v.strip():
                return v
        raise HTTPException(
            status_code=400,
            detail="multipart body without a CSV field (expected 'file' or 'csv')",
        )

    # 2. application/x-www-form-urlencoded — champs csv/data
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key in ("csv", "data", "body", "file"):
            if key in form:
                return str(form[key])
        # Fallback : concaténer tous les champs sous forme CSV-like
        raise HTTPException(
            status_code=400,
            detail="form body without a 'csv' or 'data' field",
        )

    # 3. text/csv ou raw body — comportement historique
    raw = await request.body()
    return raw.decode("utf-8", errors="replace")


@router.post("/upload")
async def upload_positions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Auth — X-API-Token
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TRACKING_API_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if received != expected:
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    # Body — accepte CSV brut OU multipart/form-data OU urlencoded
    raw = await _extract_csv_body(request)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="body vide")

    # Debug log temporaire — montre les 800 premiers chars du body reçu et les
    # en-têtes utiles. Permet de diagnostiquer ce que Power Automate envoie.
    import logging as _logging
    _log = _logging.getLogger("tracking")
    _log.warning(
        "Received tracking upload: content-type=%r, body_len=%d, body[:800]=%r",
        request.headers.get("content-type"),
        len(raw),
        raw[:800],
    )

    # Détection du début utile (saute les éventuelles lignes vides ou de boundary
    # multipart résiduelles si le parser FastAPI n'a pas tout nettoyé).
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    cleaned = "\n".join(lines)

    delim = _detect_delimiter(cleaned)
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delim)

    inserted = 0
    skipped = 0
    errors: list[str] = []

    vessels = {v.code: v for v in (await db.execute(select(Vessel))).scalars().all()}

    for idx, row in enumerate(reader, start=1):
        if not row:
            continue
        code = (
            row.get("vessel_code") or row.get("vessel") or row.get("code")
            or row.get("Code") or row.get("Vessel") or ""
        ).strip()
        v = vessels.get(code)
        if not v:
            skipped += 1
            errors.append(f"row {idx}: vessel_code '{code}' unknown")
            continue
        dt = _parse_dt(
            row.get("date") or row.get("datetime") or row.get("recorded_at")
            or row.get("Date") or row.get("timestamp") or ""
        )
        lat = _parse_float(row.get("lat") or row.get("latitude") or row.get("Lat"))
        lon = _parse_float(
            row.get("lon") or row.get("longitude") or row.get("lng")
            or row.get("Long") or row.get("Lon")
        )
        if dt is None or lat is None or lon is None:
            skipped += 1
            errors.append(f"row {idx}: missing date/lat/lon (date={row.get('date')}, lat={row.get('lat')}, lon={row.get('lon')})")
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
            sog_kn=_parse_float(row.get("sog") or row.get("speed") or row.get("SOG")),
            cog_deg=_parse_float(row.get("cog") or row.get("heading") or row.get("COG")),
            source=(row.get("source") or "satcom")[:40],
        ))
        inserted += 1

    await db.flush()
    return JSONResponse({
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors[:10],  # ne renvoie pas plus de 10 erreurs pour rester compact
    })


def _detect_delimiter(text: str) -> str:
    head = text[:2048]
    counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get) or ","
