"""ESC-06 — couplage opération d'escale ↔ équipage, alertes billets, auto-PAF.

Quand un agent d'escale saisit une opération d'**embarquement** ou de
**débarquement**, l'affectation équipage (``CrewAssignment``) correspondante
est créée / clôturée automatiquement (régression V3 : la saisie escale ne
créait plus l'embarquement). Un passage à la Police Aux Frontières (PAF) est
auto-généré aux ports français (contrôle Schengen). Les billets attachés aux
embarquements sont confrontés à la fenêtre du voyage pour lever des alertes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewAssignment
from app.models.escale import EscaleOperation
from app.models.leg import Leg
from app.models.port import Port

# Actions d'opération qui pilotent un mouvement d'équipage.
CREW_ACTIONS: frozenset[str] = frozenset({"embarquement", "debarquement"})

# Action auto-générée du passage Police Aux Frontières.
PAF_ACTION = "passage_paf"


def _naive(dt: datetime | None) -> datetime | None:
    """Retire la tzinfo (comparaisons homogènes SQLite naïf / Postgres aware)."""
    return dt.replace(tzinfo=None) if (dt is not None and dt.tzinfo is not None) else dt


async def couple_crew_assignment(
    db: AsyncSession, op: EscaleOperation, leg: Leg | None, crew_member_id: int | None
) -> CrewAssignment | None:
    """Crée (embarquement) ou clôt (débarquement) l'affectation d'un marin.

    - **embarquement** : nouvelle ``CrewAssignment`` (navire/leg, port de départ,
      date = réelle sinon prévue) ;
    - **débarquement** : on retrouve le dernier embarquement actif du marin sur
      le navire et on pose ``disembark_at`` + port d'arrivée.

    Retourne l'affectation touchée, ou ``None`` si l'action n'est pas un
    mouvement d'équipage ou si aucun marin n'est sélectionné.
    """
    if crew_member_id is None or op.action not in CREW_ACTIONS:
        return None
    when = op.actual_start or op.planned_start
    if op.action == "embarquement":
        assignment = CrewAssignment(
            crew_member_id=crew_member_id,
            leg_id=leg.id if leg else None,
            vessel_id=leg.vessel_id if leg else None,
            embark_at=when,
            embark_port_id=leg.departure_port_id if leg else None,
        )
        db.add(assignment)
        await db.flush()
        return assignment
    # débarquement → clôture du dernier embarquement actif (sans disembark)
    stmt = select(CrewAssignment).where(
        CrewAssignment.crew_member_id == crew_member_id,
        CrewAssignment.disembark_at.is_(None),
    )
    if leg is not None and leg.vessel_id is not None:
        stmt = stmt.where(CrewAssignment.vessel_id == leg.vessel_id)
    stmt = stmt.order_by(CrewAssignment.embark_at.desc().nullslast()).limit(1)
    assignment = (await db.execute(stmt)).scalar_one_or_none()
    if assignment is not None:
        assignment.disembark_at = when
        assignment.disembark_port_id = leg.arrival_port_id if leg else None
        await db.flush()
    return assignment


async def maybe_create_paf(
    db: AsyncSession, op: EscaleOperation, leg: Leg | None
) -> EscaleOperation | None:
    """Auto-génère un passage PAF aux ports français (idempotent par leg).

    La PAF (Police Aux Frontières) contrôle les mouvements d'équipage aux
    frontières françaises. À l'embarquement on regarde le port de départ, au
    débarquement le port d'arrivée. Un seul passage PAF auto par leg.
    """
    if op.action not in CREW_ACTIONS or leg is None:
        return None
    port_id = leg.departure_port_id if op.action == "embarquement" else leg.arrival_port_id
    port = await db.get(Port, port_id) if port_id else None
    if port is None or (port.country or "").upper() != "FR":
        return None
    existing = (
        await db.execute(
            select(EscaleOperation.id)
            .where(EscaleOperation.leg_id == leg.id)
            .where(EscaleOperation.action == PAF_ACTION)
            .limit(1)
        )
    ).first()
    if existing is not None:
        return None
    paf = EscaleOperation(
        leg_id=leg.id,
        direction=op.direction,
        operation_type="relations_externes",
        action=PAF_ACTION,
        label=f"Passage PAF — {port.name} (auto)",
        planned_start=op.actual_start or op.planned_start,
    )
    db.add(paf)
    await db.flush()
    return paf


def embarkation_alerts(assignments: list[CrewAssignment], leg: Leg | None) -> list[dict]:
    """Confronte embarquements & billets à la fenêtre du voyage.

    Retourne une liste d'alertes ``{level, member_id, message}`` :
    - embarquement sans date → warning ;
    - embarquement après l'ETD du voyage → warning (incompatibilité dates) ;
    - billet non chargé → info.
    """
    alerts: list[dict] = []
    etd = _naive(leg.etd or leg.etd_ref) if leg else None
    for a in assignments:
        embark = _naive(a.embark_at)
        if embark is None and a.disembark_at is None:
            alerts.append(
                {
                    "level": "warning",
                    "member_id": a.crew_member_id,
                    "message": "Date d'embarquement manquante",
                }
            )
        elif embark is not None and etd is not None and embark > etd:
            alerts.append(
                {
                    "level": "warning",
                    "member_id": a.crew_member_id,
                    "message": "Embarquement saisi après l'ETD du voyage",
                }
            )
        if not a.ticket_path:
            alerts.append(
                {"level": "info", "member_id": a.crew_member_id, "message": "Billet non chargé"}
            )
    return alerts
