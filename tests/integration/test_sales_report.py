"""Chiffre d'affaires de la vente à bord.

Le siège ne pouvait pas répondre à « combien la boutique a-t-elle vendu ce
mois-ci ? » autrement qu'en lisant l'export CSV de caisse : aucune agrégation
n'existait dans l'application (audit du 2026-08-27).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.onboard_sales import OnboardProduct
from app.models.vessel import Vessel
from app.services import onboard_sales as svc


async def _vessel(db, code="ANE", name="Anemos"):
    v = Vessel(code=code, name=name)
    db.add(v)
    await db.flush()
    return v


async def _product(db, sku="CAF-250", label="Café", price="6.50"):
    p = OnboardProduct(sku=sku, label=label, kind="bien", unit_price=Decimal(price), currency="EUR")
    db.add(p)
    await db.flush()
    return p


async def _sale(db, vessel, product, qty, staff_user, *, settle=True):
    sale = await svc.create_sale(db, vessel_id=vessel.id, currency="EUR")
    await svc.add_line(db, sale, product=product, qty=Decimal(qty))
    await svc.recompute_total(db, sale)
    if settle:
        await svc.settle_sale(db, sale, payment_method="cash", recorded_by_id=staff_user.id)
    return sale


@pytest.mark.asyncio
async def test_only_settled_sales_are_counted(db, staff_user):
    """Un brouillon n'est pas du chiffre d'affaires."""
    vessel = await _vessel(db)
    product = await _product(db)
    await _sale(db, vessel, product, "2", staff_user)  # réglée : 13,00
    await _sale(db, vessel, product, "5", staff_user, settle=False)  # brouillon

    summary = await svc.sales_summary(db)
    assert summary["totals"]["EUR"]["net"] == Decimal("13.00")
    assert summary["totals"]["EUR"]["count"] == 1


@pytest.mark.asyncio
async def test_a_refund_is_deducted_from_the_net(db, staff_user):
    vessel = await _vessel(db)
    product = await _product(db)
    gardee = await _sale(db, vessel, product, "2", staff_user)  # 13,00
    remboursee = await _sale(db, vessel, product, "4", staff_user)  # 26,00
    await svc.refund_sale(db, remboursee, refunded_by_id=staff_user.id)

    summary = await svc.sales_summary(db)
    eur = summary["totals"]["EUR"]
    assert eur["net"] == gardee.total == Decimal("13.00")
    assert eur["refunded"] == Decimal("26.00")
    assert eur["count"] == 1


@pytest.mark.asyncio
async def test_currencies_are_never_merged(db, staff_user):
    """Aucun taux de change n'existe : un total consolidé serait faux."""
    vessel = await _vessel(db)
    eur = await _product(db)
    vnd = OnboardProduct(
        sku="VND-1", label="Bière VN", kind="bien", unit_price=Decimal("50000"), currency="VND"
    )
    db.add(vnd)
    await db.flush()

    await _sale(db, vessel, eur, "2", staff_user)
    sale_vnd = await svc.create_sale(db, vessel_id=vessel.id, currency="VND")
    await svc.add_line(db, sale_vnd, product=vnd, qty=Decimal("2"))
    await svc.recompute_total(db, sale_vnd)
    await svc.settle_sale(db, sale_vnd, payment_method="cash")

    summary = await svc.sales_summary(db)
    assert set(summary["totals"]) == {"EUR", "VND"}
    assert summary["totals"]["VND"]["net"] == Decimal("100000.00")


@pytest.mark.asyncio
async def test_the_breakdown_by_vessel_and_product(db, staff_user):
    anemos = await _vessel(db)
    grain = await _vessel(db, code="GRA", name="Grain de Sail")
    cafe = await _product(db)
    biere = await _product(db, sku="BIE-33", label="Bière", price="3.00")

    await _sale(db, anemos, cafe, "2", staff_user)  # 13,00
    await _sale(db, grain, biere, "5", staff_user)  # 15,00
    await _sale(db, grain, cafe, "1", staff_user)  # 6,50

    summary = await svc.sales_summary(db)
    par_navire = {row["code"]: row["net"] for row in summary["by_vessel"]}
    assert par_navire == {"ANE": Decimal("13.00"), "GRA": Decimal("21.50")}

    # Classé par chiffre d'affaires décroissant.
    assert [row["label"] for row in summary["by_product"]] == ["Café", "Bière"]
    cafe_row = summary["by_product"][0]
    assert cafe_row["qty"] == Decimal("3.000")
    assert cafe_row["net"] == Decimal("19.50")


@pytest.mark.asyncio
async def test_the_report_can_be_scoped_to_one_vessel(db, staff_user):
    anemos = await _vessel(db)
    grain = await _vessel(db, code="GRA", name="Grain de Sail")
    cafe = await _product(db)
    await _sale(db, anemos, cafe, "2", staff_user)
    await _sale(db, grain, cafe, "10", staff_user)

    summary = await svc.sales_summary(db, vessel_id=anemos.id)
    assert summary["totals"]["EUR"]["net"] == Decimal("13.00")
    assert [row["code"] for row in summary["by_vessel"]] == ["ANE"]


@pytest.mark.asyncio
async def test_onboard_revenue_by_leg_is_exposed_but_not_injected(db, staff_user):
    """Finance saisit `revenue_eur` à la main : y écrire d'office l'écraserait."""
    vessel = await _vessel(db)
    cafe = await _product(db)
    sale = await _sale(db, vessel, cafe, "2", staff_user)
    assert sale.leg_id is None  # aucun voyage : le rattachement est optionnel
    assert await svc.onboard_revenue_by_leg(db, 999) == {}


@pytest.mark.asyncio
async def test_the_csv_export_carries_every_breakdown(db, staff_user):
    vessel = await _vessel(db)
    cafe = await _product(db)
    await _sale(db, vessel, cafe, "2", staff_user)

    csv_text = svc.export_summary_csv(await svc.sales_summary(db), period="2026-08")
    for section in ("Totaux par devise", "Par navire", "Par article", "Par voyage"):
        assert section in csv_text
    assert "EUR;13.00" in csv_text or "ANE;Anemos;EUR;13.00" in csv_text
