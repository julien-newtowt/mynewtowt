"""STO-09 — arrimage avant cargo doc (fallback order→item placeholder)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.leg import Leg
from app.models.packing_list import PackingList, PackingListBatch
from app.models.port import Port
from app.models.stowage import StowageItem, StowagePlan
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _base(db):
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
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    return c


@pytest.mark.asyncio
async def test_order_without_pl_yields_placeholder(db):
    """Une commande sans packing list est arrimée via un placeholder (STO-09)."""
    from app.services.stowage import gather_suggestion_items

    c = await _base(db)
    db.add(
        Order(
            reference="ORD-1",
            client_id=c.id,
            leg_id=1,
            status="confirmed",
            booked_palettes=12,
            palette_format="EPAL",
            weight_per_palette_kg=Decimal("300"),
            cargo_description="Café vert",
        )
    )
    await db.flush()

    items = await gather_suggestion_items(db, 1)
    assert len(items) == 1
    it = items[0]
    assert it["batch_id"] is None  # signal du caractère provisoire
    assert it["pallet_count"] == 12
    assert it["pallet_format"] == "EPAL"
    assert it["weight_kg"] == 300.0 * 12
    assert it["description"] == "Café vert"


@pytest.mark.asyncio
async def test_placeholder_without_weight_or_format(db):
    """Réservation sans poids unitaire ni format → weight None, format EPAL par défaut."""
    from app.services.stowage import gather_suggestion_items

    c = await _base(db)
    db.add(
        Order(
            reference="ORD-2",
            client_id=c.id,
            leg_id=1,
            status="confirmed",
            booked_palettes=5,
        )
    )
    await db.flush()

    items = await gather_suggestion_items(db, 1)
    assert len(items) == 1
    it = items[0]
    assert it["weight_kg"] is None
    assert it["pallet_format"] == "EPAL"
    assert it["pallet_count"] == 5


@pytest.mark.asyncio
async def test_order_with_pl_uses_batches_not_placeholder(db):
    """Quand la PL existe, on prend les batches (détail figé), pas le placeholder."""
    from app.services.stowage import gather_suggestion_items

    c = await _base(db)
    db.add(
        Order(
            id=1,
            reference="ORD-1",
            client_id=c.id,
            leg_id=1,
            status="confirmed",
            booked_palettes=12,
        )
    )
    await db.flush()
    pl = PackingList(order_id=1, status="draft")
    db.add(pl)
    await db.flush()
    db.add_all(
        [
            PackingListBatch(packing_list_id=pl.id, pallet_format="EPAL", pallet_count=4),
            PackingListBatch(packing_list_id=pl.id, pallet_format="EPAL", pallet_count=3),
        ]
    )
    await db.flush()

    items = await gather_suggestion_items(db, 1)
    assert len(items) == 2
    assert all(it["batch_id"] is not None for it in items)
    assert all("placeholder" not in it for it in items)


@pytest.mark.asyncio
async def test_suggest_route_places_placeholder_items(db, staff_user):
    """Le bouton « Suggérer auto » arrime une commande sans PL (placeholder)."""
    from app.routers.stowage_router import suggest_plan

    c = await _base(db)
    db.add(
        Order(
            reference="ORD-1",
            client_id=c.id,
            leg_id=1,
            status="confirmed",
            booked_palettes=8,
            palette_format="EPAL",
        )
    )
    await db.flush()
    plan = StowagePlan(leg_id=1, status="draft")
    db.add(plan)
    await db.flush()

    resp = await suggest_plan(plan.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303

    placed = (
        await db.execute(StowageItem.__table__.select().where(StowageItem.plan_id == plan.id))
    ).fetchall()
    assert len(placed) == 1
    row = placed[0]._mapping
    assert row["batch_id"] is None
    assert row["pallet_count"] == 8
    assert row["zone"] not in (None, "OVERFLOW")
