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


def test_default_brackets_follow_the_business_scale():
    """Barème métier : < 50 / 50-100 / 100-300 / 300-500 / 500-800 / navire complet.

    Les bornes sont **inclusives des deux côtés** : ``max_qty`` porte la borne
    haute incluse, la borne basse du palier suivant est celle-ci + 1.
    """
    b = default_brackets_for("shipper")
    assert [x["key"] for x in b] == [
        "lt50",
        "50_100",
        "100_300",
        "300_500",
        "500_800",
        "full",
    ]
    assert [x["max_qty"] for x in b] == [49, 100, 300, 500, 800, None]
    # Coefficients strictement dégressifs — un palier plus gros ne coûte jamais plus cher.
    coeffs = [x["coeff"] for x in b]
    assert coeffs == sorted(coeffs, reverse=True)


def test_default_brackets_bounds_are_inclusive():
    """Chaque borne annoncée appartient bien à son palier."""
    for qty, expected in [
        (1, "lt50"),
        (49, "lt50"),
        (50, "50_100"),
        (100, "50_100"),  # « de 50 à 100 » inclut 100
        (101, "100_300"),
        (300, "100_300"),
        (301, "300_500"),
        (500, "300_500"),
        (501, "500_800"),
        (800, "500_800"),
        (801, "full"),
    ]:
        assert pick_bracket(DEFAULT_BRACKETS_SHIPPER, qty)["key"] == expected, qty


def test_default_brackets_for_freight_forwarder_returns_flat():
    b = default_brackets_for("freight_forwarder")
    assert len(b) == 1
    assert b[0]["coeff"] == 1.0


def test_pick_bracket_below_first_threshold():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 10)
    assert b["key"] == "lt50"


def test_pick_bracket_at_100():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 100)
    assert b["key"] == "50_100"


def test_pick_bracket_at_350_picks_300_500():
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 350)
    assert b["key"] == "300_500"


def test_pick_bracket_above_max_picks_full():
    """Le palier « navire complet » n'est pas borné : aucune quantité ne le dépasse."""
    b = pick_bracket(DEFAULT_BRACKETS_SHIPPER, 1000)
    assert b["key"] == "full"
    assert pick_bracket(DEFAULT_BRACKETS_SHIPPER, 99_999)["key"] == "full"


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
