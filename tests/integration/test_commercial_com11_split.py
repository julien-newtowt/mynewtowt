"""COM-11 — ventilation multi-legs : route de découpage + réconciliation capacité."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.commercial import Client, Order, OrderAssignment
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.routers.commercial_router import order_split_submit


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _order(db, *, booked=80) -> Order:
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="MQFDF", name="Fort-de-France", country="MQ"))
    await db.flush()
    base = datetime(2026, 3, 1, tzinfo=UTC)
    for i in (1, 2):
        db.add(
            Leg(
                id=i,
                leg_code=f"{i}CFRMQ6",
                vessel_id=1,
                departure_port_id=1,
                arrival_port_id=2,
                etd_ref=base + timedelta(days=i),
                eta_ref=base + timedelta(days=i + 15),
                etd=base + timedelta(days=i),
                eta=base + timedelta(days=i + 15),
            )
        )
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    o = Order(
        reference="ORD-1",
        client_id=c.id,
        status="confirmed",
        booked_palettes=booked,
        palette_format="EPAL",
        total_eur=Decimal("9600.00"),
    )
    db.add(o)
    await db.flush()
    return o


@pytest.mark.asyncio
async def test_split_creates_assignments_and_sets_primary_leg(db, staff_user):
    o = await _order(db, booked=80)
    resp = await order_split_submit(
        o.id, _Req(), leg_ids=[1, 2], palettes=[40, 40], notes=None, db=db, user=staff_user
    )
    assert resp.status_code == 303
    assigns = (
        (await db.execute(select(OrderAssignment).where(OrderAssignment.order_id == o.id)))
        .scalars()
        .all()
    )
    assert {(a.leg_id, a.palettes_count) for a in assigns} == {(1, 40), (2, 40)}
    await db.refresh(o)
    assert o.leg_id == 1  # leg principal = première ligne


@pytest.mark.asyncio
async def test_split_rejects_sum_mismatch(db, staff_user):
    o = await _order(db, booked=80)
    with pytest.raises(HTTPException) as ei:
        await order_split_submit(
            o.id, _Req(), leg_ids=[1, 2], palettes=[40, 30], notes=None, db=db, user=staff_user
        )
    assert ei.value.status_code == 400
    assert "réservées" in ei.value.detail


@pytest.mark.asyncio
async def test_split_rejects_duplicate_leg(db, staff_user):
    o = await _order(db, booked=80)
    with pytest.raises(HTTPException) as ei:
        await order_split_submit(
            o.id, _Req(), leg_ids=[1, 1], palettes=[40, 40], notes=None, db=db, user=staff_user
        )
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_split_rejects_departed_leg(db, staff_user):
    o = await _order(db, booked=80)
    leg2 = await db.get(Leg, 2)
    leg2.atd = datetime(2026, 3, 2, tzinfo=UTC)
    await db.flush()
    with pytest.raises(HTTPException) as ei:
        await order_split_submit(
            o.id, _Req(), leg_ids=[1, 2], palettes=[40, 40], notes=None, db=db, user=staff_user
        )
    assert ei.value.status_code == 400
    assert "parti" in ei.value.detail


@pytest.mark.asyncio
async def test_split_rejects_zero_part(db, staff_user):
    o = await _order(db, booked=80)
    with pytest.raises(HTTPException) as ei:
        await order_split_submit(
            o.id, _Req(), leg_ids=[1, 2], palettes=[80, 0], notes=None, db=db, user=staff_user
        )
    assert ei.value.status_code == 400
