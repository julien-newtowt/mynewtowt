"""Tracking — ingestion satcom (CSV brut / multipart / ZIP+XLSX).

Endpoint :
    POST /api/tracking/upload   header X-API-Token: <TRACKING_API_TOKEN>

Formats acceptés (négociation automatique) :
  - **text/csv** ou body brut : CSV directement
  - **multipart/form-data** : champ `file` contenant soit un CSV soit un
    ZIP (le ZIP est dézippé en mémoire, le 1er .xlsx ou .csv trouvé est
    parsé). Cas typique Power Automate qui forwarde un rapport satcom
    quotidien sous forme de ZIP contenant un Excel.
  - **application/x-www-form-urlencoded** : champ `csv` ou `data`

Colonnes attendues (tolérantes) — variantes acceptées :
    vessel_code | vessel | code | Vessel | Code
    date | datetime | recorded_at | timestamp | Date | DateTime
    lat | latitude | Lat
    lon | longitude | lng | Long | Lon
    sog | speed | SOG
    cog | heading | COG
    source

Si la colonne `vessel_code` est absente, l'endpoint **extrait l'identifiant
du nom du fichier** (ex. `DailyReport-19914-...` → vessel_code = `19914`,
mappé par .env `TRACKING_VESSEL_MAP="19914=1,19915=2,..."` ou direct si
le code existe en base).

Si TRACKING_API_TOKEN n'est pas défini en .env, retour 503.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.claim import VesselPosition
from app.models.vessel import Vessel

router = APIRouter(prefix="/api/tracking", tags=["tracking-api"])
logger = logging.getLogger("tracking")


def _expected_token() -> str | None:
    return (os.getenv("TRACKING_API_TOKEN") or "").strip() or None


def _vessel_map() -> dict[str, str]:
    """Lit TRACKING_VESSEL_MAP="<id_externe>=<code_db>,<id>=<code>,..." du .env.

    Permet de mapper l'identifiant satcom (ex. MMSI 19914) vers le code
    interne du navire (ex. "1" pour Anemos). Sans ce mapping on essaie
    aussi l'identifiant brut comme code direct.
    """
    raw = (os.getenv("TRACKING_VESSEL_MAP") or "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_float(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_dt(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
            "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
            "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
            "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


# ────────────────────── Body extraction (multi-format) ───────────────────


async def _extract_payload(request: Request) -> tuple[bytes, str]:
    """Renvoie (bytes_body, filename) — bytes utiles pour la détection ZIP/CSV/XLSX."""
    content_type = (request.headers.get("content-type") or "").lower()

    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"multipart parsing failed: {e}")
        # Cherche un champ fichier
        for key in ("file", "files", "csv", "data", "body", "upload", "attachment"):
            if key in form:
                value = form[key]
                if hasattr(value, "read"):  # UploadFile
                    return await value.read(), getattr(value, "filename", "") or ""
                return str(value).encode("utf-8"), ""
        # Fallback : 1er field qui est un UploadFile
        for _, v in form.items():
            if hasattr(v, "read"):
                return await v.read(), getattr(v, "filename", "") or ""
        for _, v in form.items():
            if isinstance(v, str) and v.strip():
                return v.encode("utf-8"), ""
        raise HTTPException(status_code=400, detail="multipart without a file field")

    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key in ("csv", "data", "body", "file"):
            if key in form:
                return str(form[key]).encode("utf-8"), ""
        raise HTTPException(status_code=400, detail="form body without 'csv' or 'data'")

    body = await request.body()
    return body, ""


# ────────────────────── Parsers (CSV / XLSX / ZIP) ─────────────────────


def _rows_from_csv(text: str) -> Iterable[dict]:
    """Parse a CSV string into a list of normalized dict rows."""
    # Skip empty lines + leftover multipart boundaries
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    cleaned = "\n".join(lines)
    delim = _detect_delimiter(cleaned)
    return list(csv.DictReader(io.StringIO(cleaned), delimiter=delim))


def _rows_from_xlsx(content: bytes) -> Iterable[dict]:
    """Parse an XLSX file (first sheet) — headers on first row."""
    try:
        import openpyxl
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"openpyxl not installed: {e}")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"xlsx parsing failed: {e}")
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [str(c).strip() if c is not None else "" for c in next(rows_iter)]
    except StopIteration:
        return []
    out: list[dict] = []
    for row in rows_iter:
        if not row or all(c is None or (isinstance(c, str) and not c.strip()) for c in row):
            continue
        d = {header[i]: row[i] for i in range(min(len(header), len(row))) if header[i]}
        out.append(d)
    return out


def _rows_from_zip(content: bytes) -> tuple[Iterable[dict], str]:
    """Open the ZIP and parse the first .xlsx (preferred) or .csv inside.

    Returns (rows, inner_filename) — inner_filename utile pour extraire un
    vessel_code à partir du nom de fichier interne.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail=f"invalid ZIP: {e}")
    # Préférer XLSX → CSV → HTML
    candidates = sorted(zf.namelist())
    xlsx = next((n for n in candidates if n.lower().endswith(".xlsx")), None)
    csv_name = next((n for n in candidates if n.lower().endswith(".csv")), None)
    if xlsx:
        with zf.open(xlsx) as f:
            return _rows_from_xlsx(f.read()), xlsx
    if csv_name:
        with zf.open(csv_name) as f:
            return _rows_from_csv(f.read().decode("utf-8", errors="replace")), csv_name
    raise HTTPException(
        status_code=400,
        detail=f"ZIP without xlsx/csv (found: {candidates})",
    )


