"""Remise, ligne hors catalogue, seuil d'alerte, rendu de monnaie.

Quatre manques P1 de l'audit du 2026-08-27. Chacun avait un contournement
observé, et chaque contournement dégradait une donnée : créer un faux produit
pour offrir un article, découvrir une rupture au moment de vendre, ou un écart
de comptage inexplicable faute de trace du rendu.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.onboard_sales import OnboardProduct, OnboardSaleLine
from app.models.vessel import Vessel
from app.services import onboard_sales as svc


async def _setup(db, *, price="10.00", stock="20", min_alert=None):
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    product = OnboardProduct(
        sku="CAF-250",
        label="Café",
        kind="bien",
        unit_price=Decimal(price),
        currency="EUR",
        min_stock_alert=Decimal(min_alert) if min_alert is not None else None,
    )
    db.add(product)
    await db.flush()
    await svc.add_stock_entry(
        db, vessel_id=vessel.id, product=product, qty=Decimal(stock), reason="avitaillement"
    )
    sale = await svc.create_sale(db, vessel_id=vessel.id, currency="EUR")
    return vessel, product, sale


# ── Remise ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_discount_is_derived_never_entered(db):
    """Le total de ligne reste dérivé de prix × quantité × remise."""
    _vessel, product, sale = await _setup(db)
    line = await svc.add_line(
        db, sale, product=product, qty=Decimal("2"), discount_pct=Decimal("25")
    )
    assert line.unit_price == Decimal("10.00")  # le prix catalogue est conservé
    assert line.discount_pct == Decimal("25")
    assert line.line_total == Decimal("15.00")  # 2 × 10 × 0,75
    await svc.recompute_total(db, sale)
    assert sale.total == Decimal("15.00")


@pytest.mark.asyncio
async def test_a_full_discount_is_a_free_item(db):
    """100 % = gratuité — article offert à l'équipage, geste commercial."""
    _vessel, product, sale = await _setup(db)
    line = await svc.add_line(
        db, sale, product=product, qty=Decimal("1"), discount_pct=Decimal("100")
    )
    assert line.line_total == Decimal("0.00")


@pytest.mark.asyncio
async def test_an_out_of_range_discount_is_refused(db):
    _vessel, product, sale = await _setup(db)
    for bad in (Decimal("-1"), Decimal("101"), Decimal("nan")):
        with pytest.raises(svc.OnboardSalesError):
            await svc.add_line(db, sale, product=product, qty=Decimal("1"), discount_pct=bad)


@pytest.mark.asyncio
async def test_a_discounted_sale_still_settles_and_moves_stock(db, staff_user):
    vessel, product, sale = await _setup(db)
    await svc.add_line(db, sale, product=product, qty=Decimal("2"), discount_pct=Decimal("50"))
    await svc.recompute_total(db, sale)
    await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    assert sale.total == Decimal("10.00")
    # La remise ne change pas la quantité sortie du stock.
    assert await svc.stock_on_hand(db, vessel_id=vessel.id, product_id=product.id) == Decimal("18")


# ── Ligne hors catalogue ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_free_line_needs_no_catalogue_entry(db):
    """Vendre un article non référencé imposait de créer un faux produit."""
    _vessel, _product, sale = await _setup(db)
    line = await svc.add_free_line(
        db, sale, label="Réparation ciré", unit_price=Decimal("12.50"), qty=Decimal("1")
    )
    assert line.product_id is None
    assert line.label == "Réparation ciré"
    assert line.line_total == Decimal("12.50")
    await db.refresh(sale)
    assert sale.total == Decimal("12.50")


@pytest.mark.asyncio
async def test_a_free_line_writes_no_stock_movement(db, staff_user):
    """Cohérent : sans produit, l'article n'est pas suivi en stock."""
    from app.models.onboard_sales import OnboardStockMovement

    vessel, _product, sale = await _setup(db)
    await svc.add_free_line(
        db, sale, label="Service divers", unit_price=Decimal("5.00"), qty=Decimal("1")
    )
    await svc.recompute_total(db, sale)
    await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    ventes = (
        (
            await db.execute(
                select(OnboardStockMovement).where(OnboardStockMovement.reason == "vente")
            )
        )
        .scalars()
        .all()
    )
    assert ventes == []


