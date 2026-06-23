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


# ─────────────────────────────── COM-07 ───────────────────────────────


class _RenderReq:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/commercial/api/rate-lookup")
    state = SimpleNamespace(notif_count=0, newtowt_agent_enabled=True, recent_notifications=[])


async def _ports(db):
    from app.models.port import Port

    db.add(Port(id=1, locode="FRLEH", name="Le Havre", country="FR"))
    db.add(Port(id=2, locode="MQFDF", name="Fort-de-France", country="MQ"))
    await db.flush()


@pytest.mark.asyncio
async def test_rate_lookup_returns_grid_price(db, staff_user):
    from app.routers.commercial_router import rate_lookup

    await _ports(db)
    resp = await rate_lookup(
        _RenderReq(),
        departure_locode="FRLEH",
        arrival_locode="MQFDF",
        booked_palettes=100,
        palette_format="EPAL",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 200
    html = resp.body.decode()
    assert "data-apply-rate=" in html  # bouton « Appliquer ce tarif »
    assert "EUR" in html  # tarif rendu via le filtre money


@pytest.mark.asyncio
async def test_rate_lookup_requires_route(db, staff_user):
    from app.routers.commercial_router import rate_lookup

    resp = await rate_lookup(
        _RenderReq(),
        departure_locode=None,
        arrival_locode=None,
        booked_palettes=10,
        db=db,
        user=staff_user,
    )
    assert "Renseignez POL et POD" in resp.body.decode()


@pytest.mark.asyncio
async def test_rate_lookup_requires_palettes(db, staff_user):
    from app.routers.commercial_router import rate_lookup

    await _ports(db)
    resp = await rate_lookup(
        _RenderReq(),
        departure_locode="FRLEH",
        arrival_locode="MQFDF",
        booked_palettes=0,
        db=db,
        user=staff_user,
    )
    assert "palettes" in resp.body.decode().lower()


def test_order_form_template_compiles():
    """Le formulaire de commande (HTMX rate-lookup) compile sans erreur."""
    from app.templating import templates

    assert templates.env.get_template("staff/commercial/order_form.html") is not None


# ─────────────────────────────── COM-08 ───────────────────────────────


async def _grid(db, ref="RG-2026-0001", is_default=False):
    from datetime import date

    from app.models.commercial import RateGrid

    g = RateGrid(reference=ref, status="active", valid_from=date(2026, 1, 1), is_default=is_default)
    db.add(g)
    await db.flush()
    return g


@pytest.mark.asyncio
async def test_grid_performance_kpis(db):
    from decimal import Decimal

    from app.models.commercial import RateOffer
    from app.services.commercial_dashboard import commercial_totals, grid_performance

    c = Client(name="ACME", client_type="shipper")
    db.add(c)
    await db.flush()
    g = await _grid(db)
    # 3 offres émises (sent/accepted/declined), 1 acceptée
    for ref, status in [("RO-1", "sent"), ("RO-2", "accepted"), ("RO-3", "declined")]:
        db.add(RateOffer(reference=ref, client_id=c.id, grid_id=g.id, title="T", status=status))
    # 2 commandes liées à la grille : 1 confirmée (CA réalisé), 1 draft (non réalisé)
    db.add(
        Order(
            reference="ORD-A",
            client_id=c.id,
            rate_grid_id=g.id,
            status="confirmed",
            total_eur=Decimal("1000.00"),
        )
    )
    db.add(
        Order(
            reference="ORD-B",
            client_id=c.id,
            rate_grid_id=g.id,
            status="draft",
            total_eur=Decimal("500.00"),
        )
    )
    await db.flush()

    perf = await grid_performance(db)
    assert len(perf) == 1
    row = perf[0]
    assert row["offers_emitted"] == 3
    assert row["offers_accepted"] == 1
    assert row["conversion_pct"] == round(100 / 3, 1)
    assert row["orders_count"] == 2
    assert row["ca_eur"] == Decimal("1000.00")  # seul le confirmé compte

    totals = await commercial_totals(db)
    assert totals["ca_total_eur"] == Decimal("1000.00")
    assert totals["offers_emitted"] == 3 and totals["offers_accepted"] == 1


@pytest.mark.asyncio
async def test_grid_performance_empty_when_no_activity(db):
    from app.services.commercial_dashboard import grid_performance

    await _grid(db)  # grille sans offre ni commande
    assert await grid_performance(db) == []


def test_commercial_index_template_compiles():
    """L'index commercial (tableau perf par grille COM-08) compile."""
    from app.templating import templates

    assert templates.env.get_template("staff/commercial/index.html") is not None
