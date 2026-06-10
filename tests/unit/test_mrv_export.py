"""Tests for app.services.mrv_export — DNV CSV generation, SOF→MRV mapping."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.mrv_export import (
    CO2_EMISSION_FACTOR_MDO,
    carbon_report_summary,
    map_sof_to_mrv_type,
    to_dnv_csv,
)


@dataclass
class FakeEvent:
    vessel_imo: str = "9876543"
    leg_code: str = "1CFRBR6"
    event_type: str = "departure"
    occurred_at: datetime = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    fuel_type: str = "MDO"
    rob_t: float | None = 12.5
    consumed_t: float | None = 0.8
    notes: str | None = "noon report"


def test_co2_factor_is_3206():
    assert CO2_EMISSION_FACTOR_MDO == 3.206


def test_sof_to_mrv_map_known_events():
    assert map_sof_to_mrv_type("SOSP") == "departure"
    assert map_sof_to_mrv_type("EOSP") == "arrival"
    assert map_sof_to_mrv_type("ANCHORED") == "begin_anchoring"


def test_sof_to_mrv_map_unknown_returns_none():
    assert map_sof_to_mrv_type("UNKNOWN_TYPE") is None


def test_to_dnv_csv_header_present():
    csv = to_dnv_csv([FakeEvent()])
    first_line = csv.splitlines()[0]
    assert "vessel_imo" in first_line
    assert "co2_t" in first_line
    assert ";" in first_line


def test_to_dnv_csv_computes_co2():
    csv = to_dnv_csv([FakeEvent(consumed_t=1.0)])
    # CO₂ = 1.0 * 3.206 = 3.206
    assert "3.206" in csv


def test_carbon_report_summary_aggregates():
    events = [FakeEvent(consumed_t=0.5), FakeEvent(consumed_t=1.0), FakeEvent(consumed_t=0.3)]
    summary = carbon_report_summary(events)
    assert summary["total_fuel_t"] == 1.8
    assert summary["event_count"] == 3
    assert abs(summary["total_co2_t"] - 1.8 * 3.206) < 0.001


def test_carbon_report_summary_handles_none():
    events = [FakeEvent(consumed_t=None), FakeEvent(consumed_t=2.0)]
    summary = carbon_report_summary(events)
    assert summary["total_fuel_t"] == 2.0
    assert summary["event_count"] == 1
