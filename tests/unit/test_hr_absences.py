"""Tests du décompte de jours ouvrés des absences (SIRH L3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.hr_absences import count_business_days


def test_full_week_monday_to_friday() -> None:
    # 2026-06-15 (lun) → 2026-06-19 (ven) = 5 jours ouvrés.
    assert count_business_days(date(2026, 6, 15), date(2026, 6, 19)) == Decimal("5")


def test_weekend_excluded() -> None:
    # lundi → lundi suivant inclus = 6 ouvrés (2 week-end exclus).
    assert count_business_days(date(2026, 6, 15), date(2026, 6, 22)) == Decimal("6")


def test_single_day() -> None:
    assert count_business_days(date(2026, 6, 17), date(2026, 6, 17)) == Decimal("1")


def test_single_day_half() -> None:
    # Un seul jour avec demi-journée début → 0.5 (la borne fin == début est ignorée).
    assert count_business_days(
        date(2026, 6, 17), date(2026, 6, 17), half_day_start=True
    ) == Decimal("0.5")


def test_half_days_both_ends() -> None:
    assert count_business_days(
        date(2026, 6, 15), date(2026, 6, 19), half_day_start=True, half_day_end=True
    ) == Decimal("4.0")


def test_half_day_on_weekend_border_not_counted() -> None:
    # Samedi 2026-06-20 comme borne de fin demi-journée : pas de décompte
    # (jour non ouvré), donc lun→sam = 5 ouvrés inchangés.
    assert count_business_days(
        date(2026, 6, 15), date(2026, 6, 20), half_day_end=True
    ) == Decimal("5")


def test_pure_weekend_is_zero() -> None:
    assert count_business_days(date(2026, 6, 20), date(2026, 6, 21)) == Decimal("0")


def test_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        count_business_days(date(2026, 6, 19), date(2026, 6, 15))
