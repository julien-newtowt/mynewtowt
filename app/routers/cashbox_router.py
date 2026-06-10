"""Onboard cashbox routes — one cashbox per vessel, multi-currency."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.onboard_cashbox import (
    CATEGORY_LABELS,
    CURRENCY_LABELS,
    MOVEMENT_CATEGORIES,
    SUPPORTED_CURRENCIES,
)
from app.models.vessel import Vessel
from app.permissions import require_permission
from app.services.activity import record as activity_record
from app.services.cashbox import (
    CashboxError,
    add_movement,
    balances,
    get_or_create,
    recent_movements,
)
from app.templating import templates

router = APIRouter(prefix="/cashbox", tags=["cashbox"])


@router.get("", response_class=HTMLResponse)
async def cashbox_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())
    summary = []
    for v in vessels:
        cb = await get_or_create(db, v.id)
        bal = await balances(db, cb)
        summary.append({"vessel": v, "cashbox": cb, "balances": bal})
    return templates.TemplateResponse(
        "staff/cashbox/index.html",
        {
            "request": request, "user": user,
            "summary": summary, "currency_labels": CURRENCY_LABELS,
        },
    )


@router.get("/{vessel_id}", response_class=HTMLResponse)
async def cashbox_detail(
    request: Request,
    vessel_id: int,
    currency: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "C")),
) -> HTMLResponse:
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    cb = await get_or_create(db, vessel_id)
    bal = await balances(db, cb)
    mvts = await recent_movements(db, cb, currency=currency, limit=200)
    return templates.TemplateResponse(
        "staff/cashbox/detail.html",
        {
            "request": request, "user": user,
            "vessel": vessel, "cashbox": cb,
            "balances": bal, "movements": mvts,
            "currency_filter": currency,
            "currencies": SUPPORTED_CURRENCIES,
            "currency_labels": CURRENCY_LABELS,
            "categories": MOVEMENT_CATEGORIES,
            "category_labels": CATEGORY_LABELS,
        },
    )


@router.post("/{vessel_id}/movement")
async def add_mov(
    request: Request,
    vessel_id: int,
    amount: str = Form(...),
    currency: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    movement_kind: str = Form("expense"),  # "income" | "expense"
    occurred_at: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("captain", "M")),
) -> RedirectResponse:
    cb = await get_or_create(db, vessel_id)
    try:
        amt = Decimal(amount.replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid amount")
    # Negative for expenses
    if movement_kind == "expense" and amt > 0:
        amt = -amt
    occ = None
    if occurred_at:
        try:
            occ = datetime.fromisoformat(occurred_at.replace("T", " "))
            if occ.tzinfo is None:
                occ = occ.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        mov = await add_movement(
            db, cb,
            amount=amt, currency=currency, category=category,
            description=description, occurred_at=occ,
            recorded_by_id=user.id,
        )
    except CashboxError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_record(
        db, action="cashbox_movement",
        user_id=user.id, user_name=user.username, user_role=user.role,
        module="captain", entity_type="cashbox_movement", entity_id=mov.id,
        detail=f"vessel={vessel_id} {amt} {currency} {category}",
    )
    return RedirectResponse(url=f"/cashbox/{vessel_id}", status_code=303)
