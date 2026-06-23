"""Commercial P0 — reprise (COM-01/02/03) : tests d'intégration.

Couvre l'édition/désactivation client (COM-03), les champs riches de la
commande (COM-02) et l'écran d'affectation commande→leg avec écriture de
``OrderAssignment``, suggestion et alerte « hors délai » (COM-01).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order, OrderAssignment
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _client(db, **kw):
    c = Client(
        name=kw.pop("name", "ACME Forwarding"),
        client_type=kw.pop("client_type", "freight_forwarder"),
        **kw,
    )
    db.add(c)
    await db.flush()
    return c


async def _ports_and_vessel(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="MQFDF", name="Fort-de-France", country="MQ"))
    db.add(Port(id=3, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()


def _leg(leg_id, dep, arr, etd, days=18):
    base = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=etd)
    return Leg(
        id=leg_id,
        leg_code=f"{leg_id}CFRMQ6",
        vessel_id=1,
        departure_port_id=dep,
        arrival_port_id=arr,
        etd_ref=base,
        eta_ref=base + timedelta(days=days),
        etd=base,
        eta=base + timedelta(days=days),
    )


# ─────────────────────────────── COM-03 ───────────────────────────────


@pytest.mark.asyncio
async def test_client_edit_updates_all_fields(db, staff_user):
    from app.routers.commercial_router import client_edit

    c = await _client(db, contact_name="Old", address=None, country="FR")
    resp = await client_edit(
        c.id,
        _Req(),
        name="ACME Logistics",
        client_type="shipper",
        contact_name="Jane Doe",
        contact_email="jane@acme.io",
        contact_phone="612345678",
        phone_dial_code="+33",
        address="12 quai de Southampton\n76600 Le Havre",
        country="fr",
        vat_number="FR12345678901",
        notes="VIP",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    await db.refresh(c)
    assert c.name == "ACME Logistics"
    assert c.client_type == "shipper"
    assert c.contact_email == "jane@acme.io"
    assert c.contact_phone == "+33 612345678"
    assert "Southampton" in c.address
    assert c.country == "FR"
    assert c.vat_number == "FR12345678901"


@pytest.mark.asyncio
async def test_client_toggle_active(db, staff_user):
    from app.routers.commercial_router import client_toggle_active

    c = await _client(db)
    assert c.is_active is True
    await client_toggle_active(c.id, _Req(), db=db, user=staff_user)
    await db.refresh(c)
    assert c.is_active is False
    await client_toggle_active(c.id, _Req(), db=db, user=staff_user)
    await db.refresh(c)
    assert c.is_active is True


# ─────────────────────────────── COM-02 ───────────────────────────────


@pytest.mark.asyncio
async def test_order_create_persists_rich_fields(db, staff_user):
    from app.routers.commercial_router import order_create

    c = await _client(db)
    resp = await order_create(
        _Req(),
        client_id=c.id,
        leg_id=None,
        booked_palettes=40,
        rate_per_palette_eur=120.0,
        cargo_description="Vin",
        shipper_name="Cave X",
        consignee_name="Import Y",
        palette_format="USPAL",
        weight_per_palette_kg="650.5",
        thc_included="on",
        booking_fee="50",
        documentation_fee="25",
        departure_locode="frleh",
        arrival_locode="mqfdf",
        delivery_date_start="2026-02-01",
        delivery_date_end="2026-02-20",
        rate_grid_id=None,
        rate_grid_line_id=None,
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    order = (await db.execute(Order.__table__.select())).fetchone()
    assert order.palette_format == "USPAL"
    assert float(order.weight_per_palette_kg) == 650.5
    assert order.thc_included is True
    assert order.departure_locode == "FRLEH"  # normalisé majuscules
    assert order.arrival_locode == "MQFDF"
    assert order.delivery_date_end == date(2026, 2, 20)
    assert float(order.total_eur) == 4800.0  # 120 × 40


@pytest.mark.asyncio
async def test_order_create_rejects_bad_locode(db, staff_user):
    from fastapi import HTTPException

    from app.routers.commercial_router import order_create

    c = await _client(db)
    with pytest.raises(HTTPException) as exc:
        await order_create(
            _Req(),
            client_id=c.id,
            leg_id=None,
            booked_palettes=10,
            rate_per_palette_eur=None,
            cargo_description=None,
            shipper_name=None,
            consignee_name=None,
            palette_format=None,
            weight_per_palette_kg=None,
            thc_included=None,
            booking_fee=None,
            documentation_fee=None,
            departure_locode="TOOLONG",
            arrival_locode=None,
            delivery_date_start=None,
            delivery_date_end=None,
            rate_grid_id=None,
            rate_grid_line_id=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_order_create_rejects_bad_delivery_date(db, staff_user):
    """Une date de livraison non vide mais invalide lève 400 (pas de drop silencieux)."""
    from fastapi import HTTPException

    from app.routers.commercial_router import order_create

    c = await _client(db)
    with pytest.raises(HTTPException) as exc:
        await order_create(
            _Req(),
            client_id=c.id,
            leg_id=None,
            booked_palettes=10,
            rate_per_palette_eur=None,
            cargo_description=None,
            shipper_name=None,
            consignee_name=None,
            palette_format=None,
            weight_per_palette_kg=None,
            thc_included=None,
            booking_fee=None,
            documentation_fee=None,
            departure_locode=None,
            arrival_locode=None,
            delivery_date_start=None,
            delivery_date_end="2026-13-45",
            rate_grid_id=None,
            rate_grid_line_id=None,
            db=db,
            user=staff_user,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_assign_duplicate_leg_blocked_by_unique_constraint(db, staff_user):
    """Garde-fou base : impossible d'écrire deux affectations (order, leg) identiques."""
    from sqlalchemy.exc import IntegrityError

    await _ports_and_vessel(db)
    db.add(_leg(1, 1, 2, etd=10))
    await db.flush()
    c = await _client(db)
    order = Order(reference="ORD-2026-0010", client_id=c.id, status="draft", booked_palettes=10)
    db.add(order)
    await db.flush()
    db.add(OrderAssignment(order_id=order.id, leg_id=1, palettes_count=10))
    await db.flush()
    db.add(OrderAssignment(order_id=order.id, leg_id=1, palettes_count=5))
    with pytest.raises(IntegrityError):
        await db.flush()


