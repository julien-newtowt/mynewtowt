"""Crew P1 — reprise (CREW-06 API par navire, CREW-08 désactivation marin)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.crew import CrewAssignment, CrewMember
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _setup(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    db.add(
        Leg(
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
    )
    await db.flush()


# ─────────────────────────────── CREW-06 ───────────────────────────────


@pytest.mark.asyncio
async def test_api_by_vessel(db, staff_user):
    from app.routers.crew_router import crew_api_by_vessel

    await _setup(db)
    m = CrewMember(full_name="Jean Marin", role="capitaine", nationality="FR")
    db.add(m)
    await db.flush()
    db.add(
        CrewAssignment(
            crew_member_id=m.id, leg_id=1, vessel_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC)
        )
    )
    await db.flush()

    resp = await crew_api_by_vessel(1, db=db, user=staff_user)
    data = json.loads(resp.body)
    assert data["count"] == 1
    assert data["crew"][0]["full_name"] == "Jean Marin"
    assert data["crew"][0]["role"] == "capitaine"

    # navire sans équipage → liste vide
    resp2 = await crew_api_by_vessel(999, db=db, user=staff_user)
    assert json.loads(resp2.body)["count"] == 0


# ─────────────────────────────── CREW-08 ───────────────────────────────


@pytest.mark.asyncio
async def test_member_deactivation_blocked_when_embarked(db, staff_user):
    from fastapi import HTTPException

    from app.routers.crew_router import crew_member_toggle_active

    await _setup(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    # embarquement EN COURS (pas de disembark)
    db.add(
        CrewAssignment(
            crew_member_id=m.id, leg_id=1, vessel_id=1, embark_at=datetime(2026, 4, 1, tzinfo=UTC)
        )
    )
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await crew_member_toggle_active(m.id, _Req(), db=db, user=staff_user)
    assert exc.value.status_code == 400
    await db.refresh(m)
    assert m.is_active is True  # toujours actif


@pytest.mark.asyncio
async def test_member_deactivation_allowed_when_disembarked(db, staff_user):
    from app.routers.crew_router import crew_member_toggle_active

    await _setup(db)
    m = CrewMember(full_name="Jean Marin", role="matelot")
    db.add(m)
    await db.flush()
    # embarquement terminé (débarqué dans le passé)
    db.add(
        CrewAssignment(
            crew_member_id=m.id,
            leg_id=1,
            vessel_id=1,
            embark_at=datetime(2026, 4, 1, tzinfo=UTC),
            disembark_at=datetime(2026, 4, 10, tzinfo=UTC),
        )
    )
    await db.flush()

    resp = await crew_member_toggle_active(m.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(m)
    assert m.is_active is False
    # réactivation
    await crew_member_toggle_active(m.id, _Req(), db=db, user=staff_user)
    await db.refresh(m)
    assert m.is_active is True
