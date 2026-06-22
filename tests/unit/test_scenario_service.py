"""Tests unitaires du service de planification provisoire (scénarios).

Logique pure (Gantt, comparaison, CSV, avertissements) validée sans DB via
SimpleNamespace, à l'image de tests/unit/test_planning_service.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.planning import InvalidLegDates
from app.services.scenario import (
    _sea_days,
    _validate_leg_inputs,
    build_gantt_rows,
    scenario_warnings,
    to_csv,
)


def _leg(id, vessel_id, dep, arr, etd, eta, *, label=None, status="planned", elong=None):
    return SimpleNamespace(
        id=id,
        vessel_id=vessel_id,
        departure_port_id=dep,
        arrival_port_id=arr,
        etd=etd,
        eta=eta,
        label=label,
        status=status,
        port_stay_planned_hours=None,
        elongation_coef=elong,
        transit_speed_kn=None,
        notes=None,
    )


# ── Validation dure ────────────────────────────────────────────────────────


def test_validate_leg_inputs_ok() -> None:
    now = datetime.now(UTC)
    _validate_leg_inputs(departure_port_id=1, arrival_port_id=2, etd=now, eta=now + timedelta(days=3))


def test_validate_leg_inputs_rejects_same_port() -> None:
    now = datetime.now(UTC)
    with pytest.raises(InvalidLegDates):
        _validate_leg_inputs(departure_port_id=1, arrival_port_id=1, etd=now, eta=now + timedelta(days=1))


def test_validate_leg_inputs_rejects_etd_after_eta() -> None:
    now = datetime.now(UTC)
    with pytest.raises(InvalidLegDates):
        _validate_leg_inputs(departure_port_id=1, arrival_port_id=2, etd=now + timedelta(days=1), eta=now)


# ── Gantt ──────────────────────────────────────────────────────────────────


def test_build_gantt_rows_positions_bar_within_window() -> None:
    ws = datetime(2027, 1, 1, tzinfo=UTC)
    we = datetime(2027, 12, 31, 23, 59, tzinfo=UTC)
    vessel = SimpleNamespace(id=10, code="C", name="Anemos")
    leg = _leg(1, 10, 1, 2, datetime(2027, 7, 1, tzinfo=UTC), datetime(2027, 7, 15, tzinfo=UTC), label="L1")
    rows = build_gantt_rows(vessels=[vessel], legs=[leg], window_start=ws, window_end=we, ports={})
    assert len(rows) == 1
    bars = rows[0]["bars"]
    assert len(bars) == 1
    assert 0 < bars[0]["left_pct"] < 100
    assert bars[0]["leg_code"] == "L1"


def test_build_gantt_rows_skips_out_of_window_leg() -> None:
    ws = datetime(2027, 1, 1, tzinfo=UTC)
    we = datetime(2027, 12, 31, 23, 59, tzinfo=UTC)
    vessel = SimpleNamespace(id=10, code="C", name="Anemos")
    leg = _leg(1, 10, 1, 2, datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 7, 15, tzinfo=UTC))
    rows = build_gantt_rows(vessels=[vessel], legs=[leg], window_start=ws, window_end=we, ports={})
    assert rows[0]["bars"] == []


def test_build_gantt_rows_fallback_label_uses_id() -> None:
    ws = datetime(2027, 1, 1, tzinfo=UTC)
    we = datetime(2027, 12, 31, 23, 59, tzinfo=UTC)
    vessel = SimpleNamespace(id=10, code="C", name="Anemos")
    leg = _leg(7, 10, 1, 2, datetime(2027, 7, 1, tzinfo=UTC), datetime(2027, 7, 15, tzinfo=UTC))
    rows = build_gantt_rows(vessels=[vessel], legs=[leg], window_start=ws, window_end=we, ports={})
    assert rows[0]["bars"][0]["leg_code"] == "#7"


# ── Avertissements souples ─────────────────────────────────────────────────


def test_scenario_warnings_flags_continuity_break() -> None:
    a = _leg(1, 10, 1, 2, datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 1, 10, tzinfo=UTC), label="A")
    # b part du port 3 alors que a arrive au port 2 → rupture de continuité
    b = _leg(2, 10, 3, 4, datetime(2027, 1, 20, tzinfo=UTC), datetime(2027, 1, 30, tzinfo=UTC), label="B")
    warns = scenario_warnings([a, b], ports={})
    assert any("continuité" in w for w in warns)


def test_scenario_warnings_flags_overlap() -> None:
    a = _leg(1, 10, 1, 2, datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 1, 20, tzinfo=UTC), label="A")
    # b démarre avant la fin de a sur le même navire → chevauchement
    b = _leg(2, 10, 2, 3, datetime(2027, 1, 10, tzinfo=UTC), datetime(2027, 1, 25, tzinfo=UTC), label="B")
    warns = scenario_warnings([a, b], ports={})
    assert any("chevauchement" in w for w in warns)


def test_scenario_warnings_clean_chain_is_silent() -> None:
    a = _leg(1, 10, 1, 2, datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 1, 10, tzinfo=UTC), label="A")
    b = _leg(2, 10, 2, 3, datetime(2027, 1, 15, tzinfo=UTC), datetime(2027, 1, 25, tzinfo=UTC), label="B")
    assert scenario_warnings([a, b], ports={}) == []


# ── Comparaison + CSV ──────────────────────────────────────────────────────


def test_sea_days_sums_durations() -> None:
    a = _leg(1, 10, 1, 2, datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 1, 3, tzinfo=UTC))
    b = _leg(2, 10, 2, 3, datetime(2027, 1, 5, tzinfo=UTC), datetime(2027, 1, 6, tzinfo=UTC))
    assert _sea_days([a, b]) == 3.0


def test_to_csv_has_header_and_rows() -> None:
    scenario = SimpleNamespace(id=1, name="Test")
    vessel = SimpleNamespace(id=10, code="C", name="Anemos")
    port_a = SimpleNamespace(id=1, locode="FRFEC", name="Fécamp")
    port_b = SimpleNamespace(id=2, locode="BRSSO", name="São Sebastião")
    leg = _leg(1, 10, 1, 2, datetime(2027, 1, 1, 8, 0, tzinfo=UTC), datetime(2027, 1, 15, 8, 0, tzinfo=UTC), label="L1")
    csv_text = to_csv(scenario, [leg], {10: vessel}, {1: port_a, 2: port_b})
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("leg;navire;POL;POD")
    assert "L1;C;FRFEC;BRSSO" in lines[1]
