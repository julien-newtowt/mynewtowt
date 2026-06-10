"""Cargo module — document generation (BL, packing list, invoice, CO2).

Two entry points:

- Staff (/cargo/...) : list of bookings ready to issue documents, preview
  + download PDFs for any booking regardless of owner.
- Client (/me/bookings/{ref}/{doc}.pdf) : owner-only download of their
  own booking's documents.

Distance estimation: V3.0 uses a simple lookup table (orthodromic NM
between known port pairs). Beyond V3.0 we'll persist the actual leg
distance after the noon-report data is collected.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client
from app.database import get_db
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.anemos import resolve_distance_nm
from app.services.pdf_generator import (
    render_bill_of_lading,
    render_anemos_certificate,
    render_invoice,
    render_packing_list,
)
from app.templating import templates

router = APIRouter(tags=["cargo"])


async def _load_booking_bundle(
    db: AsyncSession, booking: Booking
) -> tuple[Leg, Vessel, Port, Port, ClientAccount]:
    leg = await db.get(Leg, booking.leg_id)
    vessel = await db.get(Vessel, leg.vessel_id) if leg else None
    pol = await db.get(Port, leg.departure_port_id) if leg else None
    pod = await db.get(Port, leg.arrival_port_id) if leg else None
    client = await db.get(ClientAccount, booking.client_account_id)
    # Eager-load items so the template never lazy-loads inside WeasyPrint.
    await db.refresh(booking, attribute_names=["items"])
    if not (leg and vessel and pol and pod and client):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is missing referenced data (vessel/port/client)",
        )
    return leg, vessel, pol, pod, client


# ---------------------------------------------------------------------------
# Staff — cargo dashboard
# ---------------------------------------------------------------------------


@router.get("/cargo", response_class=HTMLResponse)
async def cargo_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    """List bookings that are 'issuable' (confirmed or beyond)."""
    issuable_statuses = ("confirmed", "loaded", "at_sea", "discharged", "delivered")
    res = await db.execute(
        select(Booking)
        .where(Booking.status.in_(issuable_statuses))
        .order_by(Booking.created_at.desc())
        .limit(200)
    )
    bookings = list(res.scalars().all())
    return templates.TemplateResponse(
        "staff/cargo/index.html",
        {"request": request, "user": user, "bookings": bookings},
    )


@router.get("/cargo/booking/{ref}", response_class=HTMLResponse)
async def cargo_booking_detail(
    request: Request,
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> HTMLResponse:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    return templates.TemplateResponse(
        "staff/cargo/booking_detail.html",
        {
            "request": request,
            "user": user,
            "booking": booking,
            "leg": leg,
            "vessel": vessel,
            "pol": pol,
            "pod": pod,
            "client": client,
        },
    )


# ---------------------------------------------------------------------------
# Staff PDF endpoints (all bookings)
# ---------------------------------------------------------------------------


@router.get("/cargo/booking/{ref}/bl.pdf")
async def staff_bl_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _bl_response(db, ref)


@router.get("/cargo/booking/{ref}/packing-list.pdf")
async def staff_pl_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _packing_response(db, ref)


@router.get("/cargo/booking/{ref}/invoice.pdf")
async def staff_invoice_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _invoice_response(db, ref)


@router.get("/cargo/booking/{ref}/anemos.pdf")
async def staff_anemos_pdf(
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("cargo", "C")),
) -> Response:
    return await _co2_response(db, ref)


@router.get("/cargo/booking/{ref}/co2-certificate.pdf")
async def staff_co2_pdf_legacy(ref: str):
    """Backward-compat : ancien chemin → 301 vers anemos.pdf."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/cargo/booking/{ref}/anemos.pdf", status_code=301)


# ---------------------------------------------------------------------------
# Client PDF endpoints (owner-only)
# ---------------------------------------------------------------------------


@router.get("/me/bookings/{ref}/bl.pdf")
async def client_bl_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _bl_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/packing-list.pdf")
async def client_pl_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _packing_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/invoice.pdf")
async def client_invoice_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await _invoice_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/anemos.pdf")
async def client_anemos_pdf(
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Label Anemos (anciennement certificat CO₂) — PDF téléchargeable."""
    return await _co2_response(db, ref, owner_client_id=client.id)


@router.get("/me/bookings/{ref}/co2-certificate.pdf")
async def client_co2_pdf_legacy(ref: str):
    """Backward-compat : redirects 301 vers anemos.pdf."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=f"/me/bookings/{ref}/anemos.pdf", status_code=301,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_booking(
    db: AsyncSession, ref: str, owner_client_id: int | None = None
) -> Booking:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if owner_client_id is not None and booking.client_account_id != owner_client_id:
        raise HTTPException(status_code=404, detail="Not found")
    return booking


def _pdf_response(doc) -> Response:
    return Response(
        content=doc.pdf,
        media_type=doc.mime,
        headers={"Content-Disposition": f'inline; filename="{doc.filename}"'},
    )


async def _bl_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    if booking.status in ("draft",):
        raise HTTPException(
            status_code=400,
            detail="Bill of Lading not available until the booking is confirmed",
        )
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    doc = render_bill_of_lading(
        booking=booking, leg=leg, vessel=vessel, pol=pol, pod=pod, client=client
    )
    return _pdf_response(doc)


async def _packing_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    doc = render_packing_list(
        booking=booking, leg=leg, vessel=vessel, pol=pol, pod=pod, client=client
    )
    return _pdf_response(doc)


async def _invoice_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    # Look for an existing ClientInvoice; if none, the PDF acts as a quote.
    from app.models.client_invoice import ClientInvoice

    invoice = (
        await db.execute(
            select(ClientInvoice).where(ClientInvoice.booking_id == booking.id)
            .order_by(ClientInvoice.issued_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    doc = render_invoice(
        booking=booking, leg=leg, vessel=vessel, pol=pol, pod=pod,
        client=client, invoice=invoice,
    )
    return _pdf_response(doc)


async def _co2_response(db, ref, owner_client_id=None) -> Response:
    booking = await _get_booking(db, ref, owner_client_id)
    leg, vessel, pol, pod, client = await _load_booking_bundle(db, booking)
    if booking.status not in ("discharged", "delivered"):
        raise HTTPException(
            status_code=400,
            detail="CO2 certificate is issued once the cargo is discharged",
        )
    distance = resolve_distance_nm(leg, pol, pod)
    from app.models.anemos_certificate import AnemosCertificate

    cert = (
        await db.execute(
            select(AnemosCertificate).where(AnemosCertificate.booking_id == booking.id)
        )
    ).scalar_one_or_none()
    doc = render_anemos_certificate(
        booking=booking, leg=leg, vessel=vessel, pol=pol, pod=pod,
        client=client, distance_nm=distance, certificate=cert,
    )
    return _pdf_response(doc)
