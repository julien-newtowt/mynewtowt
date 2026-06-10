"""Planning service — date validation, leg_code generation, conflict detection.

Cascade behaviour is validated through pure logic on the data classes;
DB-backed cascade test belongs in tests/integration.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.planning import (
    InvalidLegDates,
    _leg_code_for,
    detect_port_conflicts,
    validate_dates,
)


def test_validate_dates_accepts_normal_window() -> None:
    now = datetime.now(UTC)
    validate_dates(now, now + timedelta(days=8))  # no raise


def test_validate_dates_refuses_etd_after_eta() -> None:
    now = datetime.now(UTC)
    with pytest.raises(InvalidLegDates):
        validate_dates(now + timedelta(days=1), now)


def test_validate_dates_refuses_overlong_window() -> None:
    now = datetime.now(UTC)
    with pytest.raises(InvalidLegDates):
        validate_dates(now, now + timedelta(days=200))


def test_leg_code_format() -> None:
    etd = datetime(2026, 6, 4, tzinfo=UTC)
    # Format spec NEWTOWT : {seq}{vessel_code}{POL}{POD}{year_digit}
    code = _leg_code_for("C", "FR", "BR", etd)
    assert code == "1CFRBR6"


def test_leg_code_sequence_bump() -> None:
    etd = datetime(2026, 6, 4, tzinfo=UTC)
    code_2 = _leg_code_for("C", "FR", "US", etd, 2)
    assert code_2 == "2CFRUS6"


def _leg_ns(id, vessel_id, arrival_port_id, eta, stay=24):
    return SimpleNamespace(
        id=id, vessel_id=vessel_id, arrival_port_id=arrival_port_id,
        eta=eta, port_stay_planned_hours=stay,
    )


def test_detect_port_conflicts_finds_concurrent_arrivals() -> None:
    """Two different vessels whose escale windows overlap = conflict."""
    base_eta = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=24)
    leg_b = _leg_ns(2, 11, 42, base_eta + timedelta(hours=4), stay=24)
    assert detect_port_conflicts([leg_a, leg_b]) == [(1, 2)]


def test_detect_port_conflicts_ignores_same_vessel() -> None:
    base_eta = datetime(2026, 6, 12, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=24)
    leg_b = _leg_ns(2, 10, 42, base_eta + timedelta(hours=2), stay=24)
    assert detect_port_conflicts([leg_a, leg_b]) == []


def test_detect_port_conflicts_non_overlapping_stays_ok() -> None:
    """Same port, ETA 20h apart but SHORT stays → no overlap → OK."""
    base_eta = datetime(2026, 6, 12, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=4)               # [0h, 4h]
    leg_b = _leg_ns(2, 11, 42, base_eta + timedelta(hours=20), stay=4)  # [20h, 24h]
    assert detect_port_conflicts([leg_a, leg_b]) == []


def test_detect_port_conflicts_overlapping_long_stays() -> None:
    """REGRESSION fix: ETA 20h apart but long stays overlap → conflict.

    L'ancienne heuristique ETA±12h ratait ce cas réel.
    """
    base_eta = datetime(2026, 6, 12, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=48)              # [0h, 48h]
    leg_b = _leg_ns(2, 11, 42, base_eta + timedelta(hours=20), stay=24)  # [20h, 44h]
    assert detect_port_conflicts([leg_a, leg_b]) == [(1, 2)]


def test_detect_port_conflicts_ignores_different_ports() -> None:
    base_eta = datetime(2026, 6, 12, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=24)
    leg_b = _leg_ns(2, 11, 43, base_eta, stay=24)
    assert detect_port_conflicts([leg_a, leg_b]) == []
