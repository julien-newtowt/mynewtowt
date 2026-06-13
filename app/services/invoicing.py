"""Facturation client — module DORMANT.

⚠️ La **facturation n'est pas gérée par cet ERP** : elle est émise hors
plateforme par la comptabilité NEWTOWT (la plateforme produit une *booking
note* de confirmation, pas une facture). Ce module est conservé comme
réceptacle de données pour un futur export comptable et n'est plus appelé
par le cycle de vie booking.

TVA : le **transport maritime international est exonéré de TVA**
(art. 262 II CGI / art. 148 directive 2006/112/CE) — taux 0 %.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.client_invoice import ClientInvoice
from app.services.activity import record as activity_record

# Transport maritime international : exonéré de TVA (art. 262 II CGI).
VAT_RATE = Decimal("0")
_PAYMENT_TERMS_DAYS = 30
_CENTS = Decimal("0.01")


def generate_reference(year: int | None = None) -> str:
    year = year or datetime.now(UTC).year
    suffix = secrets.token_hex(3).upper()
    return f"INV-{year}-{suffix}"


def compute_amounts(amount_excl_vat: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """(HT, TVA, TTC) — TVA 0 % (transport maritime international exonéré)."""
    excl = (amount_excl_vat or Decimal("0")).quantize(_CENTS)
    vat = (excl * VAT_RATE).quantize(_CENTS)
    incl = (excl + vat).quantize(_CENTS)
    return excl, vat, incl


async def issue_for_booking(
    db: AsyncSession, booking: Booking, *, status: str = "issued"
) -> ClientInvoice:
    """Crée (ou retourne) la facture d'un booking. Idempotent."""
    existing = (
        await db.execute(select(ClientInvoice).where(ClientInvoice.booking_id == booking.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    base = booking.confirmed_price_eur or booking.estimated_price_eur or Decimal("0")
    excl, vat, incl = compute_amounts(base)
    now = datetime.now(UTC)

    invoice = ClientInvoice(
        reference=generate_reference(now.year),
        booking_id=booking.id,
        client_account_id=booking.client_account_id,
        due_at=now + timedelta(days=_PAYMENT_TERMS_DAYS),
        amount_excl_vat_eur=excl,
        vat_amount_eur=vat,
        amount_incl_vat_eur=incl,
        currency="EUR",
        status=status,
    )
    db.add(invoice)
    await db.flush()

    await activity_record(
        db,
        action="invoice_issued",
        user_name="system",
        module="finance",
        entity_type="client_invoice",
        entity_id=invoice.id,
        entity_label=invoice.reference,
    )
    return invoice
