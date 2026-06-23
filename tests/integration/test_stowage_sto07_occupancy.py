"""STO-07 — le gerbage réduit l'occupation plancher dans l'évaluation du plan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.stowage import StowageItem, StowagePlan
from app.models.vessel import Vessel
from app.services.stowage import evaluate_plan


async def _plan_with_item(db, *, is_stacked: bool) -> int:
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
    plan = StowagePlan(leg_id=1, status="draft")
    db.add(plan)
    await db.flush()
    # INF_AR_AR autorise le gerbage (stack_allowed=True dans le référentiel Phoenix).
    db.add(
        StowageItem(
            plan_id=plan.id,
            zone="INF_AR_AR",
            pallet_format="EPAL",
            pallet_count=10,
            stackable=True,
            is_stacked=is_stacked,
        )
    )
    await db.flush()
    return 1


@pytest.mark.asyncio
async def test_flat_item_consumes_full_floor(db):
    await _plan_with_item(db, is_stacked=False)
    ev = await evaluate_plan(db, 1)
    assert ev["zones"]["INF_AR_AR"]["used_epal"] == 10.0


@pytest.mark.asyncio
async def test_stacked_item_consumes_half_floor(db):
    await _plan_with_item(db, is_stacked=True)
    ev = await evaluate_plan(db, 1)
    # 10 palettes EPAL gerbées dans une zone gerbable → 5 emplacements plancher.
    assert ev["zones"]["INF_AR_AR"]["used_epal"] == 5.0
    # Le total agrégé reflète aussi le gain.
    assert ev["totals"]["used_epal"] == 5.0
