"""Agrégation des documents d'un client (hub /me/documents).

Combine les documents *générés* (BL, packing list, facture, label Anemos —
liens vers les endpoints PDF owner-only existants de ``cargo_router``) et
les pièces *uploadées* par le client (``PackingListDocument`` rattachées au
booking). Les règles de disponibilité reflètent celles de ``cargo_router``.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.packing_list import PackingListDocument
from app.services.booking import list_for_client as _list_bookings


def generated_docs_for(booking: Booking) -> list[dict]:
    ref = booking.reference
    not_draft = booking.status != "draft"
    discharged = booking.status in ("discharged", "delivered")
    return [
        {"kind": "bl", "label": "Bill of Lading",
         "url": f"/me/bookings/{ref}/bl.pdf", "available": not_draft},
        {"kind": "packing", "label": "Packing List",
         "url": f"/me/bookings/{ref}/packing-list.pdf", "available": True},
        {"kind": "invoice", "label": "Facture / Devis",
         "url": f"/me/bookings/{ref}/invoice.pdf", "available": True},
        {"kind": "anemos", "label": "Label Anemos",
         "url": f"/me/bookings/{ref}/anemos.pdf", "available": discharged},
    ]


async def list_for_client(db: AsyncSession, client_id: int) -> list[dict]:
    """Renvoie, par booking du client, ses documents générés + uploadés."""
    bookings = await _list_bookings(db, client_id, limit=200)
    out: list[dict] = []
    for b in bookings:
        uploaded = (
            await db.execute(
                select(PackingListDocument)
                .where(PackingListDocument.booking_id == b.id)
                .order_by(PackingListDocument.uploaded_at.desc())
            )
        ).scalars().all()
        out.append({
            "booking": b,
            "generated": generated_docs_for(b),
            "uploaded": list(uploaded),
        })
    return out
