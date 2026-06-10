"""Ticket service — workflow, SLA, transitions.

Catégories (V3.0)            Priorités (V3.0)
- avarie                     - P1  : bloquant — SLA 2h
- avitaillement_urgent       - P2  : à traiter < 24h — SLA 8h
- formalite_douane           - P3  : informatif — SLA 72h
- reparation
- incident_cargo             Workflow
- medical                    open → in_progress → pending_external | resolved → closed
- securite                   any → cancelled
- meteo
- documentation
- autre
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketComment


CATEGORIES: tuple[str, ...] = (
    "avarie",
    "avitaillement_urgent",
    "formalite_douane",
    "reparation",
    "incident_cargo",
    "medical",
    "securite",
    "meteo",
    "documentation",
    "autre",
)

CATEGORY_LABELS: dict[str, str] = {
    "avarie": "Avarie cargo / matériel",
    "avitaillement_urgent": "Avitaillement urgent",
    "formalite_douane": "Formalité douanière",
    "reparation": "Réparation à quai",
    "incident_cargo": "Incident manutention",
    "medical": "Urgence médicale",
    "securite": "Sécurité (ISPS)",
    "meteo": "Routage météo",
    "documentation": "Document portuaire",
    "autre": "Autre",
}

PRIORITIES: tuple[str, ...] = ("P1", "P2", "P3")

PRIORITY_SLA_HOURS: dict[str, int] = {
    "P1": 2,
    "P2": 8,
    "P3": 72,
}

PRIORITY_LABELS: dict[str, str] = {
    "P1": "P1 — Bloquant",
    "P2": "P2 — À traiter",
    "P3": "P3 — Informatif",
}

# Allowed status transitions
_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"pending_external", "resolved", "cancelled"},
    "pending_external": {"in_progress", "resolved", "cancelled"},
    "resolved": {"closed", "in_progress"},     # can be re-opened
    "closed": set(),
    "cancelled": set(),
}

STATUS_LABELS: dict[str, str] = {
    "open": "Ouvert",
    "in_progress": "En cours",
    "pending_external": "Attente externe",
    "resolved": "Résolu",
    "closed": "Clos",
    "cancelled": "Annulé",
}

# 4 Kanban columns (closed/cancelled are off-board, accessed via "Archives")
KANBAN_COLUMNS: tuple[str, ...] = ("open", "in_progress", "pending_external", "resolved")


class TicketError(Exception):
    """Base ticket error."""


class InvalidTicketTransition(TicketError):
    pass


@dataclass(frozen=True)
class TicketStats:
    by_status: dict[str, int]
    p1_open: int
    sla_breached: int


# ---------------------------------------------------------------------------
# Reference + SLA
# ---------------------------------------------------------------------------


def generate_reference(year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    suffix = secrets.token_hex(2).upper()
    return f"TKT-{year}-{suffix}"


def sla_target(priority: str, created_at: datetime | None = None) -> datetime:
    hours = PRIORITY_SLA_HOURS.get(priority, 72)
    return (created_at or datetime.now(timezone.utc)) + timedelta(hours=hours)


def is_sla_breached(ticket: Ticket) -> bool:
    if ticket.status in ("resolved", "closed", "cancelled"):
        return ticket.sla_breached
    if ticket.sla_target_at and ticket.sla_target_at < datetime.now(timezone.utc):
        return True
    return False


# ---------------------------------------------------------------------------
# Create / transitions
# ---------------------------------------------------------------------------


async def create_ticket(
    db: AsyncSession,
    *,
    category: str,
    priority: str,
    title: str,
    description: str,
    leg_id: int | None = None,
    port_id: int | None = None,
    created_by_id: int | None = None,
    assigned_to_id: int | None = None,
    external_contact: str | None = None,
) -> Ticket:
    if category not in CATEGORIES:
        raise TicketError(f"Unknown category: {category}")
    if priority not in PRIORITIES:
        raise TicketError(f"Unknown priority: {priority}")
    if not title.strip() or not description.strip():
        raise TicketError("Title and description are required")

    now = datetime.now(timezone.utc)
    ticket = Ticket(
        reference=generate_reference(now.year),
        leg_id=leg_id,
        port_id=port_id,
        category=category,
        priority=priority,
        title=title.strip()[:200],
        description=description.strip(),
        status="open",
        created_by_id=created_by_id,
        assigned_to_id=assigned_to_id,
        external_contact=(external_contact or "").strip()[:200] or None,
        sla_target_at=sla_target(priority, now),
        sla_breached=False,
    )
    db.add(ticket)
    await db.flush()
    return ticket


def assert_transition(current: str, target: str) -> None:
    allowed = _TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidTicketTransition(f"{current} → {target} not allowed")


async def change_status(
    db: AsyncSession,
    ticket: Ticket,
    new_status: str,
    *,
    reason: str | None = None,
) -> Ticket:
    assert_transition(ticket.status, new_status)
    now = datetime.now(timezone.utc)
    ticket.status = new_status
    if new_status == "resolved":
        ticket.resolved_at = now
        ticket.sla_breached = ticket.sla_target_at is not None and ticket.sla_target_at < now
    elif new_status == "closed":
        ticket.closed_at = now
    elif new_status == "cancelled":
        ticket.cancelled_at = now
        ticket.cancelled_reason = (reason or "").strip()[:500] or None
    elif new_status == "in_progress" and ticket.resolved_at:
        # Re-opened — clear resolved/sla flags but keep history
        ticket.resolved_at = None
    await db.flush()
    return ticket


async def assign_ticket(
    db: AsyncSession, ticket: Ticket, user_id: int | None
) -> Ticket:
    ticket.assigned_to_id = user_id
    if ticket.status == "open" and user_id is not None:
        ticket.status = "in_progress"
    await db.flush()
    return ticket


async def add_comment(
    db: AsyncSession,
    ticket: Ticket,
    *,
    body: str,
    author_id: int | None,
    author_name: str | None,
    is_internal: bool = False,
) -> TicketComment:
    if not body.strip():
        raise TicketError("Comment body cannot be empty")
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=author_id,
        author_name=author_name,
        body=body.strip(),
        is_internal=is_internal,
    )
    db.add(comment)
    await db.flush()
    return comment


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_for_kanban(
    db: AsyncSession,
    *,
    leg_id: int | None = None,
    priority: str | None = None,
    category: str | None = None,
    assigned_to_id: int | None = None,
) -> dict[str, list[Ticket]]:
    stmt = select(Ticket).where(Ticket.status.in_(KANBAN_COLUMNS))
    if leg_id is not None:
        stmt = stmt.where(Ticket.leg_id == leg_id)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if category:
        stmt = stmt.where(Ticket.category == category)
    if assigned_to_id is not None:
        stmt = stmt.where(Ticket.assigned_to_id == assigned_to_id)
    stmt = stmt.order_by(Ticket.priority.asc(), Ticket.created_at.asc())

    rows = list((await db.execute(stmt)).scalars().all())
    bucket: dict[str, list[Ticket]] = {col: [] for col in KANBAN_COLUMNS}
    for t in rows:
        bucket.setdefault(t.status, []).append(t)
    return bucket


async def stats(db: AsyncSession) -> TicketStats:
    rows = list((await db.execute(select(Ticket))).scalars().all())
    by_status: dict[str, int] = {}
    p1_open = 0
    breached = 0
    for t in rows:
        by_status[t.status] = by_status.get(t.status, 0) + 1
        if t.priority == "P1" and t.status in KANBAN_COLUMNS:
            p1_open += 1
        if is_sla_breached(t):
            breached += 1
    return TicketStats(by_status=by_status, p1_open=p1_open, sla_breached=breached)


async def get_by_reference(db: AsyncSession, ref: str) -> Ticket | None:
    return (
        await db.execute(select(Ticket).where(Ticket.reference == ref))
    ).scalar_one_or_none()
