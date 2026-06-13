"""Staff booking backoffice — list, confirm, reject submitted bookings."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.booking import Booking
from app.models.client_account import ClientAccount
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services import messaging, notifications
from app.services.activity import record as activity_record
from app.services.booking import (
    BookingError,
    BookingItemInput,
    InvalidStatusTransition,
    advance,
    cancel,
    confirm,
    create_operator_draft,
    submit,
)
from app.services.booking_lifecycle import on_status_change
from app.services.capacity import (
    BookingClosed,
    CapacityExceeded,
    NotBookable,
    get_available_capacity,
)
from app.templating import templates

_ADVANCE_TARGETS = ("loaded", "at_sea", "discharged", "delivered")
_CHANNELS = ("client", "operator")
# Nombre de lignes cargo rendues côté serveur sur le formulaire opérateur
# (pas de JS — CSP stricte). On parse les lignes non vides au POST.
_OPERATOR_CARGO_ROWS = 5

router = APIRouter(prefix="/staff/bookings", tags=["staff-booking"])


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("booking", "C"))],
)
async def list_all(
    request: Request,
    status_filter: str | None = None,
    channel: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Vue unifiée des réservations (tous canaux). Filtres : statut + canal."""
    channel_filter = channel if channel in _CHANNELS else None

    stmt = select(Booking).order_by(Booking.created_at.desc()).limit(200)
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    if channel_filter:
        stmt = stmt.where(Booking.channel == channel_filter)
    bookings = (await db.execute(stmt)).scalars().all()

    # Compteurs par canal (badges de filtre) — indépendants du filtre statut.
    counts_rows = (
        await db.execute(select(Booking.channel, func.count()).group_by(Booking.channel))
    ).all()
    channel_counts: dict[str, int] = dict.fromkeys(_CHANNELS, 0)
    for ch, n in counts_rows:
        channel_counts[ch] = n
    channel_counts["all"] = sum(channel_counts[ch] for ch in _CHANNELS)

    return templates.TemplateResponse(
        "staff/bookings.html",
        {
            "request": request,
            "bookings": bookings,
            "status_filter": status_filter,
            "channel_filter": channel_filter,
            "channel_counts": channel_counts,
        },
    )


async def _bookable_legs(db: AsyncSession) -> list[dict]:
    """Legs ouverts à la réservation, avec libellés ports (rail opérateur).

    Même logique de filtrage que le wizard client (bookable + fenêtre ouverte
    + capacité résiduelle), mais sans publier de chiffres de remplissage.
    """
    res = await db.execute(
        select(Leg, Vessel)
        .join(Vessel, Vessel.id == Leg.vessel_id)
        .where(Leg.is_bookable.is_(True))
        .order_by(Leg.etd.asc())
        .limit(50)
    )
    out: list[dict] = []
    for leg, vessel in res.all():
        try:
            capacity = await get_available_capacity(db, leg.id)
        except (NotBookable, BookingClosed):
            continue
        if capacity.available_palettes <= 0:
            continue
        pol = await db.get(Port, leg.departure_port_id)
        pod = await db.get(Port, leg.arrival_port_id)
        out.append({"leg": leg, "vessel": vessel, "pol": pol, "pod": pod})
    return out


@router.get(
    "/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("booking", "M"))],
)
async def operator_new_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Formulaire opérateur : créer une réservation au nom d'un client."""
    accounts = (
        (
            await db.execute(
                select(ClientAccount)
                .where(ClientAccount.is_verified.is_(True))
                .order_by(ClientAccount.company_name.asc())
            )
        )
        .scalars()
        .all()
    )
    legs = await _bookable_legs(db)
    return templates.TemplateResponse(
        "staff/bookings/new.html",
        {
            "request": request,
            "accounts": accounts,
            "legs": legs,
            "cargo_rows": range(_OPERATOR_CARGO_ROWS),
        },
    )


@router.post(
    "/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("booking", "M"))],
)
async def operator_new_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("booking", "M")),
) -> HTMLResponse:
    form = await request.form()

    async def _reload(error: str) -> HTMLResponse:
        accounts = (
            (
                await db.execute(
                    select(ClientAccount)
                    .where(ClientAccount.is_verified.is_(True))
                    .order_by(ClientAccount.company_name.asc())
                )
            )
            .scalars()
            .all()
        )
        legs = await _bookable_legs(db)
        return templates.TemplateResponse(
            "staff/bookings/new.html",
            {
                "request": request,
                "accounts": accounts,
                "legs": legs,
                "cargo_rows": range(_OPERATOR_CARGO_ROWS),
                "error": error,
            },
            status_code=400,
        )

    try:
        client_account_id = int(form.get("client_account_id") or 0)
        leg_id = int(form.get("leg_id") or 0)
    except (ValueError, TypeError):
        return await _reload("Client et traversée sont obligatoires.")

    client_account = await db.get(ClientAccount, client_account_id)
    leg = await db.get(Leg, leg_id)
    if client_account is None or leg is None or not leg.is_bookable:
        return await _reload("Client ou traversée invalide.")

    # Lignes cargo : même convention que le wizard client (items-{i}-*).
    items_raw: list[BookingItemInput] = []
    for i in range(_OPERATOR_CARGO_ROWS):
        fmt = form.get(f"items-{i}-format")
        if not fmt:
            continue
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
            continue

    if not items_raw:
        return await _reload("Ajoutez au moins une ligne cargo (nombre + description).")

    try:
        booking, _quote = await create_operator_draft(
            db,
            client_account=client_account,
            leg=leg,
            items=items_raw,
            pickup_address=(form.get("pickup_address") or None),
            delivery_address=(form.get("delivery_address") or None),
            shipper_reference=(form.get("shipper_reference") or None),
            notes=(form.get("notes") or None),
        )
    except CapacityExceeded as e:
        return await _reload(f"Capacité insuffisante : {e}")
    except BookingError as e:
        return await _reload(str(e))

    # Le rail opérateur saute l'auto-soumission client : on passe directement
    # en "submitted" (prêt pour le flux de confirmation existant). Mêmes effets
    # de bord que le wizard client (notifications / email client).
    await submit(db, booking)
    await on_status_change(db, booking, "submitted")

    await activity_record(
        db,
        action="booking_create_operator",
        user_id=user.id,
        user_name=user.username,
        user_role=user.role,
        module="booking",
        entity_type="booking",
        entity_id=booking.id,
        entity_label=booking.reference,
    )
    return RedirectResponse(url=f"/staff/bookings/{booking.reference}", status_code=303)


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
            db,
            booking_id=booking.id,
            sender="staff",
            sender_name=user.username,
            body=body,
        )
        await notifications.notify_client(
            db,
            client_id=booking.client_account_id,
            type="new_booking_message",
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
    # COM-05 — pas d'émission de facture in-app : la facturation est gérée
    # par la comptabilité hors plateforme. La confirmation se limite au
    # changement de statut + notifications de cycle de vie (booking note).
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
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
