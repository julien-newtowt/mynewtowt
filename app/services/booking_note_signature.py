"""Cycle de signature électronique d'une booking note.

Sépare délibérément deux choses que le module commercial ne doit jamais
confondre :

* la **signature** — l'accord juridique du chargeur sur le contrat ;
* le **règlement** — l'exécution des échéances.

Une booking note signée n'est pas une booking note payée. Les deux états vivent
côte à côte et aucun ne pilote l'autre : lier les deux ferait, à la première
évolution, considérer un contrat signé comme encaissé.

Toute transition passe par une **relecture serveur-à-serveur** de l'état chez
Yousign. Le contenu du webhook sert à savoir *quelle* demande regarder, jamais à
décider de son sort.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking_note import BookingNote
from app.services import yousign as yousign_svc

logger = logging.getLogger("yousign")

# États internes du parcours de signature.
SIGNATURE_STATUSES = ("requested", "signed", "declined", "expired", "cancelled")
SIGNATURE_STATUS_LABELS: dict[str, str] = {
    "requested": "Signature demandée",
    "signed": "Signée",
    "declined": "Refusée par le client",
    "expired": "Demande expirée",
    "cancelled": "Demande annulée",
}

# Correspondance événement Yousign → état interne. ``activated`` ne fait que
# confirmer l'envoi : il n'ajoute rien à « demandée » et n'est donc pas mappé.
_EVENT_TO_STATUS: dict[str, str] = {
    "signature_request.done": "signed",
    "signature_request.declined": "declined",
    "signature_request.expired": "expired",
    "signature_request.canceled": "cancelled",
}

# États remontés par l'API Yousign valant signature effective.
_REMOTE_DONE = {"done", "finished", "completed"}


class SignatureError(Exception):
    """Demande de signature refusée."""


async def request_signature(
    db: AsyncSession,
    note: BookingNote,
    *,
    document: bytes,
    filename: str,
) -> BookingNote:
    """Envoie la booking note à la signature du chargeur.

    Exige une booking note **diffusée** : mettre à la signature un brouillon
    ferait signer un document encore modifiable, ce qui viderait la signature de
    son sens.
    """
    if not yousign_svc.is_configured():
        raise SignatureError(
            "Signature électronique non configurée — la booking note reste "
            "signable à la main sur le document Word."
        )
    if note.status != "diffusee":
        raise SignatureError(
            "Diffusez la booking note avant de l'envoyer à la signature : "
            "un brouillon reste modifiable."
        )
    if note.signature_status == "signed":
        raise SignatureError("Booking note déjà signée.")
    if not (note.merchant_email or "").strip():
        raise SignatureError(
            "Aucune adresse e-mail de signataire sur la booking note."
        )

    first_name, last_name = yousign_svc.split_name(
        note.merchant_contact, note.merchant_email
    )
    result = await yousign_svc.create_signature_request(
        name=f"Booking note {note.reference}",
        document=document,
        filename=filename,
        signer_email=note.merchant_email,
        signer_first_name=first_name,
        signer_last_name=last_name,
    )

    note.signature_provider = "yousign"
    note.signature_request_id = str(result.get("id"))
    note.signature_status = "requested"
    note.signature_requested_at = datetime.now(UTC)
    await db.flush()
    return note


async def apply_signature_state(
    db: AsyncSession,
    note: BookingNote,
    *,
    event_type: str,
) -> BookingNote:
    """Applique l'état de signature après **relecture** chez Yousign.

    Idempotent : rejouer un événement ne change rien. Une demande déjà signée
    n'est jamais rétrogradée — un événement d'expiration arrivé en retard ne doit
    pas effacer une signature acquise.
    """
    target = _EVENT_TO_STATUS.get(event_type)
    if target is None or note.signature_status == "signed":
        return note

    # Le webhook dit quelle demande regarder ; c'est Yousign qui dit son état.
    remote_status = ""
    try:
        remote = await yousign_svc.retrieve_signature_request(
            note.signature_request_id or ""
        )
        remote_status = (remote.get("status") or "").lower()
    except yousign_svc.YousignError as exc:
        logger.warning(
            "Relecture Yousign impossible pour %s : %s", note.reference, exc
        )
        return note

    if target == "signed":
        if remote_status not in _REMOTE_DONE:
            # Le webhook annonce une signature que Yousign ne confirme pas :
            # on ne transitionne pas.
            logger.warning(
                "Webhook « done » non confirmé pour %s (état distant : %s)",
                note.reference,
                remote_status or "inconnu",
            )
            return note
        note.signature_status = "signed"
        note.signed_at = datetime.now(UTC)
        await _store_signed_document(db, note)
    else:
        note.signature_status = target

    await db.flush()
    return note


async def _store_signed_document(db: AsyncSession, note: BookingNote) -> None:
    """Archive le PDF signé et son empreinte (best-effort).

    L'échec du téléchargement ne doit pas annuler la signature : elle est acquise
    chez Yousign, et l'archive se rattrape. On journalise pour que le manque soit
    visible plutôt que silencieux.
    """
    from app.services import safe_files

    try:
        content = await yousign_svc.download_signed_document(
            note.signature_request_id or ""
        )
    except yousign_svc.YousignError as exc:
        logger.warning(
            "Document signé %s non récupéré : %s — à retélécharger", note.reference, exc
        )
        return
    if not content:
        return
    try:
        rel_path, _mime = safe_files.save_upload(
            content,
            f"{note.reference}_signe.pdf",
            # Sous-dossier dérivé d'un identifiant interne, jamais d'une saisie.
            subdir=f"booking_notes/{note.id}",
        )
    except Exception as exc:  # pragma: no cover - dépend du stockage
        logger.warning("Archivage du document signé %s échoué : %s", note.reference, exc)
        return
    note.signed_document_path = rel_path
    note.signed_document_sha256 = hashlib.sha256(content).hexdigest()


async def reconcile(db: AsyncSession, note: BookingNote) -> BookingNote:
    """Rattrape un webhook perdu en relisant l'état chez Yousign.

    Appelée à l'affichage de la booking note : un webhook peut se perdre, et
    l'écran ne doit pas rester bloqué sur « signature demandée » alors que le
    client a signé.
    """
    if (
        not yousign_svc.is_configured()
        or not note.signature_request_id
        or note.signature_status in ("signed", "declined", "cancelled", "expired")
    ):
        return note
    try:
        remote = await yousign_svc.retrieve_signature_request(note.signature_request_id)
    except yousign_svc.YousignError:
        return note
    status = (remote.get("status") or "").lower()
    if status in _REMOTE_DONE:
        note.signature_status = "signed"
        note.signed_at = datetime.now(UTC)
        await _store_signed_document(db, note)
        await db.flush()
    elif status in ("expired", "declined", "canceled"):
        note.signature_status = "cancelled" if status == "canceled" else status
        await db.flush()
    return note
