"""COM-11 — ventilation multi-legs du CA d'une commande (prorata palettes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.commercial import Client, Order, OrderAssignment
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.finance_rollup import rollup_for_leg


async def _base(db) -> Client:
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
    return c


async def _leg(db, leg_id: int) -> Leg:
    return await db.get(Leg, leg_id)


@pytest.mark.asyncio
async def test_even_split_bills_half_each(db):
    c = await _base(db)
    o = Order(
        reference="ORD-1",
        client_id=c.id,
        leg_id=1,
        status="confirmed",
        booked_palettes=80,
        total_eur=Decimal("9600.00"),
    )
    db.add(o)
    await db.flush()
    db.add_all(
        [
            OrderAssignment(order_id=o.id, leg_id=1, palettes_count=40),
            OrderAssignment(order_id=o.id, leg_id=2, palettes_count=40),
        ]
    )
    await db.flush()

    f1 = await rollup_for_leg(db, await _leg(db, 1))
    f2 = await rollup_for_leg(db, await _leg(db, 2))
    # 40/40 → 50/50 du CA sur chaque leg.
    assert f1.revenue_eur == Decimal("4800.00")
    assert f2.revenue_eur == Decimal("4800.00")


@pytest.mark.asyncio
async def test_uneven_split_prorata(db):
    c = await _base(db)
    o = Order(
        reference="ORD-2",
        client_id=c.id,
        leg_id=1,
        status="confirmed",
        booked_palettes=100,
        total_eur=Decimal("1000.00"),
    )
    db.add(o)
    await db.flush()
    db.add_all(
        [
            OrderAssignment(order_id=o.id, leg_id=1, palettes_count=75),
            OrderAssignment(order_id=o.id, leg_id=2, palettes_count=25),
        ]
    )
    await db.flush()

    f1 = await rollup_for_leg(db, await _leg(db, 1))
    f2 = await rollup_for_leg(db, await _leg(db, 2))
    assert f1.revenue_eur == Decimal("750.00")
    assert f2.revenue_eur == Decimal("250.00")


@pytest.mark.asyncio
async def test_single_leg_order_unchanged(db):
    """Sans OrderAssignment : pleine valeur sur Order.leg_id (parité V2)."""
    c = await _base(db)
    o = Order(
        reference="ORD-3",
        client_id=c.id,
        leg_id=1,
        status="confirmed",
        booked_palettes=50,
        total_eur=Decimal("5000.00"),
    )
    db.add(o)
    await db.flush()

    f1 = await rollup_for_leg(db, await _leg(db, 1))
    f2 = await rollup_for_leg(db, await _leg(db, 2))
    assert f1.revenue_eur == Decimal("5000.00")
    assert f2.revenue_eur == Decimal("0.00")


@pytest.mark.asyncio
async def test_draft_order_excluded_from_ventilation(db):
    """Une commande non « revenu » (draft) ne contribue à aucun leg."""
    c = await _base(db)
    o = Order(
        reference="ORD-4",
        client_id=c.id,
        leg_id=1,
        status="draft",
        booked_palettes=80,
        total_eur=Decimal("8000.00"),
    )
    db.add(o)
    await db.flush()
    db.add_all(
        [
            OrderAssignment(order_id=o.id, leg_id=1, palettes_count=40),
            OrderAssignment(order_id=o.id, leg_id=2, palettes_count=40),
        ]
    )
    await db.flush()

    f1 = await rollup_for_leg(db, await _leg(db, 1))
    f2 = await rollup_for_leg(db, await _leg(db, 2))
    assert f1.revenue_eur == Decimal("0.00")
    assert f2.revenue_eur == Decimal("0.00")
