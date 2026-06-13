"""Onboard cashbox service: balance computation per (vessel, currency).

Pure CRUD + aggregations. Movements are signed amounts:
positive = income/recharge, negative = expense.
"""

from __future__ import annotations

import calendar
import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboard_cashbox import (
    CATEGORY_LABELS,
    SUPPORTED_CURRENCIES,
    CashboxClosure,
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


class PeriodClosed(CashboxError):
    """La période visée est clôturée : mouvement refusé / lecture seule."""


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """(début, fin) UTC inclusifs d'un mois calendaire."""
    start = datetime(year, month, 1, tzinfo=UTC)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
    return start, end


async def get_or_create(db: AsyncSession, vessel_id: int) -> OnboardCashbox:
    cb = (
        await db.execute(select(OnboardCashbox).where(OnboardCashbox.vessel_id == vessel_id))
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
    receipt_mime: str | None = None,
) -> CashboxMovement:
    if currency.upper() not in SUPPORTED_CURRENCIES:
        raise CashboxError(f"Unsupported currency: {currency}")
    if amount == 0:
        raise CashboxError("Amount cannot be zero")
    if not description.strip():
        raise CashboxError("Description required")
    occ = occurred_at or datetime.now(UTC)
    if await is_period_closed(db, cashbox, currency.upper(), occ):
        raise PeriodClosed(
            "Période clôturée : impossible d'ajouter un mouvement à cette date."
        )
    mov = CashboxMovement(
        cashbox_id=cashbox.id,
        amount=amount,
        currency=currency.upper(),
        category=category,
        description=description.strip()[:300],
        leg_id=leg_id,
        port_id=port_id,
        occurred_at=occ,
        recorded_by_id=recorded_by_id,
        receipt_url=receipt_url,
        receipt_mime=receipt_mime,
    )
    db.add(mov)
    await db.flush()
    return mov


async def is_period_closed(
    db: AsyncSession, cashbox: OnboardCashbox, currency: str, when: datetime
) -> bool:
    """Vrai si une clôture (cashbox, devise) couvre la date ``when``."""
    stmt = (
        select(CashboxClosure.id)
        .where(
            CashboxClosure.cashbox_id == cashbox.id,
            CashboxClosure.currency == currency.upper(),
            CashboxClosure.period_start.is_not(None),
            CashboxClosure.period_start <= when,
            CashboxClosure.period_end >= when,
        )
        .limit(1)
    )
    return (await db.scalar(stmt)) is not None


async def balances(db: AsyncSession, cashbox: OnboardCashbox) -> list[CurrencyBalance]:
    """One row per currency that has at least one movement."""
    stmt = (
        select(
            CashboxMovement.currency,
            func.coalesce(func.sum(CashboxMovement.amount), 0).label("balance"),
            func.coalesce(
                func.sum(func.greatest(CashboxMovement.amount, 0)),
                0,
            ).label("income"),
            func.coalesce(
                func.sum(func.least(CashboxMovement.amount, 0)),
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


async def period_movements(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    year: int,
    month: int,
    currency: str | None = None,
) -> list[CashboxMovement]:
    start, end = month_bounds(year, month)
    stmt = (
        select(CashboxMovement)
        .where(
            CashboxMovement.cashbox_id == cashbox.id,
            CashboxMovement.occurred_at >= start,
            CashboxMovement.occurred_at <= end,
        )
        .order_by(CashboxMovement.occurred_at.asc())
    )
    if currency:
        stmt = stmt.where(CashboxMovement.currency == currency.upper())
    return list((await db.execute(stmt)).scalars().all())


def export_csv(movements: list[CashboxMovement], *, vessel_code: str, period: str) -> str:
    """Construit l'export comptable CSV d'une liste de mouvements (période)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Caisse de bord — {vessel_code} — période {period}"])
    w.writerow(
        ["Date", "Sens", "Catégorie", "Libellé", "Montant", "Devise",
         "Justificatif", "Saisi le", "Verrou"]
    )
    for m in movements:
        kind = "Encaissement" if m.amount > 0 else "Décaissement"
        cat = CATEGORY_LABELS.get(m.category, m.category)
        w.writerow(
            [
                m.occurred_at.strftime("%Y-%m-%d"),
                kind,
                cat,
                (m.description or "").replace("\n", " "),
                f"{m.amount:.2f}",
                m.currency,
                "oui" if m.receipt_url else "non",
                m.recorded_at.strftime("%Y-%m-%d %H:%M") if m.recorded_at else "",
                "verrouillé" if m.closure_id else "ouvert",
            ]
        )
    return buf.getvalue()


async def close_month(
    db: AsyncSession,
    cashbox: OnboardCashbox,
    *,
    year: int,
    month: int,
    counted: dict[str, Decimal],
    closed_by_id: int | None = None,
    export_path: str | None = None,
) -> list[CashboxClosure]:
    """Clôture mensuelle : fige le solde par devise et **verrouille** les
    mouvements de la période (lecture seule après export comptable).

    Idempotent au sens où une devise déjà clôturée pour ce mois est ignorée
    (la contrainte ``uq_closure_period`` la protège aussi). Renvoie les
    clôtures créées.
    """
    start, end = month_bounds(year, month)
    movs = await period_movements(db, cashbox, year=year, month=month)
    if not movs:
        raise CashboxError("Aucun mouvement sur la période — rien à clôturer.")

    # Devises déjà clôturées pour ce period_end → on ne re-clôture pas.
    already = {
        row[0]
        for row in (
            await db.execute(
                select(CashboxClosure.currency).where(
                    CashboxClosure.cashbox_id == cashbox.id,
                    CashboxClosure.period_end == end,
                )
            )
        ).all()
    }

    currencies = sorted({m.currency for m in movs} - already)
    if not currencies:
        raise CashboxError("Période déjà clôturée pour toutes les devises concernées.")

    now = datetime.now(UTC)
    created: list[CashboxClosure] = []
    for cur in currencies:
        # Solde cumulé à la fin de la période (cash théorique en caisse).
        computed = await db.scalar(
            select(func.coalesce(func.sum(CashboxMovement.amount), 0)).where(
                CashboxMovement.cashbox_id == cashbox.id,
                CashboxMovement.currency == cur,
                CashboxMovement.occurred_at <= end,
            )
        )
        computed = Decimal(computed or 0)
        counted_val = counted.get(cur, computed)
        period_movs = [m for m in movs if m.currency == cur]
        closure = CashboxClosure(
            cashbox_id=cashbox.id,
            currency=cur,
            period_start=start,
            period_end=end,
            counted_balance=counted_val,
            computed_balance=computed,
            variance=(counted_val - computed),
            movement_count=len(period_movs),
            exported_at=now if export_path else None,
            closed_at=now,
            closed_by_id=closed_by_id,
        )
        db.add(closure)
        await db.flush()
        # Verrouillage des mouvements de la devise sur la période.
        for m in period_movs:
            m.closure_id = closure.id
            m.locked_at = now
        created.append(closure)
    await db.flush()
    return created


async def list_closures(
    db: AsyncSession, cashbox: OnboardCashbox, limit: int = 50
) -> list[CashboxClosure]:
    stmt = (
        select(CashboxClosure)
        .where(CashboxClosure.cashbox_id == cashbox.id)
        .order_by(CashboxClosure.period_end.desc(), CashboxClosure.currency)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())
