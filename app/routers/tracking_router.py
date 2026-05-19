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
    # Unix timestamp (10 digits = seconds, 13 digits = ms)
    if s.isdigit():
        try:
            ts = int(s)
            if ts > 10**12:  # ms
                ts = ts // 1000
            if 10**9 <= ts <= 4 * 10**9:  # plausible epoch ~ 2001-2096
                return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OverflowError):
            pass
    s_iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
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


def _get_col(row: dict, *candidates: str) -> object:
    """Lookup d'une colonne avec tolérance aux variations courantes :
    - exact match
    - case-insensitive
    - en enlevant les suffixes parenthétiques (ex. "SOG (knots)" → "SOG")
    - en remplaçant espaces par underscores et vice versa
    """
    if not row:
        return None
    # 1. exact
    for c in candidates:
        if c in row and row[c] not in (None, ""):
            return row[c]
    # 2. normalize : lower-case + strip parens + strip non-alnum
    def _norm(s: str) -> str:
        s = re.sub(r"\([^)]*\)", "", str(s))  # drop "(knots)" etc.
        s = re.sub(r"[^a-z0-9]+", "", s.lower())
        return s
    norm_row = {_norm(k): v for k, v in row.items() if k}
    for c in candidates:
        nc = _norm(c)
        if nc in norm_row and norm_row[nc] not in (None, ""):
            return norm_row[nc]
    return None


# ────────────────────── Body extraction (multi-format) ───────────────────

# Hard cap : un rapport satcom quotidien fait < 5 MB. 20 MB couvre largement
# les ZIP avec plusieurs sheets ou plusieurs jours. Au-delà, on rejette.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _enforce_size(data: bytes, label: str = "body") -> bytes:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{label} too large ({len(data)} bytes > {MAX_UPLOAD_BYTES} max)",
        )
    return data


