"""Reçu de vente à bord — le document remis à l'acheteur.

Le module encaissait sans rien remettre : le marin payait et repartait sans
preuve d'achat ni justificatif de note de frais. C'était le manque fonctionnel
le plus visible de l'audit du 2026-08-27.

Ce n'est **pas une facture** : les ventes à bord sont en franchise de taxe
(avitaillement) et n'ouvrent pas droit à déduction. Le document doit le dire.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.onboard_sales import OnboardProduct, OnboardSaleLine
from app.models.vessel import Vessel
from app.services import onboard_sales as svc
from app.services import pdf_generator


async def _paid_sale(db, staff_user, *, payment_method="cash"):
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
    )
    db.add(product)
    await db.flush()
    await svc.add_stock_entry(
        db, vessel_id=vessel.id, product=product, qty=Decimal("10"), reason="avitaillement"
    )
    sale = await svc.create_sale(db, vessel_id=vessel.id, currency="EUR", buyer_name="Marin X")
    await svc.add_line(db, sale, product=product, qty=Decimal("2"))
    await svc.settle_sale(db, sale, payment_method=payment_method, recorded_by_id=staff_user.id)
    lines = list(
        (await db.execute(select(OnboardSaleLine).where(OnboardSaleLine.sale_id == sale.id)))
        .scalars()
        .all()
    )
    return vessel, sale, lines


@pytest.mark.asyncio
async def test_the_receipt_renders_a_real_pdf(db, staff_user):
    vessel, sale, lines = await _paid_sale(db, staff_user)
    doc = pdf_generator.onboard_sale_receipt(sale, vessel, lines, payment_label="Espèces")

    assert doc.pdf.startswith(b"%PDF-"), "le PDF n'a pas été produit"
    assert len(doc.pdf) > 1000
    assert doc.filename == f"recu-{sale.reference}.pdf"
    assert doc.mime == "application/pdf"


@pytest.mark.asyncio
async def test_the_receipt_carries_what_the_buyer_needs(db, staff_user):
    vessel, sale, lines = await _paid_sale(db, staff_user)
    html = pdf_generator.onboard_sale_receipt(sale, vessel, lines, payment_label="Espèces").html

    assert sale.reference in html
    assert "Café moulu 250 g" in html
    assert "13.00" in html  # 2 × 6,50
    assert "EUR" in html
    assert "Marin X" in html
    assert vessel.name in html
    assert "Espèces" in html


@pytest.mark.asyncio
async def test_the_receipt_states_it_is_not_an_invoice(db, staff_user):
    """Sans cette mention, le reçu finirait présenté comme une facture."""
    vessel, sale, lines = await _paid_sale(db, staff_user)
    html = pdf_generator.onboard_sale_receipt(sale, vessel, lines, payment_label="Espèces").html
    assert "franchise" in html.lower()
    assert "n'est pas une facture" in html
    assert "TVA" in html


@pytest.mark.asyncio
async def test_a_refunded_sale_says_so_on_its_receipt(db, staff_user):
    """Un reçu de vente remboursée ne doit pas rester un justificatif d'achat."""
    vessel, sale, lines = await _paid_sale(db, staff_user)
    await svc.refund_sale(db, sale, refunded_by_id=staff_user.id)
    html = pdf_generator.onboard_sale_receipt(sale, vessel, lines, payment_label="Espèces").html
    assert "remboursée" in html.lower()
    assert "ne vaut plus justificatif" in html


@pytest.mark.asyncio
async def test_an_unsettled_sale_has_no_receipt(db, staff_user):
    """Rien n'a été encaissé : il n'y a rien à justifier."""
    from fastapi import HTTPException

    from app.routers import onboard_sales_router as r

    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    sale = await svc.create_sale(db, vessel_id=vessel.id, currency="EUR")
    with pytest.raises(HTTPException) as exc:
        await r.sale_receipt(sale.reference, db=db, user=staff_user)
    assert exc.value.status_code == 400
