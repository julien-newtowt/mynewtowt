"""Cargo P1 — reprise (CARGO-08 pré-remplissage du 1er batch à la création de la PL)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.commercial import Client, Order
from app.models.packing_list import PackingListBatch
from app.services.packing_list import batch_prefill_from_order, ensure_for_order


async def _order(db, **overrides):
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    vals = {
        "reference": "ORD-2026-0001",
        "client_id": c.id,
        "status": "draft",
        "booked_palettes": 24,
        "palette_format": "USPAL",
        "shipper_name": "Domaine du Vent",
        "shipper_address": "1 quai de Fécamp",
        "consignee_name": "NY Imports",
        "consignee_address": "Pier 17, New York",
        "notify_name": "Notify Co",
        "notify_address": "Brooklyn",
        "description_of_goods": "Vins de Loire",
    }
    vals.update(overrides)
    o = Order(**vals)
    db.add(o)
    await db.flush()
    return o


# ─────────────────────────────── CARGO-08 ───────────────────────────────


def test_batch_prefill_maps_order_fields():
    o = Order(
        reference="ORD-X",
        client_id=1,
        booked_palettes=12,
        palette_format="IBC",
        shipper_name="Shipper SA",
        shipper_address="addr S",
        consignee_name="Consignee SA",
        consignee_address="addr C",
        notify_name="Notify SA",
        notify_address="addr N",
        description_of_goods="Cognac",
    )
    vals = batch_prefill_from_order(o)
    assert vals["pallet_count"] == 12
    assert vals["pallet_format"] == "IBC"
    assert vals["shipper_name"] == "Shipper SA"
    assert vals["consignee_name"] == "Consignee SA"
    assert vals["notify_name"] == "Notify SA"
    assert vals["description_of_goods"] == "Cognac"


def test_batch_prefill_defaults_and_skips_none():
    """Palettes/format ont des défauts ; les champs None sont écartés."""
    o = Order(reference="ORD-Y", client_id=1, booked_palettes=0)
    vals = batch_prefill_from_order(o)
    assert vals["pallet_count"] == 1  # 0 → défaut 1
    assert vals["pallet_format"] == "EPAL"
    assert "shipper_name" not in vals  # None écarté


def test_batch_prefill_falls_back_to_cargo_description():
    o = Order(
        reference="ORD-Z",
        client_id=1,
        cargo_description="Marchandise diverse",
    )
    vals = batch_prefill_from_order(o)
    assert vals["description_of_goods"] == "Marchandise diverse"


@pytest.mark.asyncio
async def test_ensure_for_order_creates_prefilled_first_batch(db):
    o = await _order(db)
    pl, created = await ensure_for_order(db, o)
    assert created is True

    batches = (
        (
            await db.execute(
                select(PackingListBatch).where(PackingListBatch.packing_list_id == pl.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(batches) == 1
    b = batches[0]
    assert b.batch_number == 1
    assert b.pallet_count == 24
    assert b.pallet_format == "USPAL"
    assert b.shipper_name == "Domaine du Vent"
    assert b.consignee_name == "NY Imports"
    assert b.notify_name == "Notify Co"
    assert b.description_of_goods == "Vins de Loire"


@pytest.mark.asyncio
async def test_ensure_for_order_idempotent_no_extra_batch(db):
    """Re-confirmer ne re-crée ni PL ni batch."""
    o = await _order(db)
    await ensure_for_order(db, o)
    pl2, created2 = await ensure_for_order(db, o)
    assert created2 is False
    batches = (
        (
            await db.execute(
                select(PackingListBatch).where(PackingListBatch.packing_list_id == pl2.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(batches) == 1  # toujours un seul batch
