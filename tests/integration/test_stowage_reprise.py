"""Stowage P0 — reprise (STO-01 vue à bord, STO-02 réaffectation, STO-03 retrait)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.leg import Leg
from app.models.port import Port
from app.models.stowage import StowageItem, StowagePlan
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    state = SimpleNamespace(csrf_token="t", lang="fr")
    url = SimpleNamespace(path="/stowage/onboard/1", query="")
    query_params: dict[str, str] = {}
    scope: dict = {"type": "http"}


async def _setup_plan(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    db.add(Leg(id=1, leg_code="1CFRBR6", vessel_id=1, departure_port_id=1, arrival_port_id=2,
               etd_ref=base, eta_ref=base + timedelta(days=20), etd=base, eta=base + timedelta(days=20)))
    await db.flush()
    plan = StowagePlan(leg_id=1, status="draft")
    db.add(plan)
    await db.flush()
    return plan


# ─────────────────────────────── STO-02 ───────────────────────────────


@pytest.mark.asyncio
async def test_move_item_changes_zone(db, staff_user):
    from app.routers.stowage_router import move_item

    plan = await _setup_plan(db)
    item = StowageItem(plan_id=plan.id, zone="INF_AR_AR", pallet_format="EPAL", pallet_count=10)
    db.add(item)
    await db.flush()

    resp = await move_item(plan.id, item.id, _Req(), new_zone="MIL_AR_MIL",
                           db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(item)
    assert item.zone == "MIL_AR_MIL"


@pytest.mark.asyncio
async def test_move_item_rejects_invalid_zone(db, staff_user):
    from fastapi import HTTPException

    from app.routers.stowage_router import move_item

    plan = await _setup_plan(db)
    item = StowageItem(plan_id=plan.id, zone="INF_AR_AR", pallet_count=5)
    db.add(item)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await move_item(plan.id, item.id, _Req(), new_zone="NOWHERE", db=db, user=staff_user)
    assert exc.value.status_code == 400


# ─────────────────────────────── STO-03 ───────────────────────────────


@pytest.mark.asyncio
async def test_delete_item(db, staff_user):
    from app.routers.stowage_router import delete_item

    plan = await _setup_plan(db)
    item = StowageItem(plan_id=plan.id, zone="INF_AR_AR", pallet_count=5)
    db.add(item)
    await db.flush()
    iid = item.id
    resp = await delete_item(plan.id, iid, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(StowageItem, iid)) is None


@pytest.mark.asyncio
async def test_delete_item_wrong_plan_404(db, staff_user):
    from fastapi import HTTPException

    from app.routers.stowage_router import delete_item

    plan = await _setup_plan(db)
    item = StowageItem(plan_id=plan.id, zone="INF_AR_AR", pallet_count=5)
    db.add(item)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await delete_item(999, item.id, _Req(), db=db, user=staff_user)
    assert exc.value.status_code == 404


# ─────────────────────────────── STO-01 ───────────────────────────────


@pytest.mark.asyncio
async def test_onboard_view_renders(db, staff_user):
    from app.routers.stowage_router import stowage_onboard_view

    plan = await _setup_plan(db)
    db.add(StowageItem(plan_id=plan.id, zone="INF_AR_AR", pallet_format="EPAL", pallet_count=10))
    await db.flush()
    resp = await stowage_onboard_view(1, _Req(), db=db, user=staff_user)
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "INF_AR_AR" in body
    assert "1CFRBR6" in body
