"""Booking wizard for clients — 4-step flow.

Steps:
1. /booking/new                  - choose a leg (form & search)
2. /booking/new/{leg_code}       - cargo details
3. /booking/new/{leg_code}/confirm  - review + accept terms
4. /booking/new/{leg_code}/pay   - payment (or invoice)
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_client
from app.database import get_db
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.activity import record as activity_record
from app.services.booking import (
    BookingError,
    BookingItemInput,
    create_draft,
    submit,
)
from app.services.capacity import (
    BookingClosed,
    CapacityExceeded,
    NotBookable,
    get_available_capacity,
)
from app.templating import templates

router = APIRouter(prefix="/booking", tags=["booking"])


@router.get("/new", response_class=HTMLResponse)
async def step_1_search(
    request: Request,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    # Show available legs (next 90 days, bookable)
    res = await db.execute(
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.is_bookable.is_(True))
        .order_by(Leg.etd.asc())
        .limit(20)
    )
    items = []
    for leg, vessel in res.all():
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        try:
            capacity = await get_available_capacity(db, leg.id)
        except (NotBookable, BookingClosed):
            continue
        items.append(
            {
                "leg": leg,
                "vessel": vessel,
                "pol": pol,
                "pod": pod,
                "capacity": capacity,
            }
        )
    return templates.TemplateResponse(
        "client/booking_step1.html",
        {"request": request, "client": client, "legs": items},
    )


@router.get("/new/{leg_code}", response_class=HTMLResponse)
async def step_2_cargo_form(
    request: Request,
    leg_code: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    leg = await _get_bookable_leg(db, leg_code)
    return templates.TemplateResponse(
        "client/booking_step2.html",
        {
            "request": request,
            "client": client,
            "leg": leg,
        },
    )


@router.post("/new/{leg_code}", response_class=HTMLResponse)
async def step_2_cargo_submit(
    request: Request,
    leg_code: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    leg = await _get_bookable_leg(db, leg_code)
    form = await request.form()

    items_raw = []
    # form encoding : items-0-format, items-0-count, items-0-description ...
    i = 0
    while True:
        fmt = form.get(f"items-{i}-format")
        if fmt is None:
            break
        try:
            count = int(form.get(f"items-{i}-count", "0"))
            description = (form.get(f"items-{i}-description") or "").strip()
            unit_weight = form.get(f"items-{i}-unit_weight_kg")
            hazardous = form.get(f"items-{i}-hazardous") == "on"
            stackable = form.get(f"items-{i}-stackable") != "off"
            if count > 0 and description:
                items_raw.append(
                    BookingItemInput(
                        pallet_format=str(fmt),
                        pallet_count=count,
                        cargo_description=description,
                        unit_weight_kg=Decimal(unit_weight) if unit_weight else None,
                        hazardous=hazardous,
                        stackable=stackable,
                    )
                )
        except (ValueError, TypeError):
            pass
        i += 1

    if not items_raw:
        return templates.TemplateResponse(
            "client/booking_step2.html",
            {
                "request": request,
                "client": client,
                "leg": leg,
                "error": "Veuillez ajouter au moins une palette à la réservation.",
            },
            status_code=400,
        )

    try:
        booking, quote = await create_draft(
            db,
            client=client,
            leg=leg,
            items=items_raw,
            pickup_address=(form.get("pickup_address") or None),
            delivery_address=(form.get("delivery_address") or None),
            shipper_reference=(form.get("shipper_reference") or None),
            notes=(form.get("notes") or None),
        )
    except CapacityExceeded as e:
        return templates.TemplateResponse(
            "client/booking_step2.html",
            {
                "request": request,
                "client": client,
                "leg": leg,
                "error": f"Capacité insuffisante: {e}",
            },
            status_code=400,
        )
    except BookingError as e:
        return templates.TemplateResponse(
            "client/booking_step2.html",
            {
                "request": request,
                "client": client,
                "leg": leg,
                "error": str(e),
            },
            status_code=400,
        )

    return RedirectResponse(
        url=f"/booking/{booking.reference}/confirm", status_code=303
    )


@router.get("/{ref}/confirm", response_class=HTMLResponse)
async def step_3_confirm_form(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await _get_my_draft(db, ref, client.id)
    leg = await db.get(Leg, booking.leg_id)
    return templates.TemplateResponse(
        "client/booking_step3.html",
        {"request": request, "client": client, "booking": booking, "leg": leg},
    )


@router.post("/{ref}/confirm", response_class=HTMLResponse)
async def step_3_confirm_submit(
    request: Request,
    ref: str,
    accept_terms: str = Form(""),
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await _get_my_draft(db, ref, client.id)
    if accept_terms != "on":
        leg = await db.get(Leg, booking.leg_id)
        return templates.TemplateResponse(
            "client/booking_step3.html",
            {
                "request": request,
                "client": client,
                "booking": booking,
                "leg": leg,
                "error": "Vous devez accepter les CGV pour confirmer.",
            },
            status_code=400,
        )

    from datetime import datetime, timezone

    booking.signed_terms_version = "v2026.1"
    booking.signed_terms_at = datetime.now(timezone.utc)
    await submit(db, booking)

    await activity_record(
        db,
        action="booking_submit",
        user_name=client.email,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
        ip_address=_client_ip(request),
    )

    return RedirectResponse(
        url=f"/booking/{booking.reference}/done", status_code=303
    )


@router.get("/{ref}/done", response_class=HTMLResponse)
async def step_4_done(
    request: Request,
    ref: str,
    client=Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = await _get_my_booking(db, ref, client.id)
    return templates.TemplateResponse(
        "client/booking_done.html",
        {"request": request, "client": client, "booking": booking},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_bookable_leg(db: AsyncSession, leg_code: str) -> Leg:
    leg = (
        await db.execute(select(Leg).where(Leg.leg_code == leg_code))
    ).scalar_one_or_none()
    if not leg or not leg.is_bookable:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not found")
    return leg


async def _get_my_booking(db: AsyncSession, ref: str, client_id: int):
    from app.services.booking import find_by_reference

    booking = await find_by_reference(db, ref)
    if not booking or booking.client_account_id != client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return booking


async def _get_my_draft(db: AsyncSession, ref: str, client_id: int):
    b = await _get_my_booking(db, ref, client_id)
    if b.status not in ("draft", "submitted"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Booking in status {b.status} cannot be modified",
        )
    return b


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
