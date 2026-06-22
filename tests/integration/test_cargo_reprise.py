"""Cargo P0 — reprise (CARGO-01..06) : tests d'intégration.

Couvre la reconnexion BL ↔ packing list, l'édition/suppression de batch avec
audit, l'Arrival Notice et le cloisonnement portail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.leg import Leg
from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PackingListBatch,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.services.packing_list import apply_batch_update, assign_bl_number


class _Req:
    """Requête minimale (form + url + headers + client) pour les coroutines."""

    def __init__(self, form: dict | None = None, path: str = "/p/x"):
        self._form = dict(form or {})
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.url = SimpleNamespace(path=path)

    async def form(self):
        return self._form


async def _setup_graph(db, *, with_leg: bool = True):
    """Crée client/ports/vessel/leg/order/PL et renvoie (pl, leg)."""
    db.add(Client(id=1, name="ACME", client_type="shipper"))
    leg = None
    if with_leg:
        db.add(Vessel(id=1, code="ANE", name="Anemos", imo_number="9876543"))
        db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
        db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
        await db.flush()  # matérialise vessel/ports avant le leg (FK)
        base = datetime(2026, 4, 1, tzinfo=UTC)
        leg = Leg(
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
        db.add(leg)
    await db.flush()
    db.add(Order(id=1, reference="OT-2026-0001", client_id=1, leg_id=1 if with_leg else None))
    await db.flush()
    pl = PackingList(order_id=1, token="tok_cargo_reprise_0001", status="draft")
    db.add(pl)
    await db.flush()
    return pl, leg


@pytest.mark.asyncio
async def test_apply_batch_update_audits_only_changed_fields(db):
    pl, _ = await _setup_graph(db, with_leg=False)
    b = PackingListBatch(packing_list_id=pl.id, batch_number=1, pallet_format="EPAL", pallet_count=2)
    db.add(b)
    await db.flush()

    changed = await apply_batch_update(
        db,
        batch=b,
        new_values={
            "consignee_name": "Buyer SA",
            "consignee_city": "Santos",
            "type_of_goods": "Wine",
            "pallet_count": 2,  # inchangé → pas d'audit
        },
        actor="staff",
        actor_name="Tester",
    )
    assert changed == 3  # consignee_name + consignee_city + type_of_goods
    assert b.consignee_name == "Buyer SA"
    audits = (
        (await db.execute(PackingListAudit.__table__.select())).fetchall()
    )
    fields = {a.field for a in audits}
    assert {"consignee_name", "consignee_city", "type_of_goods"} <= fields
    assert "pallet_count" not in fields


@pytest.mark.asyncio
async def test_assign_bl_number_idempotent_and_per_leg_increment(db):
    pl, leg = await _setup_graph(db, with_leg=True)
    b1 = PackingListBatch(packing_list_id=pl.id, batch_number=1)
    b2 = PackingListBatch(packing_list_id=pl.id, batch_number=2)
    db.add_all([b1, b2])
    await db.flush()

    n1 = await assign_bl_number(db, pl, b1, leg)
    assert n1 == "TUAW_1CFRBR6_001"
    # Idempotent : second appel = même numéro.
    assert await assign_bl_number(db, pl, b1, leg) == "TUAW_1CFRBR6_001"
    # Anti-doublon par leg : le batch suivant incrémente.
    n2 = await assign_bl_number(db, pl, b2, leg)
    assert n2 == "TUAW_1CFRBR6_002"
    assert b1.bl_issued_at is not None


@pytest.mark.asyncio
async def test_edit_batch_route_persists_addresses(db, staff_user):
    from app.routers.cargo_packing_router import edit_batch

    pl, _ = await _setup_graph(db, with_leg=False)
    b = PackingListBatch(packing_list_id=pl.id, batch_number=1)
    db.add(b)
    await db.flush()

    req = _Req(
        form={
            "shipper_name": "ACME Export",
            "consignee_name": "Buyer SA",
            "consignee_country": "BR",
            "type_of_goods": "Vin de Bordeaux",
            "weight_kg": "1200,5",
            "hazardous": "on",
        }
    )
    resp = await edit_batch(pl.id, b.id, req, db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(b)
    assert b.shipper_name == "ACME Export"
    assert b.consignee_country == "BR"
    assert b.weight_kg == 1200.5
    assert b.hazardous is True


@pytest.mark.asyncio
async def test_delete_batch_route_removes_and_audits(db, staff_user):
    from app.routers.cargo_packing_router import delete_batch

    pl, _ = await _setup_graph(db, with_leg=False)
    b = PackingListBatch(packing_list_id=pl.id, batch_number=1, pallet_count=3)
    db.add(b)
    await db.flush()
    bid = b.id

    resp = await delete_batch(pl.id, bid, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    assert (await db.get(PackingListBatch, bid)) is None
    audits = (await db.execute(PackingListAudit.__table__.select())).fetchall()
    assert any(a.field == "_delete_batch" for a in audits)


@pytest.mark.asyncio
async def test_edit_batch_locked_pl_rejected(db, staff_user):
    from fastapi import HTTPException

    from app.routers.cargo_packing_router import edit_batch

    pl, _ = await _setup_graph(db, with_leg=False)
    b = PackingListBatch(packing_list_id=pl.id, batch_number=1)
    db.add(b)
    pl.status = "locked"
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await edit_batch(pl.id, b.id, _Req(form={"shipper_name": "X"}), db=db, user=staff_user)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_portal_batch_isolation_404(db):
    """Un token ne peut pas éditer un batch d'une autre packing list."""
    from fastapi import HTTPException

    from app.routers.cargo_portal_router import portal_packing_edit

    pl, _ = await _setup_graph(db, with_leg=False)
    # Deuxième PL (autre commande) avec son propre batch.
    db.add(Order(id=2, reference="OT-2026-0002", client_id=1))
    await db.flush()
    pl2 = PackingList(order_id=2, token="tok_other_pl_0002", status="draft")
    db.add(pl2)
    await db.flush()
    foreign = PackingListBatch(packing_list_id=pl2.id, batch_number=1)
    db.add(foreign)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await portal_packing_edit(pl.token, foreign.id, _Req(path="/p/x/packing"), db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_bill_of_lading_pdf_renders_from_batch(db, staff_user):
    from app.routers.cargo_packing_router import batch_bill_of_lading

    pl, _ = await _setup_graph(db, with_leg=True)
    b = PackingListBatch(
        packing_list_id=pl.id,
        batch_number=1,
        pallet_format="EPAL",
        pallet_count=4,
        weight_kg=2000,
        shipper_name="ACME Export",
        consignee_name="Buyer SA",
        consignee_city="Santos",
        type_of_goods="Wine",
    )
    db.add(b)
    await db.flush()

    resp = await batch_bill_of_lading(pl.id, b.id, db=db, user=staff_user)
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500  # vrai PDF généré
    await db.refresh(b)
    assert b.bl_number == "TUAW_1CFRBR6_001"


@pytest.mark.asyncio
async def test_arrival_notice_pdf_renders(db, staff_user):
    from app.routers.cargo_packing_router import packing_list_arrival_notice

    pl, _ = await _setup_graph(db, with_leg=True)
    db.add(
        PackingListBatch(
            packing_list_id=pl.id,
            batch_number=1,
            pallet_count=4,
            weight_kg=2000,
            consignee_name="Buyer SA",
            type_of_goods="Wine",
        )
    )
    await db.flush()
    resp = await packing_list_arrival_notice(pl.id, db=db, user=staff_user)
    assert resp.media_type == "application/pdf"
    assert len(resp.body) > 500
