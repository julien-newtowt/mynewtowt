"""Stowage P1 — reprise (STO-05 politique de blocage capacité configurable, A3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.feature_flag import FeatureFlag
from app.models.leg import Leg
from app.models.port import Port
from app.models.stowage import StowageItem, StowagePlan, StowageZoneSpec
from app.models.vessel import Vessel

_ZONE = "INF_AR_AR"


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _setup(db, *, capacity=10, max_load_t=5.0):
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
    db.add(
        StowageZoneSpec(
            vessel_class="phoenix", zone=_ZONE, capacity_epal=capacity, max_load_t=max_load_t
        )
    )
    await db.flush()
    plan = StowagePlan(leg_id=1, status="draft")
    db.add(plan)
    await db.flush()
    return plan


@pytest.mark.asyncio
async def test_admission_blocks_over_capacity(db):
    from app.services.stowage import check_zone_admission

    await _setup(db, capacity=10)
    ok, reason = await check_zone_admission(
        db, 1, _ZONE, add_pallets=20, add_weight_kg=None, pallet_format="EPAL"
    )
    assert ok is False and "Capacité" in reason

    ok2, _ = await check_zone_admission(
        db, 1, _ZONE, add_pallets=5, add_weight_kg=None, pallet_format="EPAL"
    )
    assert ok2 is True


@pytest.mark.asyncio
async def test_admission_blocks_over_weight(db):
    from app.services.stowage import check_zone_admission

    await _setup(db, capacity=1000, max_load_t=5.0)
    ok, reason = await check_zone_admission(
        db, 1, _ZONE, add_pallets=1, add_weight_kg=8000, pallet_format="EPAL"
    )
    assert ok is False and "Charge" in reason


@pytest.mark.asyncio
async def test_add_item_blocked_when_flag_enabled(db, staff_user):
    from fastapi import HTTPException

    from app.routers.stowage_router import add_item

    plan = await _setup(db, capacity=10)
    db.add(FeatureFlag(key="stowage_block_overcapacity", enabled=True))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await add_item(
            plan.id,
            _Req(),
            zone=_ZONE,
            pallet_format="EPAL",
            pallet_count=50,
            weight_kg=None,
            is_dangerous=False,
            is_oversized=False,
            is_stacked=False,
            stackable=True,
            length_cm=None,
            width_cm=None,
            height_cm=None,
            hs_code=None,
            imdg_class=None,
            notes=None,
            order_id=None,
            batch_id=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_add_item_allowed_when_flag_disabled(db, staff_user):
    """Sans le flag, le dépassement n'est qu'un avertissement → ajout autorisé."""
    from app.routers.stowage_router import add_item

    plan = await _setup(db, capacity=10)
    resp = await add_item(
        plan.id,
        _Req(),
        zone=_ZONE,
        pallet_format="EPAL",
        pallet_count=50,
        weight_kg=None,
        is_dangerous=False,
        is_oversized=False,
        is_stacked=False,
        stackable=True,
        length_cm=None,
        width_cm=None,
        height_cm=None,
        hs_code=None,
        imdg_class=None,
        notes=None,
        order_id=None,
        batch_id=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    assert (await db.execute(StowageItem.__table__.select())).fetchone() is not None
