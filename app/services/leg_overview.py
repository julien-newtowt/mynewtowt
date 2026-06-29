"""ESC-08 (tranche) — synthèse commerciale d'un leg pour la vue d'escale.

Expose, en lecture, les **commandes commerciales** affectées à un leg et les
**packing lists** associées (épinglées au leg ou rattachées à l'une de ses
commandes), avec liens vers Commercial / Cargo. Sert le cockpit d'escale.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Client, Order
from app.models.packing_list import PackingList


async def commercial_overview(db: AsyncSession, leg_id: int) -> dict:
    """Commandes du leg (+ nom client) et packing lists liées."""
    order_rows = (
        await db.execute(
            select(Order, Client.name)
            .join(Client, Order.client_id == Client.id)
            .where(Order.leg_id == leg_id)
            .order_by(Order.reference)
        )
    ).all()
    order_ids = [o.id for o, _ in order_rows]

    conds = [PackingList.leg_id == leg_id]
    if order_ids:
        conds.append(PackingList.order_id.in_(order_ids))
    packing_lists = list(
        (await db.execute(select(PackingList).where(or_(*conds)).order_by(PackingList.id)))
        .scalars()
        .all()
    )

    return {
        "orders": [{"order": o, "client_name": name} for o, name in order_rows],
        "packing_lists": packing_lists,
    }