@pytest.mark.asyncio
async def test_several_free_lines_coexist(db):
    """La contrainte d'unicité porte sur (vente, produit) : NULL n'y entre pas."""
    _vessel, _product, sale = await _setup(db)
    await svc.add_free_line(db, sale, label="A", unit_price=Decimal("1.00"), qty=Decimal("1"))
    await svc.add_free_line(db, sale, label="B", unit_price=Decimal("2.00"), qty=Decimal("1"))
    lines = (
        (await db.execute(select(OnboardSaleLine).where(OnboardSaleLine.sale_id == sale.id)))
        .scalars()
        .all()
    )
    assert len(lines) == 2
    await db.refresh(sale)
    assert sale.total == Decimal("3.00")


@pytest.mark.asyncio
async def test_a_free_line_is_refused_without_a_label_or_a_price(db):
    _vessel, _product, sale = await _setup(db)
    with pytest.raises(svc.OnboardSalesError):
        await svc.add_free_line(db, sale, label="  ", unit_price=Decimal("1"), qty=Decimal("1"))
    with pytest.raises(svc.OnboardSalesError):
        await svc.add_free_line(db, sale, label="X", unit_price=Decimal("-1"), qty=Decimal("1"))


# ── Seuil d'alerte de stock ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_item_below_its_threshold_is_flagged(db):
    """Une rupture ne se découvrait qu'au moment de vendre."""
    vessel, product, _sale = await _setup(db, stock="3", min_alert="5")
    rows = await svc.current_inventory(db, vessel.id)
    assert rows[0]["low"] is True
    assert rows[0]["threshold"] == Decimal("5.000")
    assert [r["product"].sku for r in await svc.low_stock(db, vessel.id)] == ["CAF-250"]


@pytest.mark.asyncio
async def test_an_item_without_a_threshold_is_never_flagged(db):
    vessel, _product, _sale = await _setup(db, stock="1")
    rows = await svc.current_inventory(db, vessel.id)
    assert rows[0]["threshold"] is None
    assert rows[0]["low"] is False
    assert await svc.low_stock(db, vessel.id) == []


@pytest.mark.asyncio
async def test_a_negative_stock_is_always_reported(db):
    """Même sans seuil : un stock négatif est un écart d'inventaire."""
    vessel, product, _sale = await _setup(db, stock="1")
    await svc.add_stock_entry(
        db, vessel_id=vessel.id, product=product, qty=Decimal("-4"), reason="ajustement"
    )
    rows = await svc.current_inventory(db, vessel.id)
    assert rows[0]["negative"] is True
    assert len(await svc.low_stock(db, vessel.id)) == 1


# ── Rendu de monnaie ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_change_is_computed_and_traced(db, staff_user):
    """Sans cette trace, un écart de rendu restait inexplicable au comptage."""
    _vessel, product, sale = await _setup(db)
    await svc.add_line(db, sale, product=product, qty=Decimal("2"))  # 20,00
    await svc.recompute_total(db, sale)
    await svc.settle_sale(
        db,
        sale,
        payment_method="cash",
        recorded_by_id=staff_user.id,
        cash_received=Decimal("50.00"),
    )
    assert sale.cash_received == Decimal("50.00")
    assert sale.change_due == Decimal("30.00")


@pytest.mark.asyncio
async def test_the_cashbox_is_credited_with_the_sale_not_the_cash_handed_over(db, staff_user):
    """Le point qui compte : la caisse reçoit le montant de la vente."""
    from app.models.onboard_cashbox import CashboxMovement

    _vessel, product, sale = await _setup(db)
    await svc.add_line(db, sale, product=product, qty=Decimal("2"))
    await svc.recompute_total(db, sale)
    await svc.settle_sale(
        db,
        sale,
        payment_method="cash",
        recorded_by_id=staff_user.id,
        cash_received=Decimal("50.00"),
    )
    mov = await db.get(CashboxMovement, sale.cashbox_movement_id)
    assert mov.amount == Decimal("20.00")


@pytest.mark.asyncio
async def test_cash_received_below_the_total_is_refused(db, staff_user):
    _vessel, product, sale = await _setup(db)
    await svc.add_line(db, sale, product=product, qty=Decimal("2"))
    await svc.recompute_total(db, sale)
    with pytest.raises(svc.OnboardSalesError):
        await svc.settle_sale(
            db,
            sale,
            payment_method="cash",
            recorded_by_id=staff_user.id,
            cash_received=Decimal("5.00"),
        )


@pytest.mark.asyncio
async def test_change_is_none_when_nothing_was_entered(db, staff_user):
    """Le champ est facultatif : ne rien saisir ne casse rien."""
    _vessel, product, sale = await _setup(db)
    await svc.add_line(db, sale, product=product, qty=Decimal("1"))
    await svc.recompute_total(db, sale)
    await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    assert sale.cash_received is None
    assert sale.change_due is None
