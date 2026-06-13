"""Tests purs (sans DB) de la caisse de bord : bornes de période, export
comptable CSV, et séparation encaissement / décaissement."""

from __future__ import annotations

import types
from datetime import UTC, datetime
from decimal import Decimal

from app.models.onboard_cashbox import (
    CATEGORY_KIND,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    categories_for,
)
from app.services.cashbox import export_csv, month_bounds


def test_month_bounds_inclusive_utc():
    start, end = month_bounds(2026, 2)  # février 2026 (28 j)
    assert start == datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)
    # décembre → 31 jours
    _, dec_end = month_bounds(2026, 12)
    assert dec_end.day == 31


def test_vente_a_bord_is_income():
    assert "vente_a_bord" in INCOME_CATEGORIES
    assert CATEGORY_KIND["vente_a_bord"] == "income"


def test_income_and_expense_are_disjoint():
    assert set(INCOME_CATEGORIES).isdisjoint(EXPENSE_CATEGORIES)
    assert categories_for("income") == INCOME_CATEGORIES
    assert categories_for("expense") == EXPENSE_CATEGORIES
    # défaut prudent : tout sens inconnu → décaissement
    assert categories_for("???") == EXPENSE_CATEGORIES


def _mov(**kw):
    base = {
        "occurred_at": datetime(2026, 6, 3, tzinfo=UTC),
        "recorded_at": datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
        "currency": "EUR",
        "amount": Decimal("-12.50"),
        "category": "avitaillement",
        "description": "eau + vivres",
        "receipt_url": None,
        "closure_id": None,
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_export_csv_sense_and_lock_columns():
    movs = [
        _mov(amount=Decimal("50.00"), category="vente_a_bord", description="vente bar"),
        _mov(amount=Decimal("-12.50"), receipt_url="cashbox/receipts/x.jpg", closure_id=4),
    ]
    csv_text = export_csv(movs, vessel_code="ANE", period="2026-06")
    lines = csv_text.strip().splitlines()
    assert "ANE" in lines[0] and "2026-06" in lines[0]
    # ligne encaissement
    assert "Encaissement" in csv_text and "Vente à bord" in csv_text
    # ligne décaissement, justificatif présent, verrouillée
    assert "Décaissement" in csv_text
    assert ";oui;" in csv_text  # justificatif = oui
    assert "verrouillé" in csv_text
    assert "-12.50" in csv_text
