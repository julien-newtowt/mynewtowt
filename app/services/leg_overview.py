"""ESC-08 (tranche) — éléments du cockpit d'escale pour un leg.

- ``commercial_overview`` : commandes affectées + packing lists liées.
- ``port_call_steps`` : timeline du flux opérationnel (5 étapes) dérivée de
  l'état du leg (ATA/ATD), de ses opérations et du verrouillage d'escale.
- ``operations_by_lane`` : regroupement des opérations en swim-lanes par
  catégorie (``operation_type``) pour la vue « activités parallèles ».
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Client, Order
from app.models.escale import OPERATION_TYPES
from app.models.packing_list import PackingList

# Libellés FR courts des catégories d'opération (ordre = ``OPERATION_TYPES``).
OPERATION_TYPE_LABELS: dict[str, str] = {
    "technique": "Technique",
    "armement": "Armement",
    "relations_externes": "Relations externes",
    "documentaire": "Documentaire",
    "commercial": "Commercial",
}

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


def operations_by_lane(operations) -> list[dict]:
    """Regroupe les opérations en swim-lanes par ``operation_type``.

    Une lane par catégorie de ``OPERATION_TYPES`` **non vide**, dans l'ordre
    canonique des types. Les opérations de chaque lane sont triées par borne de
    début (``actual_start`` sinon ``planned_start`` ; ``None`` en queue).
    Les types inconnus (hors ``OPERATION_TYPES``) sont regroupés dans une lane
    « autre » en fin de liste pour ne jamais perdre une opération.

    Pure fonction (testable sans DB)."""
    ops = list(operations or [])

    def _sort_key(op):
        start = op.actual_start or op.planned_start
        # ``None`` trié en dernier (les opérations non bornées en fin de lane).
        return (start is None, start)

    buckets: dict[str, list] = {}
    for op in ops:
        buckets.setdefault(op.operation_type, []).append(op)

    lanes: list[dict] = []
    # Lanes connues, dans l'ordre canonique.
    for otype in OPERATION_TYPES:
        bucket = buckets.pop(otype, None)
        if bucket:
            lanes.append(
                {
                    "type": otype,
                    "label": OPERATION_TYPE_LABELS.get(otype, otype),
                    "ops": sorted(bucket, key=_sort_key),
                }
            )
    # Types inattendus (robustesse) — agrégés sans perte.
    for otype, bucket in buckets.items():
        lanes.append(
            {
                "type": otype,
                "label": OPERATION_TYPE_LABELS.get(otype, otype),
                "ops": sorted(bucket, key=_sort_key),
            }
        )
    return lanes


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