def _detect_delimiter(text: str) -> str:
    head = text[:2048]
    counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get) or ","


# ────────────────────── Vessel code resolution ─────────────────────────


_FILENAME_VESSEL_RE = re.compile(r"(\d{4,})")


def _resolve_vessel(
    row: dict, filename: str, *, vessels: dict[str, Vessel], vmap: dict[str, str],
) -> Vessel | None:
    """Tente plusieurs stratégies pour identifier le navire.

    1. Colonne explicite (vessel_code, Vessel, Code, MMSI, IMO)
    2. Extraction du 1er nombre 4+ chars du nom de fichier (ex. DailyReport-19914-...)
    3. Mapping TRACKING_VESSEL_MAP (.env) sur l'identifiant brut
    """
    candidates = [
        row.get("vessel_code"), row.get("vessel"), row.get("code"),
        row.get("Vessel"), row.get("Code"), row.get("VESSEL"),
        row.get("MMSI"), row.get("IMO"), row.get("imo_number"),
        row.get("vessel_name"), row.get("Name"),
    ]
    for c in candidates:
        if c is None or str(c).strip() == "":
            continue
        s = str(c).strip()
        # Direct hit on code
        if s in vessels:
            return vessels[s]
        # Mapping override
        mapped = vmap.get(s)
        if mapped and mapped in vessels:
            return vessels[mapped]
        # By IMO
        for v in vessels.values():
            if v.imo_number and str(v.imo_number) == s:
                return v
        # By name (case-insensitive)
        for v in vessels.values():
            if v.name and v.name.lower() == s.lower():
                return v

    # Fallback : extraire identifiant du nom de fichier
    if filename:
        m = _FILENAME_VESSEL_RE.search(filename)
        if m:
            ext_id = m.group(1)
            mapped = vmap.get(ext_id)
            if mapped and mapped in vessels:
                return vessels[mapped]
            if ext_id in vessels:
                return vessels[ext_id]
            # Essayer comme IMO
            for v in vessels.values():
                if v.imo_number and str(v.imo_number) == ext_id:
                    return v
    return None


# ────────────────────── Endpoint ───────────────────────────────────────


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

    # Extraction du body (renvoie bytes + filename éventuel)
    payload, filename = await _extract_payload(request)
    if not payload:
        raise HTTPException(status_code=400, detail="body vide")

    # Détection du format
    rows: Iterable[dict]
    inner_name = filename
    if payload[:4] == b"PK\x03\x04":  # ZIP magic
        rows, inner_name = _rows_from_zip(payload)
        logger.warning("Tracking upload: ZIP file '%s' → inner '%s'", filename, inner_name)
    else:
        # Essai XLSX (signature alternative PK\x05/\x07)
        if payload[:2] == b"PK" or filename.lower().endswith(".xlsx"):
            rows = _rows_from_xlsx(payload)
            logger.warning("Tracking upload: XLSX file '%s'", filename)
        else:
            # CSV / texte
            text = payload.decode("utf-8", errors="replace")
            rows = _rows_from_csv(text)
            logger.warning("Tracking upload: CSV text len=%d, file='%s'", len(text), filename)

    rows = list(rows)
    if not rows:
        return JSONResponse({"inserted": 0, "skipped": 0, "errors": ["no rows extracted"]})

    inserted = 0
    skipped = 0
    errors: list[str] = []

    vessels_by_code = {
        v.code: v for v in (await db.execute(select(Vessel))).scalars().all()
    }
    vmap = _vessel_map()

    for idx, row in enumerate(rows, start=1):
        if not row:
            continue

        v = _resolve_vessel(row, inner_name, vessels=vessels_by_code, vmap=vmap)
        if not v:
            skipped += 1
            if len(errors) < 10:
                errors.append(
                    f"row {idx}: vessel unknown — tried code/imo/name + filename '{inner_name}'. "
                    f"Set TRACKING_VESSEL_MAP in .env to map external ids."
                )
            continue

        # Date — accepte une multitude de noms et formats
        date_val = (
            row.get("date") or row.get("Date") or row.get("DateTime")
            or row.get("datetime") or row.get("Datetime") or row.get("Timestamp")
            or row.get("timestamp") or row.get("recorded_at") or row.get("Recorded_At")
            or row.get("Time UTC") or row.get("UTC") or row.get("ReportTime")
        )
        dt = _parse_dt(date_val)

        lat = _parse_float(
            row.get("lat") or row.get("Lat") or row.get("Latitude") or row.get("latitude")
            or row.get("LAT")
        )
        lon = _parse_float(
            row.get("lon") or row.get("Lon") or row.get("Longitude") or row.get("longitude")
            or row.get("lng") or row.get("Long") or row.get("LON") or row.get("LONG")
        )

        if dt is None or lat is None or lon is None:
            skipped += 1
            if len(errors) < 10:
                errors.append(
                    f"row {idx}: missing/unparseable date/lat/lon "
                    f"(date={date_val!r}, lat={row.get('lat')!r}, lon={row.get('lon')!r}, keys={list(row.keys())[:6]})"
                )
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
            latitude=lat,
            longitude=lon,
            sog_kn=_parse_float(
                row.get("sog") or row.get("SOG") or row.get("speed") or row.get("Speed")
            ),
            cog_deg=_parse_float(
                row.get("cog") or row.get("COG") or row.get("heading") or row.get("Heading")
                or row.get("course")
            ),
            source=(row.get("source") or "satcom")[:40],
        ))
        inserted += 1

    await db.flush()
    return JSONResponse({
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors[:10],
        "rows_detected": len(rows),
        "file": inner_name,
    })
