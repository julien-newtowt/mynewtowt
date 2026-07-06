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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
    only: str | None = Query(
        default=None,
        description="'crew' ou 'schedules' pour ne synchroniser qu'une partie "
        "(appels courts, à espacer côté cron). Omis = les deux.",
    ),
) -> JSONResponse:
    """Cron externe (Power Automate). Auth par X-API-Token. Lecture seule.

    ``/api/Crewing`` et ``/api/CrewingSchedule`` sont à 1 req/min avec une
    fenêtre partagée : les enchaîner dans un seul appel force un 429 sur le
    second, et l'attendre dépasse le timeout du reverse-proxy (Caddy coupe à
    ~60 s → 504). Le paramètre ``only`` permet donc de **découpler** : le cron
    appelle ``?only=crew`` puis, après un délai > 60 s, ``?only=schedules`` —
    deux requêtes courtes, aucune attente longue. Sans ``only``, on tente les
    deux (best-effort ; les plannings peuvent prendre un 429, alors remonté).
    """
    expected = _expected_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MARAD_SYNC_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-API-Token invalide ou absent")

    part = (only or "").strip().lower()
    if part == "crew":
        result = {"part": "crew", **await marad_sync.sync_crew(db)}
    elif part in ("schedules", "schedule", "plannings", "planning"):
        result = {"part": "schedules", **await marad_sync.sync_schedules(db)}
    else:
        # Les deux en un appel (best-effort). schedule_retry_wait n'est utile que
        # si le reverse-proxy autorise des réponses > 60 s ; sinon préférer le
        # découplage ?only= ci-dessus (cf. runbook).
        result = await marad_sync.sync_all(
            db, schedule_retry_wait=settings.marad_schedule_retry_wait
        )
    logger.info("Marad refresh (API, only=%s): %s", part or "all", result)
    return JSONResponse(result)
