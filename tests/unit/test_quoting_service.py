"""Tests unitaires du moteur de cotation multi-routes (services/quoting).

Fonctions pures uniquement (pas de DB) : brackets de grille, base_rate de la
route, options, surcharges, forfaits, formule OPEX et résolution de route.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.models.commercial import RateGrid, RateGridLine, RateGridOption
from app.services.quoting import (
    QuotingError,
    _match_route,
    bracket_for_quantity,
    compute_grid_quote,
    route_base_rate,
    route_nav_days,
)

_DEFAULT_BRACKETS = [
    {"key": "lt50", "label": "lt50", "max_qty": 49, "coeff": 1.10},
    {"key": "200", "label": "200", "max_qty": 200, "coeff": 0.80},
    {"key": "full", "label": "full", "max_qty": 850, "coeff": 0.60},
]


def _grid(
    *,
    adjustment: str = "1.0000",
    brackets: list[dict] | None = None,
    options: list[tuple[str, str, str, bool]] | None = None,
    bl_fee: str | None = None,
    booking_fee: str | None = None,
    min_charge: str | None = None,
    haz_pct: str | None = None,
) -> RateGrid:
    grid = RateGrid(
        reference="RG-2026-TEST",
        client_id=None,
        is_default=True,
        status="active",
        adjustment_index=Decimal(adjustment),
        currency="EUR",
        bl_fee=Decimal(bl_fee) if bl_fee is not None else None,
        booking_fee=Decimal(booking_fee) if booking_fee is not None else None,
        min_charge_eur=Decimal(min_charge) if min_charge is not None else None,
        hazardous_surcharge_pct=Decimal(haz_pct) if haz_pct is not None else None,
    )
    grid.id = 1
    grid.brackets_json = json.dumps(brackets or _DEFAULT_BRACKETS)
    grid.lines = []
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


def _route(base: str = "100.00", *, pol: str = "FRLEH", pod: str = "USNYC") -> RateGridLine:
    route = RateGridLine(
        grid_id=1,
        pol_locode=pol,
        pod_locode=pod,
        distance_nm=Decimal("3180"),
        nav_days=Decimal("16.562"),
        opex_daily=Decimal("12000"),
        base_rate=Decimal(base),
        is_manual=False,
    )
    route.id = 1
    return route


# ─────────────────────────── brackets (grille) ──────────────────────────────


def test_bracket_selection_reads_grid_brackets():
    grid = _grid()
    label, coeff = bracket_for_quantity(grid, 10)
    assert (label, coeff) == ("lt50", Decimal("1.10"))
    _label, coeff = bracket_for_quantity(grid, 200)
    assert coeff == Decimal("0.80")
    # Au-delà de la dernière bracket : on retombe sur la dernière.
    _label, coeff = bracket_for_quantity(grid, 2000)
    assert coeff == Decimal("0.60")


def test_brackets_fallback_to_default_shipper_when_unset():
    grid = _grid()
    grid.brackets_json = None
    # défaut shipper : 7 brackets, full ship ×0.60
    _label, coeff = bracket_for_quantity(grid, 850)
    assert coeff == Decimal("0.60")


# ─────────────────────────── fret (base_rate de la route) ───────────────────


def test_freight_uses_route_base_x_adjustment_x_bracket_x_format():
    grid = _grid(adjustment="1.1000")
    route = _route(base="100.00")
    quote = compute_grid_quote(grid, route, items=[("EPAL", 10), ("BARRIQUE140", 5)])
    # 10 palettes + 5 barriques = 15 → bracket lt50 (×1.10)
    # base effective = 100 (route) × 1.10 (adj) × 1.10 (bracket) = 121.00
    epal = next(li for li in quote.lines if "EPAL" in li.label)
    barrique = next(li for li in quote.lines if "BARRIQUE140" in li.label)
    assert epal.unit_price_eur == Decimal("121.00")
    assert barrique.unit_price_eur == Decimal("242.00")  # coef format 2.0
    assert quote.freight_subtotal_eur == Decimal("121.00") * 10 + Decimal("242.00") * 5
    assert quote.total_eur == quote.freight_subtotal_eur


def test_distinct_routes_yield_distinct_base_rates():
    """Une même grille tarifie chaque route via le base_rate de sa ligne."""
    grid = _grid()
    q1 = compute_grid_quote(grid, _route(base="330.00"), items=[("EPAL", 10)])
    q2 = compute_grid_quote(grid, _route(base="450.00"), items=[("EPAL", 10)])
    # même bracket (lt50 ×1.10) mais base_rate distinct par route
    assert q1.lines[0].unit_price_eur == Decimal("363.00")
    assert q2.lines[0].unit_price_eur == Decimal("495.00")


# ─────────────────────────── options / forfaits ─────────────────────────────


def test_active_options_are_quoted_by_unit():
    grid = _grid(
        options=[
            ("BOOKING_NOTE", "per_booking_note", "50.00", True),
            ("THC", "per_palette", "12.00", True),
            ("TONNE", "per_tonne", "3.00", True),
            ("INACTIVE", "per_booking", "999.00", False),
        ]
    )
    quote = compute_grid_quote(grid, _route(), items=[("EPAL", 20)], tonnage_t=Decimal("10.5"))
    by_code = {li.label.split(" ")[0]: li for li in quote.lines if li.kind == "option"}
    assert by_code["BOOKING_NOTE"].total_eur == Decimal("50.00")
    assert by_code["THC"].total_eur == Decimal("240.00")  # 12 × 20 palettes
    assert by_code["TONNE"].total_eur == Decimal("31.50")  # 3 × 10.5 t
    assert "INACTIVE" not in by_code
    assert quote.options_total_eur == Decimal("321.50")


def test_per_tonne_option_skipped_without_tonnage():
    grid = _grid(options=[("TONNE", "per_tonne", "3.00", True)])
    quote = compute_grid_quote(grid, _route(), items=[("EPAL", 5)], tonnage_t=None)
    assert all(li.kind != "option" for li in quote.lines)
    assert quote.options_total_eur == Decimal("0")


def test_bl_and_booking_fees_are_added_once():
    grid = _grid(bl_fee="35.00", booking_fee="50.00")
    quote = compute_grid_quote(grid, _route(), items=[("EPAL", 10)])
    labels = [li.label for li in quote.lines if li.kind == "option"]
    assert "Frais de connaissement (BL)" in labels
    assert "Frais de réservation (booking)" in labels
    assert quote.options_total_eur == Decimal("85.00")
    assert quote.total_eur == quote.freight_subtotal_eur + Decimal("85.00")


def test_hazardous_surcharge_is_25_pct_of_freight():
    grid = _grid()
    quote = compute_grid_quote(grid, _route(), items=[("EPAL", 10)], hazardous=True)
    surcharge = next(li for li in quote.lines if li.kind == "surcharge")
    assert surcharge.total_eur == (quote.freight_subtotal_eur * Decimal("0.25")).quantize(
        Decimal("0.01")
    )


def test_min_charge_tops_up_low_totals():
    grid = _grid(min_charge="500.00")
    quote = compute_grid_quote(grid, _route(base="10.00"), items=[("EPAL", 1)])
    assert quote.total_eur == Decimal("500.00")
    assert any(li.label == "Ajustement minimum de facturation" for li in quote.lines)


def test_volume_commitment_flag():
    grid = _grid()
    grid.volume_commitment = 50
    quote = compute_grid_quote(grid, _route(), items=[("EPAL", 20)])
    assert quote.volume_commitment == 50
    assert quote.below_commitment is True


def test_zero_palettes_raises():
    grid = _grid()
    try:
        compute_grid_quote(grid, _route(), items=[("EPAL", 0)])
    except QuotingError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected QuotingError")


# ─────────────────────────── formule OPEX (route) ───────────────────────────


def test_route_economics_formula_matches_acceptance():
    # Acceptance Module 6 : FRFEC→BRSSO, 4 500 NM, OPEX 12 000 €/j.
    nav_days = route_nav_days(Decimal("4500"))
    assert nav_days == Decimal("23.438")  # 4500 / (8 × 24) = 23.4375 → 23.438
    base = route_base_rate(Decimal("12000"), nav_days)
    assert base == Decimal("330.89")  # 12000 × 23.438 / 850


def test_route_base_rate_has_floor():
    assert route_base_rate(Decimal("0"), Decimal("0")) == Decimal("1.00")


# ─────────────────────────── résolution de route ────────────────────────────


def test_match_route_is_case_insensitive_and_exact():
    grid = _grid()
    grid.lines = [_route(pol="FRFEC", pod="BRSSO"), _route(pol="BRSSO", pod="FRFEC")]
    assert _match_route(grid, "frfec", "brsso") is grid.lines[0]
    assert _match_route(grid, "BRSSO", "FRFEC") is grid.lines[1]
    assert _match_route(grid, "FRFEC", "USNYC") is None
