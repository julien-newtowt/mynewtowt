"""Staff booking backoffice — list, confirm, reject submitted bookings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.permissions import require_permission
from app.services import invoicing, messaging, notifications
from app.services.activity import record as activity_record
from app.services.booking import InvalidStatusTransition, advance, cancel, confirm
from app.services.booking_lifecycle import on_status_change
from app.templating import templates

_ADVANCE_TARGETS = ("loaded", "at_sea", "discharged", "delivered")

router = APIRouter(prefix="/staff/bookings", tags=["staff-booking"])


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("booking", "C"))],
)
async def list_all(
    request: Request,
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    stmt = select(Booking).order_by(Booking.created_at.desc()).limit(200)
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    bookings = (await db.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        "staff/bookings.html",
        {"request": request, "bookings": bookings, "status_filter": status_filter},
    )


@router.get(
    "/{ref}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("booking", "C"))],
)
async def detail(
    request: Request,
    ref: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    client = await db.get(ClientAccount, booking.client_account_id)
    messages = await messaging.list_for_booking(db, booking.id)
    await messaging.mark_thread_read(db, booking.id, reader="staff")
    return templates.TemplateResponse(
        "staff/booking_detail.html",
        {"request": request, "booking": booking, "client": client, "messages": messages},
    )


@router.post(
    "/{ref}/messages",
    dependencies=[Depends(require_permission("booking", "M"))],
)
async def post_staff_message(
    ref: str,
    body: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("booking", "M")),
) -> RedirectResponse:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.strip():
        await messaging.post(
            db, booking_id=booking.id, sender="staff",
            sender_name=user.username, body=body,
        )
        await notifications.notify_client(
            db, client_id=booking.client_account_id, type="new_booking_message",
            title=f"Nouveau message NEWTOWT — {booking.reference}",
            link=f"/me/bookings/{booking.reference}#messages",
        )
    return RedirectResponse(url=f"/staff/bookings/{ref}#messages", status_code=303)


@router.post(
    "/{ref}/confirm",
    dependencies=[Depends(require_permission("booking", "M"))],
)
async def confirm_booking(
    request: Request,
    ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("booking", "M")),
) -> RedirectResponse:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await confirm(db, booking)
    await invoicing.issue_for_booking(db, booking)
    await on_status_change(db, booking, "confirmed")
    await activity_record(
        db,
        action="booking_confirm",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
    )
    return RedirectResponse(url="/staff/bookings", status_code=303)


@router.post(
    "/{ref}/reject",
    dependencies=[Depends(require_permission("booking", "M"))],
)
async def reject_booking(
    request: Request,
    ref: str,
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("booking", "M")),
) -> RedirectResponse:
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await cancel(db, booking, reason=reason)
    await on_status_change(db, booking, "cancelled")
    await activity_record(
        db,
        action="booking_reject",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
        detail=reason,
    )
    return RedirectResponse(url="/staff/bookings", status_code=303)


@router.post(
    "/{ref}/advance",
    dependencies=[Depends(require_permission("booking", "M"))],
)
async def advance_booking(
    request: Request,
    ref: str,
    target: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("booking", "M")),
) -> RedirectResponse:
    """Avance une réservation dans le workflow de voyage
    (loaded → at_sea → discharged → delivered)."""
    if target not in _ADVANCE_TARGETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target status: {target}",
        )
    booking = (
        await db.execute(select(Booking).where(Booking.reference == ref))
    ).scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        await advance(db, booking, target)
    except InvalidStatusTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await activity_record(
        db,
        action="booking_advance",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
        detail=target,
    )
    return RedirectResponse(url=f"/staff/bookings/{ref}", status_code=303)