# ─────────────────────────────── COM-01 ───────────────────────────────


def test_leg_is_late_and_suggestion_pure():
    from app.services.commercial import leg_is_late_for_order, suggest_leg_for_order

    order = SimpleNamespace(delivery_date_end=date(2026, 2, 20))
    early = SimpleNamespace(id=1, eta=datetime(2026, 2, 10, tzinfo=UTC))
    late = SimpleNamespace(id=2, eta=datetime(2026, 3, 1, tzinfo=UTC))
    assert leg_is_late_for_order(early, order) is False
    assert leg_is_late_for_order(late, order) is True
    # La suggestion privilégie le 1er dans les délais, même si un retard le précède.
    assert suggest_leg_for_order([late, early], order).id == 1
    # Tous en retard → on suggère quand même le premier (le plus tôt).
    assert suggest_leg_for_order([late], order).id == 2
    assert suggest_leg_for_order([], order) is None


@pytest.mark.asyncio
async def test_compatible_legs_filtered_by_route(db):
    from app.services.commercial import compatible_legs_for_order

    await _ports_and_vessel(db)
    db.add(_leg(1, 1, 2, etd=10))  # FRLEH→MQFDF  ✓
    db.add(_leg(2, 1, 3, etd=12))  # FRLEH→BRSSO  ✗ (mauvais POD)
    db.add(_leg(3, 1, 2, etd=40))  # FRLEH→MQFDF  ✓
    await db.flush()
    # leg parti (atd renseigné) → exclu.
    departed = _leg(4, 1, 2, etd=5)
    departed.atd = datetime(2026, 1, 7, tzinfo=UTC)
    db.add(departed)
    await db.flush()

    order = SimpleNamespace(departure_locode="FRLEH", arrival_locode="MQFDF")
    legs = await compatible_legs_for_order(db, order)
    ids = [lg.id for lg in legs]
    assert ids == [1, 3]  # filtrés route + non partis + triés par ETD


@pytest.mark.asyncio
async def test_order_assign_single_leg_replaces(db, staff_user):
    """Affectation simple-leg : écrit OrderAssignment (palettes = commande),
    aligne ``order.leg_id``, ne touche pas au statut ; réaffecter remplace.
    """
    from app.routers.commercial_router import order_assign_submit

    await _ports_and_vessel(db)
    db.add(_leg(1, 1, 2, etd=10))
    db.add(_leg(3, 1, 2, etd=40))
    await db.flush()
    c = await _client(db)
    order = Order(
        reference="ORD-2026-0001",
        client_id=c.id,
        status="draft",
        booked_palettes=40,
        palette_format="USPAL",
    )
    db.add(order)
    await db.flush()

    resp = await order_assign_submit(
        order.id,
        _Req(),
        leg_id=1,
        notes="cale avant",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    rows = (await db.execute(OrderAssignment.__table__.select())).fetchall()
    assert len(rows) == 1
    assert rows[0].palettes_count == 40  # dérivé de la commande (anti-divergence)
    assert rows[0].pallet_format == "USPAL"
    await db.refresh(order)
    assert order.leg_id == 1
    assert order.status == "draft"  # l'affectation ne confirme pas la commande

    # Réaffecter sur un autre leg → REMPLACE (toujours une seule affectation).
    await order_assign_submit(order.id, _Req(), leg_id=3, notes=None, db=db, user=staff_user)
    rows = (await db.execute(OrderAssignment.__table__.select())).fetchall()
    assert len(rows) == 1
    assert rows[0].leg_id == 3
    await db.refresh(order)
    assert order.leg_id == 3


@pytest.mark.asyncio
async def test_order_assign_rejects_departed_leg(db, staff_user):
    from fastapi import HTTPException

    from app.routers.commercial_router import order_assign_submit

    await _ports_and_vessel(db)
    departed = _leg(1, 1, 2, etd=5)
    departed.atd = datetime(2026, 1, 7, tzinfo=UTC)
    db.add(departed)
    await db.flush()
    c = await _client(db)
    order = Order(reference="ORD-2026-0009", client_id=c.id, status="draft", booked_palettes=20)
    db.add(order)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await order_assign_submit(order.id, _Req(), leg_id=1, notes=None, db=db, user=staff_user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_order_assignment_delete_clears_leg(db, staff_user):
    from app.routers.commercial_router import order_assign_submit, order_assignment_delete

    await _ports_and_vessel(db)
    db.add(_leg(1, 1, 2, etd=10))
    await db.flush()
    c = await _client(db)
    order = Order(reference="ORD-2026-0002", client_id=c.id, status="draft", booked_palettes=80)
    db.add(order)
    await db.flush()

    await order_assign_submit(order.id, _Req(), leg_id=1, notes=None, db=db, user=staff_user)
    await db.refresh(order)
    assert order.leg_id == 1

    a1 = (await db.execute(OrderAssignment.__table__.select())).fetchone()
    resp = await order_assignment_delete(order.id, a1.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(order)
    assert order.leg_id is None  # plus d'affectation → leg détaché
    assert (await db.execute(OrderAssignment.__table__.select())).fetchone() is None
