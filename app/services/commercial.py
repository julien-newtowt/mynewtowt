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
from datetime import UTC, datetime, time
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
    return list(
        DEFAULT_BRACKETS_FF if client_type == "freight_forwarder" else DEFAULT_BRACKETS_SHIPPER
    )


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
    return (Decimal(base_rate) * Decimal(coeff) * Decimal(adjustment_index)).quantize(
        Decimal("0.01")
    )


def compute_offer_total(
    *,
    base_rate: Decimal,
    coeff: Decimal | float,
    adjustment_index: Decimal | float,
    qty: int,
) -> Decimal:
    return (
        bracket_rate(base_rate=base_rate, coeff=coeff, adjustment_index=adjustment_index) * qty
    ).quantize(Decimal("0.01"))


# ───────────────────────── Affectation commande → leg (COM-01) ──────────────


def leg_is_late_for_order(leg, order) -> bool:
    """True si l'ETA du ``leg`` dépasse la fin de la fenêtre de livraison
    souhaitée de la ``order`` (``delivery_date_end``). Sans fenêtre ou sans
    ETA, aucune commande n'est « hors délai ».

    On compare des **instants** (pas ``eta.date()``) contre la fin de journée
    UTC de la date butoir : un navire arrivant à 23 h UTC le jour J reste dans
    les délais ; à 00 h 30 le lendemain il est en retard. Cela évite l'aléa de
    troncature ``.date()`` aux abords de minuit (retard faussement posé d'un
    jour selon le fuseau). Un ETA naïf (SQLite de test) est interprété en UTC.
    """
    if order.delivery_date_end is None or leg.eta is None:
        return False
    deadline = datetime.combine(order.delivery_date_end, time(23, 59, 59), tzinfo=UTC)
    eta = leg.eta if leg.eta.tzinfo is not None else leg.eta.replace(tzinfo=UTC)
    return eta > deadline


def suggest_leg_for_order(legs: Iterable, order):
    """Suggère le meilleur leg pour une commande : le premier compatible
    livrant dans les délais ; à défaut, le premier compatible (le plus tôt).
    ``legs`` est supposé déjà trié par ETD croissant.
    """
    legs = list(legs)
    on_time = [lg for lg in legs if not leg_is_late_for_order(lg, order)]
    if on_time:
        return on_time[0]
    return legs[0] if legs else None


async def compatible_legs_for_order(db: AsyncSession, order) -> list:
    """Legs candidats à l'affectation d'une commande : filtrés sur la route
    souhaitée (POL/POD locodes de la commande) et non encore partis
    (``atd`` NULL), triés par ETD croissant.

    Sans route renseignée, retourne tous les legs à venir (le broker affine).
    """
    from sqlalchemy.orm import aliased

    from app.models.leg import Leg
    from app.models.port import Port

    dep = aliased(Port)
    arr = aliased(Port)
    stmt = (
        select(Leg)
        .join(dep, Leg.departure_port_id == dep.id)
        .join(arr, Leg.arrival_port_id == arr.id)
        .where(Leg.atd.is_(None))
    )
    if order.departure_locode:
        stmt = stmt.where(dep.locode == order.departure_locode.upper())
    if order.arrival_locode:
        stmt = stmt.where(arr.locode == order.arrival_locode.upper())
    stmt = stmt.order_by(Leg.etd.asc())
    return list((await db.execute(stmt)).scalars().all())
