"""Webhook Yousign — transitions de signature de la booking note.

Monté sous ``/webhooks/`` pour hériter de l'exemption CSRF déjà déclarée : il n'y
a pas d'utilisateur derrière cet appel, donc pas de jeton de session à croiser.
La confiance vient **exclusivement** de l'empreinte HMAC du corps brut.

Le payload n'est jamais cru sur l'état métier : il sert à identifier la demande,
puis on relit son état **chez Yousign** avant toute transition. Un webhook forgé
ne peut donc pas faire passer une booking note pour signée — au pire il provoque
une relecture inutile.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.booking_note import BookingNote
from app.services import yousign as yousign_svc
from app.services.booking_note_signature import apply_signature_state

logger = logging.getLogger("yousign")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/yousign")
async def yousign_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Réception des événements Yousign — authentifiés par signature HMAC."""
    if not yousign_svc.webhook_configured():
        return JSONResponse({"error": "not_configured"}, status_code=503)

    payload = await request.body()
    signature = request.headers.get("x-yousign-signature") or request.headers.get(
        "X-Yousign-Signature"
    )
    try:
        valid = yousign_svc.verify_webhook_signature(payload, signature)
    except yousign_svc.YousignNotConfigured:
        return JSONResponse({"error": "not_configured"}, status_code=503)
    if not valid:
        logger.warning("Webhook Yousign rejeté : signature invalide")
        return JSONResponse({"error": "invalid_signature"}, status_code=400)

    try:
        event = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    event_type = (event.get("event_name") or event.get("type") or "").strip()
    data = event.get("data") or {}
    signature_request = data.get("signature_request") or data
    request_id = signature_request.get("id") if isinstance(signature_request, dict) else None

    if not request_id or event_type not in yousign_svc.SIGNATURE_EVENTS:
        # Événement inconnu ou hors périmètre : accusé de réception sans
        # traitement. Répondre 500 ferait désactiver l'endpoint après retries.
        return JSONResponse({"received": True})

    note = (
        await db.execute(
            select(BookingNote).where(BookingNote.signature_request_id == str(request_id))
        )
    ).scalar_one_or_none()
    if note is None:
        logger.info("Webhook Yousign : demande %s inconnue", request_id)
        return JSONResponse({"received": True})

    await apply_signature_state(db, note, event_type=event_type)
    return JSONResponse({"received": True})
