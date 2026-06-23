"""Tests du décompte de jours ouvrables des absences (SIRH L3).

Convention : jours **ouvrables** = lundi à samedi inclus ; seul le dimanche
est exclu. Repères 2026 : 15/06 lun, 19/06 ven, 20/06 sam, 21/06 dim, 22/06 lun.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.hr_absences import count_business_days


def test_full_week_monday_to_friday() -> None:
    # lun → ven = 5 jours ouvrables (pas de dimanche dans l'intervalle).
    assert count_business_days(date(2026, 6, 15), date(2026, 6, 19)) == Decimal("5")


def test_saturday_is_workable_sunday_excluded() -> None:
    # lun → lun suivant inclus = 8 jours - 1 dimanche = 7 ouvrables.
    assert count_business_days(date(2026, 6, 15), date(2026, 6, 22)) == Decimal("7")


def test_includes_saturday() -> None:
    # lun → sam = 6 jours ouvrables (samedi compté).
    assert count_business_days(date(2026, 6, 15), date(2026, 6, 20)) == Decimal("6")


def test_single_day() -> None:
    assert count_business_days(date(2026, 6, 17), date(2026, 6, 17)) == Decimal("1")


def test_single_day_half() -> None:
    assert count_business_days(
        date(2026, 6, 17), date(2026, 6, 17), half_day_start=True
    ) == Decimal("0.5")


def test_half_days_both_ends() -> None:
    assert count_business_days(
        date(2026, 6, 15), date(2026, 6, 19), half_day_start=True, half_day_end=True
    ) == Decimal("4.0")


def test_half_day_on_sunday_border_not_counted() -> None:
    # lun → dim : dimanche exclu (6 ouvrables) ; demi-journée sur le dimanche
    # (non ouvrable) n'est pas décomptée.
    assert count_business_days(date(2026, 6, 15), date(2026, 6, 21), half_day_end=True) == Decimal(
        "6"
    )


def test_pure_sunday_is_zero() -> None:
    assert count_business_days(date(2026, 6, 21), date(2026, 6, 21)) == Decimal("0")


def test_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        count_business_days(date(2026, 6, 19), date(2026, 6, 15))
