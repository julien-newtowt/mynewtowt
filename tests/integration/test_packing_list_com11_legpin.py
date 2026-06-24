"""COM-11 — PL/BL épinglées au leg d'origine (stables après réaffectation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.commercial import Client, Order
from app.models.leg import Leg
from app.models.packing_list import PackingList
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.packing_list import ensure_for_order, resolve_pl_context


async def _order_on_leg(db, leg_id: int) -> Order:
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
        leg_id=leg_id,
        status="confirmed",
        booked_palettes=10,
        shipper_name="ACME",
    )
    db.add(o)
    await db.flush()
    return o


@pytest.mark.asyncio
async def test_ensure_for_order_pins_leg(db):
    o = await _order_on_leg(db, 1)
    pl, created = await ensure_for_order(db, o)
    assert created is True
    assert pl.leg_id == 1


@pytest.mark.asyncio
async def test_resolution_stable_after_reassignment(db):
    """Le leg principal de la commande bascule ; la PL reste sur son leg d'origine."""
    o = await _order_on_leg(db, 1)
    pl, _ = await ensure_for_order(db, o)
    # Réaffectation partielle : le leg principal de la commande change.
    o.leg_id = 2
    await db.flush()

    _order, _booking, leg, *_ = await resolve_pl_context(db, pl)
    assert leg is not None and leg.id == 1  # épinglé, pas suivi du basculement


@pytest.mark.asyncio
async def test_legacy_pl_falls_back_to_order_leg(db):
    """PL héritée (leg_id NULL) : repli dynamique sur order.leg_id."""
    o = await _order_on_leg(db, 2)
    pl = PackingList(order_id=o.id, leg_id=None, status="draft")
    db.add(pl)
    await db.flush()

    _order, _booking, leg, *_ = await resolve_pl_context(db, pl)
    assert leg is not None and leg.id == 2
