"""Tests for app.services.mrv_export — SOF→MRV mapping + carbon summary.

LOT 14 : les exports CSV DNV (9 col. code mort dès lot 10, puis 18 col.
``dnv_csv_18``/``build_dnv_rows``/``DNV_18_HEADERS``) ont été RETIRÉS (Q3) — la
voie unique est ``services.mrv_dataset`` (OVDLA/OVDBR, testé séparément). Ne
subsistent ici que le mapping SOF→MRV (consommé par ``voyage_events``) et
l'agrégat ``carbon_report_summary`` (référencé par la sentinelle facteurs).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.mrv_export import (
    CO2_EMISSION_FACTOR_MDO,
    carbon_report_summary,
    map_sof_to_mrv_type,
)


@dataclass
class FakeEvent:
    consumed_t: float | None = 0.8


def test_co2_factor_is_3206():
    assert CO2_EMISSION_FACTOR_MDO == 3.206


def test_sof_to_mrv_map_known_events():
    assert map_sof_to_mrv_type("SOSP") == "departure"
    assert map_sof_to_mrv_type("EOSP") == "arrival"
    assert map_sof_to_mrv_type("ANCHORED") == "begin_anchoring"


def test_sof_to_mrv_map_unknown_returns_none():
    assert map_sof_to_mrv_type("UNKNOWN_TYPE") is None


def test_to_dnv_csv_removed():
    """Le code mort ``to_dnv_csv`` (9 colonnes) a bien été purgé (lot 10)."""
    import app.services.mrv_export as me

    assert not hasattr(me, "to_dnv_csv")


def test_dnv_18_columns_export_removed_lot14():
    """LOT 14 (Q3) — l'export DNV 18 colonnes est RETIRÉ (OVDLA/OVDBR unique)."""
    import app.services.mrv_export as me

    assert not hasattr(me, "DNV_18_HEADERS")
    assert not hasattr(me, "dnv_csv_18")
    assert not hasattr(me, "build_dnv_rows")


# ─────────────────────────────── Carbon summary ─────────────────────────────


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
