"""Planning service — date validation, leg_code generation, conflict detection.

Cascade behaviour is validated through pure logic on the data classes;
DB-backed cascade test belongs in tests/integration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.planning import (
    InvalidLegDates,
    _leg_code_for,
    detect_port_conflicts,
    validate_dates,
)


def test_validate_dates_accepts_normal_window() -> None:
    now = datetime.now(timezone.utc)
    validate_dates(now, now + timedelta(days=8))  # no raise


def test_validate_dates_refuses_etd_after_eta() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(InvalidLegDates):
        validate_dates(now + timedelta(days=1), now)


def test_validate_dates_refuses_overlong_window() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(InvalidLegDates):
        validate_dates(now, now + timedelta(days=200))


def test_leg_code_format() -> None:
    etd = datetime(2026, 6, 4, tzinfo=timezone.utc)
    code = _leg_code_for("1", "FR", "US", etd)
    # SHIP + LETTER + POL_COUNTRY + POD_COUNTRY + YEAR_LAST_DIGIT
    assert code == "1AFRUS6"


def test_leg_code_letter_bump() -> None:
    etd = datetime(2026, 6, 4, tzinfo=timezone.utc)
    code_b = _leg_code_for("1", "FR", "US", etd, "B")
    assert code_b == "1BFRUS6"


def test_detect_port_conflicts_finds_concurrent_arrivals() -> None:
    """Two different vessels arriving at the same port within 12h = conflict."""
    base_eta = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    leg_a = SimpleNamespace(id=1, vessel_id=10, arrival_port_id=42, eta=base_eta)
    leg_b = SimpleNamespace(
        id=2, vessel_id=11, arrival_port_id=42, eta=base_eta + timedelta(hours=4)
    )
    conflicts = detect_port_conflicts([leg_a, leg_b])
    assert conflicts == [(1, 2)]


def test_detect_port_conflicts_ignores_same_vessel() -> None:
    """Same vessel = back-to-back legs at same port, not a conflict."""
    base_eta = datetime(2026, 6, 12, tzinfo=timezone.utc)
    leg_a = SimpleNamespace(id=1, vessel_id=10, arrival_port_id=42, eta=base_eta)
    leg_b = SimpleNamespace(
        id=2, vessel_id=10, arrival_port_id=42, eta=base_eta + timedelta(hours=2)
    )
    assert detect_port_conflicts([leg_a, leg_b]) == []


def test_detect_port_conflicts_ignores_distant_arrivals() -> None:
    """Same port but > 12h apart = OK, two different windows."""
    base_eta = datetime(2026, 6, 12, tzinfo=timezone.utc)
    leg_a = SimpleNamespace(id=1, vessel_id=10, arrival_port_id=42, eta=base_eta)
    leg_b = SimpleNamespace(
        id=2, vessel_id=11, arrival_port_id=42, eta=base_eta + timedelta(hours=20)
    )
    assert detect_port_conflicts([leg_a, leg_b]) == []


def test_detect_port_conflicts_ignores_different_ports() -> None:
    base_eta = datetime(2026, 6, 12, tzinfo=timezone.utc)
    leg_a = SimpleNamespace(id=1, vessel_id=10, arrival_port_id=42, eta=base_eta)
    leg_b = SimpleNamespace(id=2, vessel_id=11, arrival_port_id=43, eta=base_eta)
    assert detect_port_conflicts([leg_a, leg_b]) == []
