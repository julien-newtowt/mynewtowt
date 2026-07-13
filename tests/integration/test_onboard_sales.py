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
        db,
        vessel_id=vessel.id,
        currency="EUR",
        buyer_name="Marin X",
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


@pytest.mark.asyncio
async def test_reconcile_settles_paid_pending_sale(db, staff_user, monkeypatch):
    """Réconciliation à l'affichage : une vente en attente dont Stripe confirme
    le paiement est soldée, même sans webhook. Idempotent avec le webhook."""
    from types import SimpleNamespace

    from app.routers import onboard_sales_router as r

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "pending_payment"
    sale.stripe_checkout_session_id = "cs_test_ret"
    await db.flush()

    monkeypatch.setattr(r.stripe_svc, "is_configured", lambda: True)

    async def fake_retrieve(session_id):
        assert session_id == "cs_test_ret"
        return SimpleNamespace(payment_status="paid", payment_intent="pi_ret_1")

    monkeypatch.setattr(r.stripe_svc, "retrieve_session", fake_retrieve)

    await r._reconcile_pending_card_payment(db, sale, recorded_by_id=staff_user.id)
    assert sale.status == "paid"
    assert sale.payment_method == "card"
    assert sale.stripe_payment_intent_id == "pi_ret_1"
    assert await _count_vente_movements(db) == 1

    # Webhook tardif / ré-affichage : pas de second encaissement.
    await r._reconcile_pending_card_payment(db, sale, recorded_by_id=staff_user.id)
    assert await _count_vente_movements(db) == 1


@pytest.mark.asyncio
async def test_reconcile_noop_when_unpaid(db, staff_user, monkeypatch):
    """Session non payée → la vente reste en attente, aucun encaissement."""
    from types import SimpleNamespace

    from app.routers import onboard_sales_router as r

    _vessel, _product, sale = await _setup_sale(db, staff_user)
    sale.status = "pending_payment"
    sale.stripe_checkout_session_id = "cs_test_unpaid"
    await db.flush()

    monkeypatch.setattr(r.stripe_svc, "is_configured", lambda: True)

    async def fake_retrieve(session_id):
        return SimpleNamespace(payment_status="unpaid", payment_intent=None)

    monkeypatch.setattr(r.stripe_svc, "retrieve_session", fake_retrieve)

    await r._reconcile_pending_card_payment(db, sale, recorded_by_id=staff_user.id)
    assert sale.status == "pending_payment"
    assert await _count_vente_movements(db) == 0


@pytest.mark.asyncio
async def test_create_session_prefixes_sku(monkeypatch):
    """La référence produit (SKU) préfixe le libellé envoyé à Stripe ; une ligne
    sans produit du catalogue garde son libellé nu."""
    from types import SimpleNamespace

    from app.services import stripe_checkout as sc

    monkeypatch.setattr(sc.settings, "stripe_secret_key", "sk_test_x")
    captured: dict = {}

    def fake_sync(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test", url="https://checkout.stripe.com/x")

    monkeypatch.setattr(sc, "_create_session_sync", fake_sync)

    sale = SimpleNamespace(id=1, reference="VB-2026-0001", currency="EUR")
    line_known = SimpleNamespace(
        product_id=7, label="Café moulu 250 g", qty=Decimal("2"), line_total=Decimal("13.00")
    )
    line_free = SimpleNamespace(
        product_id=None, label="Service divers", qty=Decimal("1"), line_total=Decimal("5.00")
    )
    await sc.create_session(
        sale,
        [line_known, line_free],
        success_url="s",
        cancel_url="c",
        sku_by_product_id={7: "CAF-250"},
    )
    names = [li["price_data"]["product_data"]["name"] for li in captured["line_items"]]
    assert names[0] == "[CAF-250] Café moulu 250 g ×2"
    assert names[1] == "Service divers ×1"


def test_qr_svg_is_responsive():
    """Le QR n'a plus de dimension fixe (omitsize) → il épouse son conteneur."""
    from app.routers.onboard_sales_router import _qr_svg

    svg = _qr_svg("https://checkout.stripe.com/pay/cs_test_abc")
    assert "viewBox" in svg
    assert "width=" not in svg


@pytest.mark.asyncio
async def test_create_product_autogenerates_sku(db, staff_user):
    """Le SKU est attribué automatiquement (ART-XXXX), jamais saisi ; unique."""
    from app.routers.onboard_sales_router import create_product

    resp = await create_product(
        label="Café moulu 250 g",
        kind="bien",
        unit_price="6.50",
        currency="EUR",
        unit="pièce",
        tracks_stock="on",
        notes="",
        db=db,
        user=staff_user,
    )
    assert resp.status_code == 303
    prods = (await db.execute(select(OnboardProduct))).scalars().all()
    assert len(prods) == 1
    assert prods[0].sku == f"ART-{prods[0].id:04d}"
    assert not prods[0].sku.startswith("__pending")

    # 2e produit → SKU distinct (pas de collision, pas de saisie).
    await create_product(
        label="Thé vert",
        kind="bien",
        unit_price="4.00",
        currency="EUR",
        unit="pièce",
        tracks_stock="on",
        notes="",
        db=db,
        user=staff_user,
    )
    skus = [p.sku for p in (await db.execute(select(OnboardProduct))).scalars().all()]
    assert len(set(skus)) == 2
