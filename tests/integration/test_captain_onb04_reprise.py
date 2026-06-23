"""ONB-04 — messagerie de bord enrichie : suppression, autocomplete @mention,
messages système (journal des actions clés)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import OnboardMessage, SofEvent
from app.models.user import User
from app.models.vessel import Vessel
from tests.integration.conftest import FakeRequest


async def _leg(db) -> Leg:
    db.add(Vessel(id=1, code="ANE", name="Anemos", vessel_class="phoenix"))
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
async def test_author_can_delete_own_message(db, staff_user):
    from app.routers.captain_router import delete_onboard_message

    await _leg(db)
    msg = OnboardMessage(leg_id=1, vessel_id=1, author_id=1, author_name="Admin", body="hi")
    db.add(msg)
    await db.flush()

    resp = await delete_onboard_message(1, msg.id, FakeRequest(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.execute(select(OnboardMessage))).scalars().all() == []


@pytest.mark.asyncio
async def test_non_author_non_admin_cannot_delete(db):
    from app.routers.captain_router import delete_onboard_message

    await _leg(db)
    msg = OnboardMessage(leg_id=1, vessel_id=1, author_id=1, author_name="Admin", body="hi")
    db.add(msg)
    await db.flush()

    other = SimpleNamespace(id=2, full_name="Marin", username="marin", role="marins")
    with pytest.raises(HTTPException) as ei:
        await delete_onboard_message(1, msg.id, FakeRequest(), db=db, user=other)
    assert ei.value.status_code == 403
    # Le message n'a pas été supprimé.
    assert len((await db.execute(select(OnboardMessage))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_system_message_not_deletable(db, staff_user):
    from app.routers.captain_router import delete_onboard_message

    await _leg(db)
    msg = OnboardMessage(
        leg_id=1,
        vessel_id=1,
        author_id=None,
        author_name="SYSTÈME",
        is_system=True,
        body="SOF signé",
    )
    db.add(msg)
    await db.flush()

    with pytest.raises(HTTPException) as ei:
        await delete_onboard_message(1, msg.id, FakeRequest(), db=db, user=staff_user)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_mention_autocomplete_filters_active_users(db, staff_user):
    from app.routers.captain_router import onboard_message_users

    db.add_all(
        [
            User(
                id=2,
                username="ycapitaine",
                full_name="Yann Capitaine",
                email="y@x.test",
                hashed_password="x",
                role="marins",
            ),
            User(
                id=3,
                username="zinactif",
                full_name="Zoe Inactive",
                email="z@x.test",
                hashed_password="x",
                role="marins",
                is_active=False,
            ),
        ]
    )
    await db.flush()

    resp = await onboard_message_users(q="capit", db=db, user=staff_user)
    data = json.loads(bytes(resp.body))
    usernames = {r["username"] for r in data}
    assert "ycapitaine" in usernames
    # Inactif exclu, et non-match exclu.
    assert "zinactif" not in usernames
    assert "admin" not in usernames


@pytest.mark.asyncio
async def test_sof_sign_posts_system_message(db, staff_user):
    from app.routers.captain_router import sign_sof_event

    leg = await _leg(db)
    ev = SofEvent(
        leg_id=leg.id,
        event_type="BERTHED",
        occurred_at=datetime(2026, 4, 2, 8, 0, tzinfo=UTC),
        recorded_by_id=1,
        recorded_by_name="Admin",
    )
    db.add(ev)
    await db.flush()

    await sign_sof_event(ev.id, FakeRequest(), db=db, user=staff_user)

    sys_msgs = [
        m for m in (await db.execute(select(OnboardMessage))).scalars().all() if m.is_system
    ]
    assert len(sys_msgs) == 1
    assert "BERTHED" in sys_msgs[0].body
    assert sys_msgs[0].author_name == "SYSTÈME"
