"""ONB-06 — SOF auto à la déclaration d'un sinistre + rattachement marin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.claim import Claim
from app.models.crew import CrewMember
from app.models.leg import Leg
from app.models.port import Port
from app.models.sof_event import SofEvent
from app.models.vessel import Vessel
from app.routers.claims_router import claim_create
from tests.integration.conftest import FakeRequest


async def _leg(db) -> int:
    db.add(Vessel(id=1, code="ANE", name="Anemos", vessel_class="phoenix"))
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
    return 1


@pytest.mark.asyncio
async def test_claim_on_leg_posts_sof_claim_declared(db, staff_user):
    await _leg(db)
    resp = await claim_create(
        FakeRequest(),
        title="Avarie cale 2",
        description="Eau de mer",
        claim_type="cargo",
        occurred_at="2026-04-05T10:00:00",
        leg_id=1,
        booking_id=None,
        crew_member_id=None,
        provision_eur=None,
        insurer=None,
        insurer_claim_ref=None,
        insurance_contract_id=None,
        cargo_position=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    sof = (await db.execute(select(SofEvent).where(SofEvent.leg_id == 1))).scalars().all()
    assert len(sof) == 1
    ev = sof[0]
    assert ev.event_type == "CLAIM_DECLARED"
    assert "CLM-" in ev.label and "Avarie cale 2" in ev.label
    # L'horodatage du SOF = date de survenance déclarée.
    assert ev.occurred_at.replace(tzinfo=None) == datetime(2026, 4, 5, 10, 0, 0)


@pytest.mark.asyncio
async def test_claim_without_leg_has_no_sof(db, staff_user):
    await claim_create(
        FakeRequest(),
        title="Litige tiers",
        description="x",
        claim_type="third_party",
        occurred_at="2026-04-05T10:00:00",
        leg_id=None,
        booking_id=None,
        crew_member_id=None,
        provision_eur=None,
        insurer=None,
        insurer_claim_ref=None,
        insurance_contract_id=None,
        cargo_position=None,
        db=db,
        user=staff_user,
    )
    sof = (await db.execute(select(SofEvent))).scalars().all()
    assert sof == []


@pytest.mark.asyncio
async def test_crew_claim_links_crew_member(db, staff_user):
    db.add(CrewMember(id=7, full_name="Yann Le Marin", role="ab", is_active=True))
    await db.flush()
    await claim_create(
        FakeRequest(),
        title="Accident de travail",
        description="Chute",
        claim_type="crew",
        occurred_at="2026-04-05T10:00:00",
        leg_id=None,
        booking_id=None,
        crew_member_id=7,
        provision_eur=None,
        insurer=None,
        insurer_claim_ref=None,
        insurance_contract_id=None,
        cargo_position=None,
        db=db,
        user=staff_user,
    )
    claim = (await db.execute(select(Claim))).scalars().one()
    assert claim.crew_member_id == 7
    assert claim.claim_type == "crew"
