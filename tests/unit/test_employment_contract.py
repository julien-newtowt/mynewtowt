"""Tests des échéances de contrats (SIRH L2)."""

from __future__ import annotations

from datetime import date, timedelta

from app.models.employment_contract import (
    CONTRACT_TYPES,
    FIXED_TERM_TYPES,
    EmploymentContract,
)


def _contract(**kw) -> EmploymentContract:
    base = {"contract_type": "cdi", "start_date": date(2020, 1, 1), "status": "active"}
    base.update(kw)
    return EmploymentContract(**base)


def test_no_alert_for_open_ended_without_dates() -> None:
    c = _contract()
    assert c.alert_status == "ok"
    assert c.has_alert is False
    assert c.end_days_remaining is None
    assert c.trial_days_remaining is None


def test_end_warning_within_threshold() -> None:
    c = _contract(contract_type="cdd", end_date=date.today() + timedelta(days=10))
    assert c.end_warning is True
    assert c.alert_status == "warning"
    assert c.has_alert is True
    assert c.end_days_remaining == 10


def test_end_expired_when_term_passed() -> None:
    c = _contract(contract_type="cdd", end_date=date.today() - timedelta(days=2))
    assert c.end_expired is True
    assert c.alert_status == "expired"
    assert c.has_alert is True


def test_trial_warning() -> None:
    c = _contract(trial_end_date=date.today() + timedelta(days=5))
    assert c.trial_warning is True
    assert c.alert_status == "warning"


def test_far_end_date_is_ok() -> None:
    c = _contract(contract_type="cdd", end_date=date.today() + timedelta(days=200))
    assert c.end_warning is False
    assert c.alert_status == "ok"
    assert c.has_alert is False


def test_has_alert_requires_active_status() -> None:
    c = _contract(
        contract_type="cdd",
        end_date=date.today() + timedelta(days=5),
        status="ended",
    )
    # alert_status reste "warning" mais has_alert ne déclenche que si actif.
    assert c.alert_status == "warning"
    assert c.has_alert is False


def test_fixed_term_types_subset_of_contract_types() -> None:
    assert set(FIXED_TERM_TYPES).issubset(set(CONTRACT_TYPES))
    assert "cdi" not in FIXED_TERM_TYPES
