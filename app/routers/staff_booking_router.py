"""Staff booking backoffice — list, confirm, reject submitted bookings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.booking import Booking
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.booking import cancel, confirm
from app.templating import templates

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
