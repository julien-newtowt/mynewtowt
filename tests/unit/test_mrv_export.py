"""Tests for app.services.mrv_export — DNV 18-col CSV, SOF→MRV mapping, carbon summary.

Lot 10 : ``to_dnv_csv`` (export DNV 9 colonnes, code mort) a été SUPPRIMÉ ; les
sorties réglementaires vivent dans ``services.mrv_dataset`` (OVDLA/OVDBR, testé
séparément). On garde ici les tests du ``dnv_csv_18`` (déprécié derrière flag,
retiré au lot 14) et du carbon summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.mrv_export import (
    CO2_EMISSION_FACTOR_MDO,
    DNV_18_HEADERS,
    build_dnv_rows,
    carbon_report_summary,
    dnv_csv_18,
    map_sof_to_mrv_type,
)


@dataclass
class FakeEvent:
    consumed_t: float | None = 0.8


@dataclass
class FakeDnvEvent:
    """Événement au shape attendu par ``build_dnv_rows`` (18 colonnes)."""

    leg_id: int = 1
    recorded_at: datetime = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    event_kind: str = "arrival"
    distance_nm: float | None = 549.0
    cargo_carried_t: float | None = 707.15
    me_consumption_t: float | None = 9.2
    ae_consumption_t: float | None = 6.5
    total_consumption_t: float | None = 15.7
    rob_calculated_t: float | None = 57.9
    lat_deg: int | None = 40
    lat_min: float | None = 41.0
    lat_ns: str | None = "N"
    lon_deg: int | None = 74
    lon_min: float | None = 8.0
    lon_ew: str | None = "W"


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


# ─────────────────────────────── DNV 18 colonnes (déprécié, gardé) ──────────


def _maps():
    vessel = SimpleNamespace(id=7, imo_number="9982938")
    leg = SimpleNamespace(id=1, vessel_id=7, departure_port_id=10, arrival_port_id=20)
    pol = SimpleNamespace(id=10, locode="FRLEH")
    pod = SimpleNamespace(id=20, locode="USNYC")
    return {1: leg}, {7: vessel}, {10: pol, 20: pod}


def test_dnv_18_headers_shape():
    assert len(DNV_18_HEADERS) == 18
    assert DNV_18_HEADERS[0] == "IMO"
    assert "Total_Consumption_MDO_mt" in DNV_18_HEADERS


def test_build_dnv_rows_resolves_vessel_leg_ports():
    leg_map, vessel_map, port_map = _maps()
    rows = build_dnv_rows([FakeDnvEvent()], leg_map=leg_map, vessel_map=vessel_map, port_map=port_map)
    assert len(rows) == 1
    r = rows[0]
    assert r["IMO"] == "9982938"
    assert r["Voyage_From"] == "FRLEH"
    assert r["Voyage_To"] == "USNYC"
    assert r["Event"] == "arrival"
    assert r["Latitude_NS"] == "N"


def test_dnv_csv_18_serialises_header_and_row():
    leg_map, vessel_map, port_map = _maps()
    rows = build_dnv_rows([FakeDnvEvent()], leg_map=leg_map, vessel_map=vessel_map, port_map=port_map)
    csv_text = dnv_csv_18(rows)
    lines = csv_text.splitlines()
    assert lines[0].split(",") == list(DNV_18_HEADERS)
    assert "9982938" in lines[1]
    assert "USNYC" in lines[1]


def test_dnv_18_time_since_previous_per_vessel():
    """``Time_Since_Previous_h`` = écart au précédent événement du même navire."""
    leg_map, vessel_map, port_map = _maps()
    e1 = FakeDnvEvent(recorded_at=datetime(2026, 5, 1, 0, 0, tzinfo=UTC))
    e2 = FakeDnvEvent(recorded_at=datetime(2026, 5, 2, 0, 0, tzinfo=UTC))
    rows = build_dnv_rows([e1, e2], leg_map=leg_map, vessel_map=vessel_map, port_map=port_map)
    assert rows[0]["Time_Since_Previous_h"] == ""  # premier événement du navire
    assert rows[1]["Time_Since_Previous_h"] == "24.00"


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
