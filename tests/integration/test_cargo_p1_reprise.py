"""Cargo P1 — reprise (CARGO-08 pré-remplissage batch + CARGO-13 champs goods riches)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.commercial import Client, Order
from app.models.packing_list import PackingList, PackingListAudit, PackingListBatch
from app.services.packing_list import (
    apply_batch_update,
    batch_prefill_from_order,
    coerce_batch_form,
    create_batch,
    ensure_for_order,
)


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


# ─────────────────────────────── CARGO-13 ───────────────────────────────


def test_compute_dimensions_properties():
    """surface / volume / densité dérivés des dimensions (formules V2)."""
    b = PackingListBatch(
        packing_list_id=1, length_cm=120, width_cm=80, height_cm=100, weight_kg=400
    )
    assert b.surface_m2 == 0.96  # 120*80/10000
    assert b.volume_m3 == 0.96  # 120*80*100/1e6
    assert b.density == round((400 / 1000) / 0.96, 3)  # t / m²


def test_compute_dimensions_none_when_missing():
    b = PackingListBatch(packing_list_id=1, length_cm=120)
    assert b.surface_m2 is None
    assert b.volume_m3 is None
    assert b.density is None


def test_rich_goods_fields_coerced():
    """cases_quantity / units_per_case → int ; cargo_value_usd → float."""
    vals = coerce_batch_form(
        {"cases_quantity": "12", "units_per_case": "6", "cargo_value_usd": "1500,50"}
    )
    assert vals["cases_quantity"] == 12
    assert vals["units_per_case"] == 6
    assert vals["cargo_value_usd"] == 1500.50


@pytest.mark.asyncio
async def test_rich_goods_fields_create_and_audited(db):
    o = await _order(db)
    pl = PackingList(order_id=o.id, status="draft")
    db.add(pl)
    await db.flush()
    b = await create_batch(
        db,
        pl=pl,
        vals={"cargo_value_usd": 999.0, "cases_quantity": 4},
        actor="staff",
        actor_name="agent",
    )
    assert b.cargo_value_usd == 999.0 and b.cases_quantity == 4

    # une édition trace le champ riche modifié
    changed = await apply_batch_update(
        db, batch=b, new_values={"units_per_case": 10}, actor="staff", actor_name="agent"
    )
    assert changed == 1
    assert b.units_per_case == 10
    audits = (
        (
            await db.execute(
                select(PackingListAudit).where(PackingListAudit.field == "units_per_case")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1 and audits[0].new_value == "10"
