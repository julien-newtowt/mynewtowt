"""Onboard cashbox service: balance computation per (vessel, currency).

Pure CRUD + aggregations. Movements are signed amounts:
positive = income/recharge, negative = expense.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboard_cashbox import (
    SUPPORTED_CURRENCIES,
    CashboxMovement,
    OnboardCashbox,
)


@dataclass(frozen=True)
class CurrencyBalance:
    currency: str
    balance: Decimal
    income_total: Decimal
    expense_total: Decimal
    movement_count: int


class CashboxError(Exception):
    pass


async def get_or_create(db: AsyncSession, vessel_id: int) -> OnboardCashbox:
    cb = (
        await db.execute(
            select(OnboardCashbox).where(OnboardCashbox.vessel_id == vessel_id)
        )
    ).scalar_one_or_none()
    if cb:
        return cb
    cb = OnboardCashbox(vessel_id=vessel_id, is_active=True)
    db.add(cb)
    await db.flush()
    return cb


async def add_movement(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    amount: Decimal,
    currency: str,
    category: str,
    description: str,
    occurred_at: datetime | None = None,
    leg_id: int | None = None,
    port_id: int | None = None,
    recorded_by_id: int | None = None,
    receipt_url: str | None = None,
) -> CashboxMovement:
    if currency.upper() not in SUPPORTED_CURRENCIES:
        raise CashboxError(f"Unsupported currency: {currency}")
    if amount == 0:
        raise CashboxError("Amount cannot be zero")
    if not description.strip():
        raise CashboxError("Description required")
    mov = CashboxMovement(
        cashbox_id=cashbox.id,
        amount=amount,
        currency=currency.upper(),
        category=category,
        description=description.strip()[:300],
        leg_id=leg_id,
        port_id=port_id,
        occurred_at=occurred_at or datetime.now(UTC),
        recorded_by_id=recorded_by_id,
        receipt_url=receipt_url,
    )
    db.add(mov)
    await db.flush()
    return mov


async def balances(db: AsyncSession, cashbox: OnboardCashbox) -> list[CurrencyBalance]:
    """One row per currency that has at least one movement."""
    stmt = (
        select(
            CashboxMovement.currency,
            func.coalesce(func.sum(CashboxMovement.amount), 0).label("balance"),
            func.coalesce(
                func.sum(
                    func.greatest(CashboxMovement.amount, 0)
                ),
                0,
            ).label("income"),
            func.coalesce(
                func.sum(
                    func.least(CashboxMovement.amount, 0)
                ),
                0,
            ).label("expense"),
            func.count(CashboxMovement.id).label("cnt"),
        )
        .where(CashboxMovement.cashbox_id == cashbox.id)
        .group_by(CashboxMovement.currency)
        .order_by(CashboxMovement.currency)
    )
    rows = (await db.execute(stmt)).all()
    return [
        CurrencyBalance(
            currency=r.currency,
            balance=Decimal(r.balance or 0),
            income_total=Decimal(r.income or 0),
            expense_total=Decimal(r.expense or 0),  # negative
            movement_count=r.cnt,
        )
        for r in rows
    ]


async def recent_movements(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    currency: str | None = None,
    limit: int = 50,
) -> list[CashboxMovement]:
    stmt = (
        select(CashboxMovement)
        .where(CashboxMovement.cashbox_id == cashbox.id)
        .order_by(CashboxMovement.occurred_at.desc())
        .limit(limit)
    )
    if currency:
        stmt = stmt.where(CashboxMovement.currency == currency.upper())
    return list((await db.execute(stmt)).scalars().all())
