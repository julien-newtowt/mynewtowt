"""Marad — endpoints machine de synchronisation (LECTURE SEULE).

``POST /api/marad/refresh`` — header ``X-API-Token: <MARAD_SYNC_TOKEN>`` :
cron Power Automate (périodique, ≥ 30 min vu le rate limit 1 req/min de Marad)
qui déclenche la lecture des données crew Marad. Read-only : ne modifie jamais
Marad. cf. docs/integrations/marad-crew-readonly.md.

``POST /api/marad/flgo-refresh`` — header ``X-API-Token: <MARAD_FLGO_TOKEN>``
(token dédié, distinct de ``MARAD_SYNC_TOKEN``) : cron FLGO (MRV LOT 7,
lecture seule, cf. ``app/services/flgo_sync.py``).

Retourne 503 si le token dédié de l'endpoint appelé n'est pas configuré.
"""

from __future__ import annotations

import logging
import secrets as _secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import flgo_sync, marad_sync
from app.services.activity import record as activity_record

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


# ══════════════════════════════ LOT 7 — FLGO (Marad, lecture seule) ══════════


def _expected_flgo_token() -> str | None:
    return (settings.marad_flgo_token or "").strip() or None


@api_router.post("/flgo-refresh")
async def marad_flgo_refresh_api(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Cron externe FLGO (Power Automate). Auth par X-API-Token dédié. Lecture seule.

    Patron du refresh crew ci-dessus : comparaison à temps constant, 503 si
    ``MARAD_FLGO_TOKEN`` n'est pas configuré. Une panne du client API Marad
    (exception non gérée par ``app.services.flgo_sync`` — ex. simulée en
    test par un mock qui lève) est traduite en 502, jamais en 500 nu :
    l'endpoint ne doit jamais planter la boucle du cron externe.
    """
    expected = _expected_flgo_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MARAD_FLGO_TOKEN non configuré dans .env",
        )
    received = request.headers.get("x-api-token") or ""
    if not _secrets.compare_digest(received.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Token invalide ou absent"
        )

    try:
        result = await flgo_sync.sync_flgo_from_api(db)
    except Exception as exc:
        logger.exception("Marad FLGO refresh: échec de synchronisation")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Synchronisation FLGO Marad indisponible (panne API amont)",
        ) from exc

    await activity_record(
        db,
        action="sync",
        module="mrv",
        entity_type="flgo_reading",
        entity_label="cron flgo-refresh",
        detail=(
            f"imported={result.get('imported')} updated={result.get('updated')} "
            f"skipped={result.get('skipped')} errors={result.get('errors')}"
        ),
    )
    logger.info("Marad FLGO refresh (API): %s", result)
    return JSONResponse(result)
