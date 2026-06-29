"""COM-10 — statuts intermédiaires de commande pilotables (chargé / livré).

Cycle avant uniquement : confirmé → chargé → livré ; 409 si aucune transition
n'est possible (brouillon, livré, annulé).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.integration.conftest import FakeRequest


async def _client(db):
    from app.models.commercial import Client

    db.add(Client(id=1, name="ACME", client_type="shipper"))
    await db.flush()


@pytest.mark.asyncio
async def test_order_advance_cycle(db, staff_user):
    from app.models.commercial import Order
    from app.routers.commercial_router import order_advance

    await _client(db)
    db.add(Order(id=1, reference="CMD-1", client_id=1, status="confirmed"))
    await db.flush()

    await order_advance(1, FakeRequest(), db=db, user=staff_user)
    assert (await db.get(Order, 1)).status == "loaded"

    await order_advance(1, FakeRequest(), db=db, user=staff_user)
    assert (await db.get(Order, 1)).status == "delivered"

    # Livré : plus aucune transition → 409.
    with pytest.raises(HTTPException) as exc:
        await order_advance(1, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_order_advance_from_draft_rejected(db, staff_user):
    from app.models.commercial import Order
    from app.routers.commercial_router import order_advance

    await _client(db)
    db.add(Order(id=2, reference="CMD-2", client_id=1, status="draft"))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await order_advance(2, FakeRequest(), db=db, user=staff_user)
    assert exc.value.status_code == 409


def test_order_detail_has_advance_button():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/commercial/order_detail.html")[0]
    assert "/advance" in src
    assert "Marquer chargée" in src
