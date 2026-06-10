"""Cycle de vie du booking — effets de bord en un seul endroit.

``on_status_change(db, booking, new_status)`` est le point unique qui, à
chaque transition, notifie le client (in-app + email best-effort) et
déclenche les actions associées (émission du label Anemos au
débarquement). Appelé depuis ``services/booking.advance`` (voyage),
``booking_router`` (submit) et ``staff_booking_router`` (confirm/reject).

Les envois d'email sont best-effort (try/except) : un échec SMTP ne doit
jamais bloquer la mutation métier.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.client_invoice import ClientInvoice
from app.services import anemos, email, notifications

logger = logging.getLogger("booking_lifecycle")


# Lien de suivi par défaut selon l'étape.
def _track_link(ref: str) -> str:
    return f"/me/track/{ref}"


def _booking_link(ref: str) -> str:
    return f"/me/bookings/{ref}"


_EVENTS: dict[str, dict[str, str]] = {
    "submitted": {
        "type": "booking_submitted",
        "subject": "Réservation reçue",
        "heading": "Votre réservation est bien reçue",
        "message": "Nous avons bien reçu votre réservation {ref}. "
                   "Notre équipe la confirme sous 4 heures ouvrées.",
        "cta": "Voir ma réservation",
    },
    "confirmed": {
        "type": "booking_confirmed",
        "subject": "Réservation confirmée",
        "heading": "Votre réservation est confirmée",
        "message": "Votre réservation {ref} est confirmée. Une facture vous a été "
                   "émise (règlement par virement, échéance à 30 jours).",
        "cta": "Voir ma réservation",
    },
    "loaded": {
        "type": "booking_loaded",
        "subject": "Cargaison chargée",
        "heading": "Votre cargaison est chargée",
        "message": "Les palettes de la réservation {ref} sont chargées à bord du navire.",
        "cta": "Suivre la traversée",
    },
    "at_sea": {
        "type": "booking_at_sea",
        "subject": "Navire en mer",
        "heading": "Votre cargaison a pris la mer",
        "message": "Le navire transportant la réservation {ref} a appareillé. "
                   "Suivez sa position en direct.",
        "cta": "Suivre la traversée",
    },
    "discharged": {
        "type": "booking_discharged",
        "subject": "Cargaison débarquée",
        "heading": "Votre cargaison est débarquée",
        "message": "La cargaison de la réservation {ref} a été débarquée à destination. "
                   "Votre label Anemos est disponible.",
        "cta": "Suivre la traversée",
    },
    "delivered": {
        "type": "booking_delivered",
        "subject": "Livraison effectuée",
        "heading": "Votre cargaison est livrée",
        "message": "La réservation {ref} est livrée. Merci d'avoir choisi le "
                   "transport vélique décarboné.",
        "cta": "Voir ma réservation",
    },
    "cancelled": {
        "type": "booking_cancelled",
        "subject": "Réservation annulée",
        "heading": "Votre réservation a été annulée",
        "message": "La réservation {ref} a été annulée. "
                   "Contactez-nous pour toute question.",
        "cta": "Voir mes réservations",
    },
}


async def _send_email(client: ClientAccount, *, subject_line: str, heading: str,
                      message: str, cta_label: str, cta_url: str) -> None:
    try:
        await email.send_template(
            "booking_event",
            to=client.email,
            recipient_name=client.contact_name or client.company_name or client.email,
            subject_line=subject_line,
            heading=heading,
            message=message,
            cta_label=cta_label,
            cta_url=cta_url,
            site_url=settings.site_url,
        )
    except Exception:  # noqa: BLE001 — best-effort, ne jamais bloquer
        logger.warning("booking_event email failed for %s", client.email, exc_info=True)


async def on_status_change(db: AsyncSession, booking: Booking, new_status: str) -> None:
    """Notifie le client et déclenche les actions liées à la transition."""
    spec = _EVENTS.get(new_status)
    if spec is None:
        return
    client = await db.get(ClientAccount, booking.client_account_id)
    if client is None:
        return

    ref = booking.reference
    message = spec["message"].format(ref=ref)
    cta_url = _track_link(ref) if new_status in ("loaded", "at_sea", "discharged") else _booking_link(ref)
    if new_status == "cancelled":
        cta_url = "/me/bookings"

    # Label Anemos émis au débarquement / à la livraison.
    if new_status in ("discharged", "delivered"):
        try:
            await anemos.issue_for_booking(db, booking)
        except Exception:  # noqa: BLE001
            logger.warning("anemos issuance failed for %s", ref, exc_info=True)

    # Notification in-app client.
    await notifications.notify_client(
        db, client_id=client.id, type=spec["type"],
        title=f"{spec['subject']} — {ref}", link=cta_url,
    )

    # Email best-effort.
    await _send_email(
        client, subject_line=f"{spec['subject']} — {ref}",
        heading=spec["heading"], message=message,
        cta_label=spec["cta"], cta_url=cta_url,
    )

    # À la confirmation, signaler aussi la facture émise.
    if new_status == "confirmed":
        invoice = (
            await db.execute(
                select(ClientInvoice).where(ClientInvoice.booking_id == booking.id)
                .order_by(ClientInvoice.issued_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if invoice is not None:
            await notifications.notify_client(
                db, client_id=client.id, type="invoice_issued",
                title=f"Facture émise — {invoice.reference}",
                link="/me/invoices",
            )
            await _send_email(
                client, subject_line=f"Facture {invoice.reference}",
                heading="Votre facture est disponible",
                message=f"La facture {invoice.reference} d'un montant de "
                        f"{invoice.amount_incl_vat_eur} EUR TTC est disponible "
                        f"dans votre espace client (règlement par virement).",
                cta_label="Voir mes factures", cta_url="/me/invoices",
            )