async def _extract_payload(request: Request) -> tuple[bytes, str]:
    """Renvoie (bytes_body, filename) — bytes utiles pour la détection ZIP/CSV/XLSX.

    Refuse tout payload > MAX_UPLOAD_BYTES (20 MB) pour éviter OOM.
    """
    # Préfilrage via Content-Length si présent (avant de lire le body)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Content-Length {cl} exceeds max {MAX_UPLOAD_BYTES}",
        )

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
                    return _enforce_size(await value.read(), key), getattr(value, "filename", "") or ""
                return _enforce_size(str(value).encode("utf-8"), key), ""
        # Fallback : 1er field qui est un UploadFile
        for _, v in form.items():
            if hasattr(v, "read"):
                return _enforce_size(await v.read(), "upload"), getattr(v, "filename", "") or ""
        for _, v in form.items():
            if isinstance(v, str) and v.strip():
                return _enforce_size(v.encode("utf-8"), "field"), ""
        raise HTTPException(status_code=400, detail="multipart without a file field")

    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key in ("csv", "data", "body", "file"):
            if key in form:
                return _enforce_size(str(form[key]).encode("utf-8"), key), ""
        raise HTTPException(status_code=400, detail="form body without 'csv' or 'data'")

    body = await request.body()
    return _enforce_size(body, "body"), ""


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

    1. Colonne explicite (vessel_code, Vessel, Code, MMSI, IMO, Name…)
    2. Recherche du nom du navire dans le filename (ex. "anemos" dans
       "98abb4ff-20241021100502anemossatcoms.csv")
    3. Extraction du 1er nombre 4+ chars du filename (ex. "19914") → IMO/mapping
    4. Mapping TRACKING_VESSEL_MAP (.env) sur l'identifiant brut
    """
    # 1. Colonnes explicites
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
        if s in vessels:
            return vessels[s]
        mapped = vmap.get(s)
        if mapped and mapped in vessels:
            return vessels[mapped]
        for v in vessels.values():
            if v.imo_number and str(v.imo_number) == s:
                return v
        for v in vessels.values():
            if v.name and v.name.lower() == s.lower():
                return v

    if not filename:
        return None

    fname_low = filename.lower()

    # 2. Recherche du nom du navire (substring case-insensitive)
    # Ex. "98abb4ff-20241021100502anemossatcoms.csv" → matche "anemos" → Anemos
    for v in vessels.values():
        if v.name and v.name.lower() in fname_low:
            return v

    # 3. Identifiant numérique dans le filename
    m = _FILENAME_VESSEL_RE.search(filename)
    if m:
        ext_id = m.group(1)
        mapped = vmap.get(ext_id)
        if mapped and mapped in vessels:
            return vessels[mapped]
        if ext_id in vessels:
            return vessels[ext_id]
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

    # Filename — 3 sources possibles dans l'ordre :
    # 1. multipart Content-Disposition (extrait par _extract_payload)
    # 2. header X-Filename
    # 3. query string ?filename=... (le plus simple pour Power Automate qui
    #    refuse parfois les expressions dynamiques dans les headers JSON)
    if not filename:
        filename = (
            request.headers.get("x-filename")
            or request.query_params.get("filename")
            or ""
        )

    # Détection du format — robuste aux binaires corrompus par PA
    rows: Iterable[dict] = []
    inner_name = filename
    fmt_tried: list[str] = []

    def _try_csv_fallback() -> list[dict]:
        try:
            text = payload.decode("utf-8", errors="replace")
            return list(_rows_from_csv(text))
        except Exception:
            return []

    if payload[:4] == b"PK\x03\x04":  # signature ZIP/XLSX
        # Try ZIP first
        try:
            rows, inner_name = _rows_from_zip(payload)
            fmt_tried.append("zip")
            logger.warning("Tracking upload: ZIP '%s' → inner '%s'", filename, inner_name)
        except HTTPException as e:
            fmt_tried.append(f"zip-failed({e.detail})")
            # Try XLSX directly
            try:
                rows = list(_rows_from_xlsx(payload))
                fmt_tried.append("xlsx")
            except HTTPException as e2:
                fmt_tried.append(f"xlsx-failed({e2.detail})")
                # Last-resort: CSV fallback (peut-être le binaire est en fait du
                # texte CSV corrompu par PA — laissons le parser tenter)
                rows = _try_csv_fallback()
                fmt_tried.append(f"csv-fallback({len(rows)} rows)")
                inner_name = filename
                # Si vraiment rien ne marche, on renvoie une 400 explicite
                if not rows:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Body looks like ZIP/XLSX but is corrupted (likely "
                            "base64ToString() encoding loss in Power Automate). "
                            f"Tried: {', '.join(fmt_tried)}. Send the CSV as raw "
                            "text/csv body instead of wrapping it in multipart."
                        ),
                    )
                logger.warning(
                    "Tracking upload: ZIP/XLSX failed, CSV fallback got %d rows",
                    len(rows),
                )
    elif filename.lower().endswith(".xlsx"):
        rows = list(_rows_from_xlsx(payload))
        fmt_tried.append("xlsx")
        logger.warning("Tracking upload: XLSX '%s'", filename)
    else:
        # CSV / text
        text = payload.decode("utf-8", errors="replace")
        rows = _rows_from_csv(text)
        fmt_tried.append("csv")
        logger.warning(
            "Tracking upload: CSV text len=%d, file='%s'", len(text), filename,
        )

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

        # Date — préfère ISO 8601 (Date / DateTime), fallback Unix Timestamp
        date_val = _get_col(
            row, "date", "Date", "DateTime", "datetime", "Datetime",
            "recorded_at", "Recorded_At", "Time UTC", "UTC", "ReportTime",
        )
        if not date_val:
            date_val = _get_col(row, "Timestamp", "timestamp")
        dt = _parse_dt(date_val)

        lat = _parse_float(_get_col(row, "lat", "Lat", "Latitude", "latitude", "LAT"))
        lon = _parse_float(_get_col(
            row, "lon", "Lon", "Longitude", "longitude", "lng", "Long", "LON", "LONG"
        ))

        if dt is None or lat is None or lon is None:
            skipped += 1
            if len(errors) < 10:
                errors.append(
                    f"row {idx}: missing/unparseable date/lat/lon "
                    f"(date={date_val!r}, lat={_get_col(row, 'lat', 'Latitude')!r}, "
                    f"lon={_get_col(row, 'lon', 'Longitude')!r}, keys={list(row.keys())[:8]})"
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

        # source : prend "Active interface" (Starlink_xxx) si dispo, sinon "satcom"
        source_val = _get_col(
            row, "source", "Source", "Active interface", "ActiveInterface", "interface",
        ) or "satcom"

        db.add(VesselPosition(
            vessel_id=v.id,
            recorded_at=dt,
            latitude=lat,
            longitude=lon,
            sog_kn=_parse_float(_get_col(
                row, "sog", "SOG", "SOG (knots)", "speed", "Speed", "speed_knots",
            )),
            cog_deg=_parse_float(_get_col(
                row, "cog", "COG", "COG (degree)", "COG (degrees)", "heading", "Heading", "course",
            )),
            source=str(source_val)[:40],
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
