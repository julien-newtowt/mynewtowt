"""Tests des helpers de reporting RH (SIRH L6)."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.hr_reporting import (
    age_bracket,
    age_on,
    seniority_years,
    turnover_rate,
)


def test_age_on_before_and_after_birthday():
    assert age_on(date(1990, 6, 20), date(2026, 6, 19)) == 35  # veille anniversaire
    assert age_on(date(1990, 6, 20), date(2026, 6, 20)) == 36  # jour anniversaire


@pytest.mark.parametrize(
    "age,bracket",
    [(20, "<25"), (24, "<25"), (25, "25-34"), (34, "25-34"),
     (35, "35-44"), (44, "35-44"), (45, "45-54"), (54, "45-54"),
     (55, "55+"), (70, "55+")],
)
def test_age_bracket(age, bracket):
    assert age_bracket(age) == bracket


def test_seniority_years():
    assert seniority_years(date(2020, 1, 1), date(2026, 1, 1)) == pytest.approx(6.0, abs=0.1)


def test_turnover_rate():
    assert turnover_rate(2, 20) == 10.0
    assert turnover_rate(0, 0) == 0.0  # pas de division par zéro
    assert turnover_rate(5, 0) == 0.0
