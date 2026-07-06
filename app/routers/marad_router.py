"""Marad — endpoint machine de synchronisation crew (LECTURE SEULE).

``POST /api/marad/refresh`` — header ``X-API-Token: <MARAD_SYNC_TOKEN>`` :
cron Power Automate (périodique, ≥ 30 min vu le rate limit 1 req/min de Marad)
qui déclenche la lecture des données crew Marad. Read-only : ne modifie jamais
Marad. cf. docs/integrations/marad-crew-readonly.md.

Retourne 503 si ``MARAD_SYNC_TOKEN`` n'est pas configuré.
"""

from __future__ import annotations

import logging
import secrets as _secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import marad_sync

logger = logging.getLogger("marad")

api_router = APIRouter(prefix="/api/marad", tags=["marad-api"])


def _expected_token() -> str | None:
    return (settings.marad_sync_token or "").strip() or None


@api_router.post("/refresh")
async def marad_refresh_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Cron externe (Power Automate). Auth par X-API-Token. Lecture seule."""
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MARAD_SYNC_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    # Cron automatisé : on peut se permettre d'attendre pour contourner le
    # throttling 1 req/min de /api/CrewingSchedule (retry une fois après pause).
    result = await marad_sync.sync_all(
        db, schedule_retry_wait=settings.marad_schedule_retry_wait
    )
    logger.info("Marad refresh (API): %s", result)
    return JSONResponse(result)
