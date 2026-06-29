"""ESC-08 (tranche) — éléments du cockpit d'escale pour un leg.

- ``commercial_overview`` : commandes affectées + packing lists liées.
- ``port_call_steps`` : timeline du flux opérationnel (5 étapes) dérivée de
  l'état du leg (ATA/ATD), de ses opérations et du verrouillage d'escale.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Client, Order
from app.models.packing_list import PackingList

# Étapes du flux opérationnel d'escale (ordre chronologique).
_PORT_CALL_STEPS = (
    ("planifie", "Planifié"),
    ("arrivee", "Arrivée (ATA)"),
    ("operations", "Opérations"),
    ("appareillage", "Appareillage (ATD)"),
    ("cloture", "Clôturé"),
)


def port_call_steps(leg, operations) -> list[dict]:
    """Timeline du flux opérationnel : 5 étapes, chacune ``done`` / ``current`` /
    ``pending``. La première étape non terminée (dans l'ordre) est ``current``.

    Dérivée de : leg planifié (toujours fait) → ATA posée → toutes les opérations
    terminées (``actual_end``) → ATD posée → escale verrouillée."""
    ops = list(operations or [])
    done_flags = {
        "planifie": True,
        "arrivee": leg.ata is not None,
        "operations": bool(ops) and all(o.actual_end is not None for o in ops),
        "appareillage": leg.atd is not None,
        "cloture": getattr(leg, "escale_locked_at", None) is not None,
    }
    out: list[dict] = []
    current_assigned = False
    for key, label in _PORT_CALL_STEPS:
        if done_flags[key]:
            state = "done"
        elif not current_assigned:
            state = "current"
            current_assigned = True
        else:
            state = "pending"
        out.append({"key": key, "label": label, "state": state})
    return out


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
