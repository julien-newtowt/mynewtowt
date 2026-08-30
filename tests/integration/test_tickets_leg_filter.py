"""Tickets — filtre kanban par leg (reprise UX legacy, phase 1).

``GET /tickets`` acceptait déjà les filtres priorité/catégorie ; le service
``list_for_kanban`` acceptait déjà ``leg_id`` mais la route ne le transmettait
pas. Ces tests couvrent le branchement du paramètre de query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from tests.integration.conftest import FakeRequest


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543", flag="FR"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(
        id=1,
        leg_code="1CFRBR6",
        vessel_id=1,
        departure_port_id=1,
        arrival_port_id=2,
        etd_ref=base,
        eta_ref=base + timedelta(days=20),
        etd=base,
        eta=base + timedelta(days=20),
    )
    db.add(leg)
    await db.flush()
    return leg


@pytest.mark.asyncio
async def test_kanban_filters_by_leg_id(db, staff_user):
    """GET /tickets?leg_id=N ne renvoie que les tickets rattachés à ce leg."""
    from app.routers.tickets_router import kanban
    from app.services.tickets import create_ticket

    leg = await _setup_leg(db)

    on_leg = await create_ticket(
        db,
        category="incident_cargo",
        priority="P2",
        title="Avarie palette sur leg",
        description="Ticket rattaché au leg 1CFRBR6",
        leg_id=leg.id,
    )
    off_leg = await create_ticket(
        db,
        category="formalite_douane",
        priority="P3",
        title="Ticket sans leg",
        description="Ticket non rattaché à un leg",
    )

    resp = await kanban(FakeRequest(), leg_id=leg.id, db=db, user=staff_user)

    assert resp.status_code == 200
    body = resp.body.decode()
    assert on_leg.reference in body
    assert off_leg.reference not in body


@pytest.mark.asyncio
async def test_kanban_without_leg_id_returns_all(db, staff_user):
    """GET /tickets sans leg_id renvoie tous les tickets, quel que soit leur leg."""
    from app.routers.tickets_router import kanban
    from app.services.tickets import create_ticket

    leg = await _setup_leg(db)

    on_leg = await create_ticket(
        db,
        category="incident_cargo",
        priority="P2",
        title="Avarie palette sur leg",
        description="Ticket rattaché au leg 1CFRBR6",
        leg_id=leg.id,
    )
    off_leg = await create_ticket(
        db,
        category="formalite_douane",
        priority="P3",
        title="Ticket sans leg",
        description="Ticket non rattaché à un leg",
    )

    resp = await kanban(FakeRequest(), leg_id=None, db=db, user=staff_user)

    assert resp.status_code == 200
    body = resp.body.decode()
    assert on_leg.reference in body
    assert off_leg.reference in body
