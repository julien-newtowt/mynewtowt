"""Tests des helpers de période de paie (SIRH L4)."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.payroll import (
    current_period,
    is_valid_period,
    overlaps_period,
    period_bounds,
    shift_period,
)


@pytest.mark.parametrize(
    "value,ok",
    [
        ("2026-06", True),
        ("2026-12", True),
        ("2026-01", True),
        ("2026-13", False),
        ("2026-00", False),
        ("26-06", False),
        ("2026/06", False),
        ("", False),
        (None, False),
    ],
)
def test_is_valid_period(value, ok):
    assert is_valid_period(value) is ok


def test_period_bounds():
    first, last = period_bounds("2026-02")
    assert first == date(2026, 2, 1)
    assert last == date(2026, 2, 28)  # 2026 non bissextile


def test_period_bounds_invalid():
    with pytest.raises(ValueError):
        period_bounds("2026-13")


def test_current_period_format():
    assert current_period(date(2026, 6, 9)) == "2026-06"


@pytest.mark.parametrize(
    "start,end,period,expected",
    [
        (date(2026, 6, 10), date(2026, 6, 12), "2026-06", True),
        (date(2026, 5, 28), date(2026, 6, 2), "2026-06", True),   # chevauche début
        (date(2026, 6, 28), date(2026, 7, 3), "2026-06", True),   # chevauche fin
        (date(2026, 5, 1), date(2026, 5, 31), "2026-06", False),  # mois précédent
        (date(2026, 7, 1), date(2026, 7, 5), "2026-06", False),   # mois suivant
    ],
)
def test_overlaps_period(start, end, period, expected):
    assert overlaps_period(start, end, period) is expected


@pytest.mark.parametrize(
    "period,delta,expected",
    [
        ("2026-06", -1, "2026-05"),
        ("2026-01", -1, "2025-12"),
        ("2026-12", 1, "2027-01"),
        ("2026-06", 0, "2026-06"),
        ("2026-06", 7, "2027-01"),
    ],
)
def test_shift_period(period, delta, expected):
    assert shift_period(period, delta) == expected
