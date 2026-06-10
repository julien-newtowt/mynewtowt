"""Commercial — generators de référence, calc tarifs dégressifs, conversions.

Logique reprise de la V3.0.0 :
- Référence orders ORD-YYYY-NNNN (séquence par année).
- Référence grilles RG-YYYY-NNNN.
- Référence offres RO-YYYY-NNNN.
- Bracket lookup : retourne la 1re bracket dont max_qty >= qty.
- Bracket rate : base_rate × coeff × adjustment_index.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date as _date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import (
    DEFAULT_BRACKETS_FF,
    DEFAULT_BRACKETS_SHIPPER,
    Order,
    RateGrid,
    RateOffer,
)

# ─────────────────────────── References ────────────────────────────


async def _next_seq(db: AsyncSession, model, prefix: str, year: int) -> int:
    """Return the next sequence number for {prefix}-{year}-NNNN."""
    pattern = f"{prefix}-{year}-%"
    stmt = select(func.count(model.id)).where(model.reference.like(pattern))
    count = (await db.scalar(stmt)) or 0
    return count + 1


async def next_order_reference(db: AsyncSession, year: int | None = None) -> str:
    y = year or _date.today().year
    n = await _next_seq(db, Order, "ORD", y)
    return f"ORD-{y}-{n:04d}"


async def next_grid_reference(db: AsyncSession, year: int | None = None) -> str:
    y = year or _date.today().year
    n = await _next_seq(db, RateGrid, "RG", y)
    return f"RG-{y}-{n:04d}"


async def next_offer_reference(db: AsyncSession, year: int | None = None) -> str:
    y = year or _date.today().year
    n = await _next_seq(db, RateOffer, "RO", y)
    return f"RO-{y}-{n:04d}"


# ─────────────────────────── Pricing ───────────────────────────────


def default_brackets_for(client_type: str) -> list[dict]:
    return list(DEFAULT_BRACKETS_FF if client_type == "freight_forwarder"
                else DEFAULT_BRACKETS_SHIPPER)


def pick_bracket(brackets: Iterable[dict], qty: int) -> dict | None:
    """Return the first bracket whose `max_qty` covers `qty`."""
    sorted_b = sorted(brackets, key=lambda b: int(b["max_qty"]))
    for b in sorted_b:
        if qty <= int(b["max_qty"]):
            return b
    return sorted_b[-1] if sorted_b else None


def bracket_rate(
    *,
    base_rate: Decimal,
    coeff: Decimal | float,
    adjustment_index: Decimal | float = Decimal("1.0"),
) -> Decimal:
    return (Decimal(base_rate) * Decimal(coeff) * Decimal(adjustment_index)).quantize(Decimal("0.01"))


def compute_offer_total(
    *,
    base_rate: Decimal,
    coeff: Decimal | float,
    adjustment_index: Decimal | float,
    qty: int,
) -> Decimal:
    return (bracket_rate(
        base_rate=base_rate, coeff=coeff, adjustment_index=adjustment_index
    ) * qty).quantize(Decimal("0.01"))
