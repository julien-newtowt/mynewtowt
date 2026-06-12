"""Tests unitaires du moteur de cotation par grille (services/quoting).

Fonctions pures uniquement (pas de DB) : brackets, options, surcharges.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.commercial import RateGrid, RateGridLine, RateGridOption
from app.services.quoting import (
    QuotingError,
    bracket_for_quantity,
    compute_grid_quote,
)


def _grid(
    *,
    base: str = "100.00",
    adjustment: str = "1.0000",
    brackets: list[tuple[str, int, str]] | None = None,
    options: list[tuple[str, str, str, bool]] | None = None,
) -> RateGrid:
    grid = RateGrid(
        reference="RG-2026-TEST",
        client_id=None,
        pol_locode="FRLEH",
        pod_locode="USNYC",
        is_default=True,
        status="active",
        base_rate_per_palette=Decimal(base),
        adjustment_index=Decimal(adjustment),
        currency="EUR",
    )
    grid.id = 1
    grid.lines = [
        RateGridLine(
            grid_id=1,
            bracket_key=key,
            bracket_label=key,
            max_qty=max_qty,
            coeff=Decimal(coeff),
        )
        for key, max_qty, coeff in (
            brackets
            or [("lt50", 49, "1.10"), ("200", 200, "0.80"), ("full", 850, "0.60")]
        )
    ]
    grid.options = [
        RateGridOption(
            grid_id=1,
            code=code,
            label=code,
            unit=unit,
            amount_eur=Decimal(amount),
            is_active=active,
        )
        for code, unit, amount, active in (options or [])
    ]
    return grid


def test_bracket_selection_uses_first_matching_max_qty():
    grid = _grid()
    label, coeff = bracket_for_quantity(grid, 10)
    assert (label, coeff) == ("lt50", Decimal("1.10"))
    label, coeff = bracket_for_quantity(grid, 200)
    assert coeff == Decimal("0.80")
    # Au-delà de la dernière bracket : on retombe sur la dernière.
    label, coeff = bracket_for_quantity(grid, 2000)
    assert coeff == Decimal("0.60")


def test_freight_uses_base_x_adjustment_x_bracket_x_format():
    grid = _grid(base="100.00", adjustment="1.1000")
    quote = compute_grid_quote(grid, items=[("EPAL", 10), ("BARRIQUE140", 5)])
    # 10 palettes + 5 barriques = 15 → bracket lt50 (×1.10)
    # base effective = 100 × 1.10 (adj) × 1.10 (bracket) = 121.00
    epal = next(li for li in quote.lines if "EPAL" in li.label)
    barrique = next(li for li in quote.lines if "BARRIQUE140" in li.label)
    assert epal.unit_price_eur == Decimal("121.00")
    assert barrique.unit_price_eur == Decimal("242.00")  # coef format 2.0
    assert quote.freight_subtotal_eur == Decimal("121.00") * 10 + Decimal("242.00") * 5
    assert quote.total_eur == quote.freight_subtotal_eur


def test_active_options_are_quoted_by_unit():
    grid = _grid(
        options=[
            ("BOOKING_NOTE", "per_booking_note", "50.00", True),
            ("THC", "per_palette", "12.00", True),
            ("TONNE", "per_tonne", "3.00", True),
            ("INACTIVE", "per_booking", "999.00", False),
        ]
    )
    quote = compute_grid_quote(
        grid, items=[("EPAL", 20)], tonnage_t=Decimal("10.5")
    )
    by_code = {li.label.split(" ")[0]: li for li in quote.lines if li.kind == "option"}
    assert by_code["BOOKING_NOTE"].total_eur == Decimal("50.00")
    assert by_code["THC"].total_eur == Decimal("240.00")  # 12 × 20 palettes
    assert by_code["TONNE"].total_eur == Decimal("31.50")  # 3 × 10.5 t
    assert "INACTIVE" not in by_code
    assert quote.options_total_eur == Decimal("321.50")


def test_per_tonne_option_skipped_without_tonnage():
    grid = _grid(options=[("TONNE", "per_tonne", "3.00", True)])
    quote = compute_grid_quote(grid, items=[("EPAL", 5)], tonnage_t=None)
    assert all(li.kind != "option" for li in quote.lines)
    assert quote.options_total_eur == Decimal("0")


def test_hazardous_surcharge_is_25_pct_of_freight():
    grid = _grid(base="100.00")
    quote = compute_grid_quote(grid, items=[("EPAL", 10)], hazardous=True)
    surcharge = next(li for li in quote.lines if li.kind == "surcharge")
    assert surcharge.total_eur == (quote.freight_subtotal_eur * Decimal("0.25")).quantize(
        Decimal("0.01")
    )


def test_zero_palettes_raises():
    grid = _grid()
    try:
        compute_grid_quote(grid, items=[("EPAL", 0)])
    except QuotingError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected QuotingError")
