"""Tests purs (sans DB) de la vente à bord : conversion de devise Stripe,
export CSV du registre douanier, et cohérence du vocabulaire."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.onboard_sales import (
    PAYMENT_METHOD_LABELS,
    PAYMENT_METHODS,
    REGIME_FRANCHISE,
    SALE_STATUS_LABELS,
    SALE_STATUSES,
    STOCK_REASON_LABELS,
    STOCK_REASONS,
)
from app.services.onboard_sales import export_csv
from app.services.stripe_checkout import ZERO_DECIMAL_CURRENCIES, amount_to_minor


def test_amount_to_minor_two_decimal():
    assert amount_to_minor(Decimal("12.50"), "EUR") == 1250
    assert amount_to_minor(Decimal("10.00"), "USD") == 1000
    assert amount_to_minor(Decimal("0.10"), "EUR") == 10


def test_amount_to_minor_zero_decimal_vnd():
    # VND est une devise « zéro-décimale » : le montant n'est PAS multiplié par 100.
    assert "VND" in ZERO_DECIMAL_CURRENCIES
    assert amount_to_minor(Decimal("50000"), "VND") == 50000
    assert amount_to_minor(Decimal("50000"), "vnd") == 50000  # insensible à la casse


def test_status_and_payment_labels_cover_codes():
    for s in SALE_STATUSES:
        assert s in SALE_STATUS_LABELS
    for m in PAYMENT_METHODS:
        assert m in PAYMENT_METHOD_LABELS
    for r in STOCK_REASONS:
        assert r in STOCK_REASON_LABELS
    assert REGIME_FRANCHISE == "franchise"


def test_register_export_csv():
    rows = [
        {
            "occurred_at": datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            "sku": "CAF-250",
            "label": "Café moulu 250 g",
            "unit": "pièce",
            "reason": "avitaillement",
            "qty_in": Decimal("24.000"),
            "qty_out": Decimal("0"),
            "sale_reference": "",
            "regime": "franchise",
            "note": "Bon n°42",
        },
        {
            "occurred_at": datetime(2026, 7, 9, 15, 30, tzinfo=UTC),
            "sku": "CAF-250",
            "label": "Café moulu 250 g",
            "unit": "pièce",
            "reason": "vente",
            "qty_in": Decimal("0"),
            "qty_out": Decimal("2.000"),
            "sale_reference": "VB-2026-0001",
            "regime": "franchise",
            "note": "",
        },
    ]
    csv_text = export_csv(rows, vessel_code="ANE")
    lines = csv_text.strip().splitlines()
    # En-tête + 2 lignes.
    assert len(lines) == 3
    assert "SKU" in lines[0] and "Régime" in lines[0]
    assert "avitaillement" in csv_text and "vente" in csv_text
    assert "VB-2026-0001" in csv_text
    assert "franchise" in csv_text
    assert "24.000" in csv_text and "2.000" in csv_text
    assert "ANE" in csv_text
