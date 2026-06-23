"""Commercial P1 — reprise (COM-09 auto-PL + notification à la confirmation)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.commercial import Client, Order
from app.models.notification import Notification
from app.models.packing_list import PackingList


class _Req:
    headers: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")


async def _order(db, status="draft"):
    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    o = Order(reference="ORD-2026-0001", client_id=c.id, status=status, booked_palettes=20)
    db.add(o)
    await db.flush()
    return o


@pytest.mark.asyncio
async def test_confirm_creates_packing_list_and_notifies(db, staff_user):
    from app.routers.commercial_router import order_confirm

    o = await _order(db)
    resp = await order_confirm(o.id, _Req(), db=db, user=staff_user)
    assert resp.status_code == 303
    await db.refresh(o)
    assert o.status == "confirmed"
    # Packing list auto-créée.
    pls = (await db.execute(PackingList.__table__.select())).fetchall()
    assert len(pls) == 1 and pls[0].order_id == o.id
    # Notification opérations émise.
    notifs = (
        await db.execute(
            Notification.__table__.select().where(
                Notification.__table__.c.type == "new_packing_list"
            )
        )
    ).fetchall()
    assert len(notifs) == 1


@pytest.mark.asyncio
async def test_reconfirm_is_idempotent(db, staff_user):
    """Re-confirmer ne crée pas de 2e PL ni de 2e notification."""
    from app.routers.commercial_router import order_confirm

    o = await _order(db)
    await order_confirm(o.id, _Req(), db=db, user=staff_user)
    await order_confirm(o.id, _Req(), db=db, user=staff_user)
    assert len((await db.execute(PackingList.__table__.select())).fetchall()) == 1
    notifs = (
        await db.execute(
            Notification.__table__.select().where(
                Notification.__table__.c.type == "new_packing_list"
            )
        )
    ).fetchall()
    assert len(notifs) == 1


@pytest.mark.asyncio
async def test_ensure_for_order_get_or_create(db):
    from app.services.packing_list import ensure_for_order

    o = await _order(db)
    pl1, created1 = await ensure_for_order(db, o)
    pl2, created2 = await ensure_for_order(db, o)
    assert created1 is True and created2 is False
    assert pl1.id == pl2.id
