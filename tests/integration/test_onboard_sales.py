"""Vente à bord — règlement (espèces & webhook carte), idempotence, stock,
registre. Base SQLite in-memory (fixtures ``db`` / ``staff_user``)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.onboard_cashbox import CashboxMovement
from app.models.onboard_sales import OnboardProduct
from app.models.vessel import Vessel
from app.services import onboard_sales as svc


async def _setup_sale(db, staff_user, *, stock=Decimal("10"), qty=Decimal("2")):
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    product = OnboardProduct(
        sku="CAF-250",
        label="Café moulu 250 g",
        kind="bien",
        unit_price=Decimal("6.50"),
        currency="EUR",
        unit="pièce",
        tracks_stock=True,
    )
    db.add(product)
    await db.flush()
    await svc.add_stock_entry(
        db,
        vessel_id=vessel.id,
        product=product,
        qty=stock,
        reason="avitaillement",
        recorded_by_id=staff_user.id,
    )
    sale = await svc.create_sale(
        db, vessel_id=vessel.id, currency="EUR", buyer_name="Marin X",
        recorded_by_id=staff_user.id,
    )
    await svc.add_line(db, sale, product=product, qty=qty)
    return vessel, product, sale


async def _count_vente_movements(db) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(CashboxMovement)
        .where(CashboxMovement.category == "vente_a_bord")
    )


@pytest.mark.asyncio
async def test_reference_format(db, staff_user):
    vessel, _product, sale = await _setup_sale(db, staff_user)
    assert sale.reference.startswith("VB-")
    assert sale.reference.endswith("-0001")


@pytest.mark.asyncio
async def test_settle_cash_posts_cashbox_and_decrements_stock(db, staff_user):
    vessel, product, sale = await _setup_sale(db, staff_user)
    assert sale.total == Decimal("13.00")  # 6.50 × 2

    settled = await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    assert settled is True
    assert sale.status == "paid"
    assert sale.payment_method == "cash"
    assert sale.cashbox_movement_id is not None

    mov = await db.get(CashboxMovement, sale.cashbox_movement_id)
    assert mov.category == "vente_a_bord"
    assert mov.currency == "EUR"
    assert mov.amount == Decimal("13.00")  # encaissement positif

    # Stock : 10 − 2 = 8
    assert await svc.stock_on_hand(db, vessel.id, product.id) == Decimal("8")


@pytest.mark.asyncio
async def test_settle_is_idempotent(db, staff_user):
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    assert await svc.settle_sale(db, sale, payment_method="cash") is True
    # Rejeu : no-op, aucun second mouvement de caisse.
    assert await svc.settle_sale(db, sale, payment_method="cash") is False
    assert await _count_vente_movements(db) == 1


@pytest.mark.asyncio
async def test_register_lists_entry_and_exit(db, staff_user):
    vessel, _product, sale = await _setup_sale(db, staff_user)
    await svc.settle_sale(db, sale, payment_method="cash")
    rows = await svc.register_rows(db, vessel.id)
    # 1 entrée (avitaillement) + 1 sortie (vente)
    assert len(rows) == 2
    reasons = {r["reason"] for r in rows}
    assert reasons == {"avitaillement", "vente"}
    exit_row = next(r for r in rows if r["reason"] == "vente")
    assert exit_row["qty_out"] == Decimal("2")
    assert exit_row["sale_reference"] == sale.reference
    assert exit_row["regime"] == "franchise"


@pytest.mark.asyncio
async def test_cancel_only_when_unsettled(db, staff_user):
    _vessel, _product, sale = await _setup_sale(db, staff_user)
    await svc.settle_sale(db, sale, payment_method="cash")
    with pytest.raises(svc.OnboardSalesError):
        await svc.cancel_sale(db, sale)


@pytest.mark.asyncio
async def test_webhook_settle_idempotent(db, staff_user):
    # Import tardif : le routeur importe segno + stripe (présents en CI).
    from app.routers.onboard_sales_router import _settle_from_session

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    session_obj = {
        "id": "cs_test_123",
        "payment_status": "paid",
        "payment_intent": "pi_test_123",
        "metadata": {"sale_id": str(sale.id), "reference": sale.reference},
    }
    await _settle_from_session(db, session_obj)
    assert sale.status == "paid"
    assert sale.payment_method == "card"
    assert sale.stripe_payment_intent_id == "pi_test_123"

    # Rejeu du même event (Stripe redélivre) : aucun doublon.
    await _settle_from_session(db, session_obj)
    assert await _count_vente_movements(db) == 1
