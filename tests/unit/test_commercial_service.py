"""Tests for app.services.commercial — pricing brackets logic."""

from __future__ import annotations

from decimal import Decimal

from app.models.commercial import (
    DEFAULT_BRACKETS_SHIPPER,
    PALETTE_COEFFICIENTS,
)
from app.services.commercial import (
    bracket_rate,
    compute_offer_total,
    default_brackets_for,
    pick_bracket,
)


def test_default_brackets_for_shipper_returns_seven_brackets():
    b = default_brackets_for("shipper")
    assert len(b) == 7
    assert b[0]["key"] == "lt50"
    assert b[-1]["key"] == "full"


def test_default_brackets_for_freight_forwarder_returns_flat():
    b = default_brackets_for("freight_forwarder")
    assert len(b) == 1
    assert b[0]["coeff"] == 1.0


def test_pick_bracket_below_first_threshold():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 10)
    assert b["key"] == "lt50"


def test_pick_bracket_at_100():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 100)
    assert b["key"] == "100"


def test_pick_bracket_at_350_picks_400():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 350)
    assert b["key"] == "400"


def test_pick_bracket_above_max_picks_full():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 1000)
    assert b["key"] == "full"


def test_bracket_rate_applies_base_coeff_and_index():
    rate = bracket_rate(
        base_rate=Decimal("100.00"),
        coeff=Decimal("0.80"),
        adjustment_index=Decimal("1.05"),
    )
    assert rate == Decimal("84.00")


def test_compute_offer_total_quantizes_to_cents():
    total = compute_offer_total(
        base_rate=Decimal("38.50"),
        coeff=Decimal("0.70"),
        adjustment_index=Decimal("1.0"),
        qty=500,
    )
    # 38.50 * 0.70 = 26.95 * 500 = 13475.00
    assert total == Decimal("13475.00")


def test_palette_coefficients_for_oversized():
    assert PALETTE_COEFFICIENTS["EPAL"] == 1.0
    assert PALETTE_COEFFICIENTS["BARRIQUE140"] == 2.0
    assert PALETTE_COEFFICIENTS["IBC"] == 1.3
