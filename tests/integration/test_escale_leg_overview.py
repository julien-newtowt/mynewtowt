"""ESC-08 (tranche) — synthèse commerciale du leg sur la vue d'escale.

`commercial_overview` liste les commandes affectées au leg et les packing lists
liées (épinglées au leg OU rattachées à une commande du leg), en excluant ce qui
relève d'un autre leg.
"""

from __future__ import annotations

import pytest

from tests.integration.test_mrv_reprise import _setup_leg


@pytest.mark.asyncio
async def test_commercial_overview_lists_orders_and_packing_lists(db):
    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList
    from app.services.leg_overview import commercial_overview

    await _setup_leg(db)  # leg id=1
    db.add(Client(id=1, name="ACME", client_type="shipper"))
    await db.flush()
    db.add(Order(id=1, reference="CMD-1", client_id=1, leg_id=1, booked_palettes=10))
    db.add(Order(id=2, reference="CMD-OTHER", client_id=1, leg_id=None))  # hors leg
    await db.flush()
    db.add(PackingList(id=1, leg_id=1))  # épinglée au leg
    db.add(PackingList(id=2, order_id=1))  # via une commande du leg
    db.add(PackingList(id=3, order_id=2))  # commande hors leg → exclue
    await db.flush()

    ov = await commercial_overview(db, 1)

    assert len(ov["orders"]) == 1
    assert ov["orders"][0]["order"].reference == "CMD-1"
    assert ov["orders"][0]["client_name"] == "ACME"

    assert {pl.id for pl in ov["packing_lists"]} == {1, 2}


def test_escale_template_has_commercial_card():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "Commercial du leg" in src
    assert "leg_overview.orders" in src
    assert "leg_overview.packing_lists" in src
