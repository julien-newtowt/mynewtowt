"""Client dashboard — once authenticated, the personal space.

Routes :
- /me          dashboard summary
- /me/bookings list of bookings
- /me/bookings/{ref} detail
- /me/invoices list of invoices
- /me/co2      CO2 certificates
- /me/account  profile + security
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client, AuthRequired
from app.database import get_db
from app.models.booking import Booking
from app.models.client_invoice import ClientInvoice
from app.models.co2_certificate import CO2Certificate
from app.services.booking import find_by_reference, list_for_client
from app.templating import templates

router = APIRouter(tags=["client-dashboard"])


@router.get("/me", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    bookings = await list_for_client(db, client.id, limit=20)
    active_count = sum(
        1
        for b in bookings
        if b.status in ("submitted", "confirmed", "loaded", "at_sea", "discharged")
    )
    co2_avoided = await db.scalar(
        select(func.coalesce(func.sum(CO2Certificate.co2_avoided_kg), 0))
        .where(CO2Certificate.client_account_id == client.id)
    )
    return templates.TemplateResponse(
        "client/dashboard.html",
        {
            "request": request,
            "client": client,
            "bookings": bookings,
            "active_count": active_count,
            "co2_avoided_kg": float(co2_avoided or 0),
        },
    )


@router.get("/me/bookings", response_class=HTMLResponse)
async def bookings_list(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    bookings = await list_for_client(db, client.id, limit=200)
    return templates.TemplateResponse(
        "client/bookings_list.html",
        {"request": request, "client": client, "bookings": bookings},
    )


@router.get("/me/bookings/{ref}", response_class=HTMLResponse)
async def booking_detail(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return templates.TemplateResponse(
        "client/booking_detail.html",
        {"request": request, "client": client, "booking": booking},
    )


@router.get("/me/invoices", response_class=HTMLResponse)
async def invoices(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    res = await db.execute(
        select(ClientInvoice)
        .where(ClientInvoice.client_account_id == client.id)
        .order_by(ClientInvoice.issued_at.desc())
    )
    return templates.TemplateResponse(
        "client/invoices.html",
        {"request": request, "client": client, "invoices": list(res.scalars().all())},
    )


@router.get("/me/co2", response_class=HTMLResponse)
async def co2(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    res = await db.execute(
        select(CO2Certificate)
        .where(CO2Certificate.client_account_id == client.id)
        .order_by(CO2Certificate.issued_at.desc())
    )
    return templates.TemplateResponse(
        "client/co2.html",
        {"request": request, "client": client, "certificates": list(res.scalars().all())},
    )


@router.get("/me/account", response_class=HTMLResponse)
async def account(
    request: Request,
    client=Depends(get_current_client),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "client/account.html",
        {"request": request, "client": client},
    )
