"""COM-08 — performance commerciale par grille tarifaire + CA.

Restaure la visibilité commerciale de la V2 : pour chaque grille, le nombre
d'offres émises / acceptées, de commandes, le chiffre d'affaires réalisé et le
taux de conversion (offres acceptées / offres émises). Plus les totaux globaux.

Pur SQL agrégé (pas de chargement ligne à ligne) — sûr pour un tableau de bord.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Order, RateGrid, RateOffer

# Statuts d'offre considérés « émise » (au moins envoyée au client).
OFFER_EMITTED_STATUSES = ("sent", "accepted", "declined", "expired")
# Statuts de commande dont le CA est « réalisé » (confirmé et au-delà).
ORDER_REALIZED_STATUSES = ("confirmed", "loaded", "delivered")


def _conversion_pct(accepted: int, emitted: int) -> float | None:
    return round(100 * accepted / emitted, 1) if emitted else None


async def grid_performance(db: AsyncSession) -> list[dict]:
    """KPI par grille : offres émises/acceptées, commandes, CA, conversion.

    N'inclut que les grilles ayant au moins une offre ou une commande
    (les grilles sans activité ne polluent pas le tableau).
    """
    # Offres regroupées par grille × statut.
    offer_rows = (
        await db.execute(
            select(RateOffer.grid_id, RateOffer.status, func.count(RateOffer.id)).group_by(
                RateOffer.grid_id, RateOffer.status
            )
        )
    ).all()
    offers: dict[int, dict[str, int]] = {}
    for gid, status, cnt in offer_rows:
        if gid is not None:
            offers.setdefault(gid, {})[status] = int(cnt or 0)

    # Commandes regroupées par grille : nombre + CA réalisé.
    realized_ca = func.sum(
        case((Order.status.in_(ORDER_REALIZED_STATUSES), Order.total_eur), else_=0)
    )
    order_rows = (
        await db.execute(
            select(Order.rate_grid_id, func.count(Order.id), realized_ca).group_by(
                Order.rate_grid_id
            )
        )
    ).all()
    orders: dict[int, tuple[int, Decimal]] = {}
    for gid, cnt, ca in order_rows:
        if gid is not None:
            orders[gid] = (int(cnt or 0), Decimal(ca or 0))

    active_grid_ids = set(offers) | set(orders)
    if not active_grid_ids:
        return []
    grids = (
        (await db.execute(select(RateGrid).where(RateGrid.id.in_(active_grid_ids)))).scalars().all()
    )

    out: list[dict] = []
    for g in grids:
        ostat = offers.get(g.id, {})
        emitted = sum(ostat.get(s, 0) for s in OFFER_EMITTED_STATUSES)
        accepted = ostat.get("accepted", 0)
        order_count, ca = orders.get(g.id, (0, Decimal(0)))
        out.append(
            {
                "grid_id": g.id,
                "grid_reference": g.reference,
                "is_default": bool(g.is_default),
                "offers_emitted": emitted,
                "offers_accepted": accepted,
                "orders_count": order_count,
                "ca_eur": ca,
                "conversion_pct": _conversion_pct(accepted, emitted),
            }
        )
    out.sort(key=lambda r: r["ca_eur"], reverse=True)
    return out


async def commercial_totals(db: AsyncSession) -> dict:
    """Totaux globaux : CA réalisé + taux de conversion toutes grilles."""
    ca_total = (
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_eur), 0)).where(
                Order.status.in_(ORDER_REALIZED_STATUSES)
            )
        )
    ) or 0
    emitted = (
        await db.scalar(
            select(func.count(RateOffer.id)).where(RateOffer.status.in_(OFFER_EMITTED_STATUSES))
        )
    ) or 0
    accepted = (
        await db.scalar(select(func.count(RateOffer.id)).where(RateOffer.status == "accepted"))
    ) or 0
    return {
        "ca_total_eur": Decimal(ca_total),
        "offers_emitted": int(emitted),
        "offers_accepted": int(accepted),
        "conversion_pct": _conversion_pct(int(accepted), int(emitted)),
    }
