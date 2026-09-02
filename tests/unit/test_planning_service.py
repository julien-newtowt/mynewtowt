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
    audit_planning_sequence,
    detect_port_conflicts,
    plan_downstream_shifts,
    schedule_kpis,
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
    # Format officiel NEWTOWT :
    # {code navire 1 chiffre}{rang année 1 lettre}{POL}{POD}{chiffre année}
    # Ex. métier : 3e voyage 2026 du navire 1 (Anemos), France → Brésil.
    code = _leg_code_for("1", "FR", "BR", etd, 3)
    assert code == "1CFRBR6"


def test_leg_code_first_of_year_is_letter_a() -> None:
    etd = datetime(2026, 6, 4, tzinfo=UTC)
    assert _leg_code_for("1", "FR", "BR", etd) == "1AFRBR6"


def test_leg_code_sequence_bump() -> None:
    etd = datetime(2026, 6, 4, tzinfo=UTC)
    code_2 = _leg_code_for("2", "FR", "US", etd, 2)
    assert code_2 == "2BFRUS6"


def test_leg_code_rank_out_of_range() -> None:
    from app.services.planning import PlanningError, rank_letter

    assert rank_letter(26) == "Z"
    with pytest.raises(PlanningError):
        rank_letter(27)
    with pytest.raises(PlanningError):
        rank_letter(0)


def _leg_ns(id, vessel_id, arrival_port_id, eta, stay=24):
    return SimpleNamespace(
        id=id,
        vessel_id=vessel_id,
        arrival_port_id=arrival_port_id,
        eta=eta,
        port_stay_planned_hours=stay,
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
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=4)  # [0h, 4h]
    leg_b = _leg_ns(2, 11, 42, base_eta + timedelta(hours=20), stay=4)  # [20h, 24h]
    assert detect_port_conflicts([leg_a, leg_b]) == []


def test_detect_port_conflicts_overlapping_long_stays() -> None:
    """REGRESSION fix: ETA 20h apart but long stays overlap → conflict.

    L'ancienne heuristique ETA±12h ratait ce cas réel.
    """
    base_eta = datetime(2026, 6, 12, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=48)  # [0h, 48h]
    leg_b = _leg_ns(2, 11, 42, base_eta + timedelta(hours=20), stay=24)  # [20h, 44h]
    assert detect_port_conflicts([leg_a, leg_b]) == [(1, 2)]


def test_detect_port_conflicts_ignores_different_ports() -> None:
    base_eta = datetime(2026, 6, 12, tzinfo=UTC)
    leg_a = _leg_ns(1, 10, 42, base_eta, stay=24)
    leg_b = _leg_ns(2, 11, 43, base_eta, stay=24)
    assert detect_port_conflicts([leg_a, leg_b]) == []


def _planning_leg(
    id,
    vessel_id,
    dep,
    arr,
    etd,
    eta,
    *,
    code=None,
    stay=24,
    status="planned",
    distance=100,
):
    return SimpleNamespace(
        id=id,
        vessel_id=vessel_id,
        departure_port_id=dep,
        arrival_port_id=arr,
        etd=etd,
        eta=eta,
        etd_ref=etd,
        eta_ref=eta,
        atd=None,
        ata=None,
        leg_code=code or f"L{id}",
        port_stay_planned_hours=stay,
        status=status,
        distance_nm=distance,
    )


def test_audit_flags_leg_before_previous_port_stay_finishes() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    a = _planning_leg(1, 1, 10, 20, base, base + timedelta(days=5), stay=72, code="1AFRBR6")
    b = _planning_leg(
        2,
        1,
        20,
        30,
        base + timedelta(days=6),
        base + timedelta(days=10),
        code="1BBRUS6",
    )
    issues = audit_planning_sequence([a, b])
    assert any(issue.code == "port_stay_overlap" for issue in issues)
    kpis = schedule_kpis([a, b], issues)
    assert kpis.critical_issues == 1
    assert kpis.calendar_respect_pct == 50.0


def test_audit_overlap_message_is_explicit() -> None:
    """Le message doit porter les CHIFFRES qui expliquent l'alerte.

    « démarre avant la fin de l'escale prévue » ne dit ni quand, ni combien,
    ni quoi corriger — retour utilisateur du 2026-09-02 : « je ne comprends
    pas l'erreur ».
    """
    base = datetime(2026, 10, 6, tzinfo=UTC)
    a = _planning_leg(1, 1, 10, 20, base, base + timedelta(days=30), stay=120, code="3CBRFR6")
    b = _planning_leg(
        2,
        1,
        20,
        30,
        base + timedelta(days=32),
        base + timedelta(days=63),
        code="3DFRBR6",
    )
    vessel = SimpleNamespace(id=1, name="Atlantis")
    issues = audit_planning_sequence([a, b], vessels={1: vessel})
    overlap = next(i for i in issues if i.code == "port_stay_overlap")
    for fragment in (
        "Atlantis",
        "3CBRFR6 arrive le 05/11/2026",
        "escale planifiée de 5 j",
        "jusqu'au 10/11/2026",
        "3DFRBR6 repart dès le 07/11/2026",
        "il manque 3 j",
        "Réduisez l'escale",
    ):
        assert fragment in overlap.message, f"{fragment!r} absent de : {overlap.message}"


def test_audit_ignores_overlap_on_a_leg_already_at_sea() -> None:
    """Un leg déjà appareillé est un fait : son ATD ne se corrige plus.

    L'auditer produisait une alerte critique que personne ne pouvait résoudre.
    """
    base = datetime(2026, 10, 6, tzinfo=UTC)
    a = _planning_leg(1, 1, 10, 20, base, base + timedelta(days=30), stay=120, code="3CBRFR6")
    b = _planning_leg(
        2, 1, 20, 30, base + timedelta(days=32), base + timedelta(days=63), code="3DFRBR6"
    )
    b.atd = base + timedelta(days=32)  # départ déclaré
    issues = audit_planning_sequence([a, b])
    assert not any(i.code == "port_stay_overlap" for i in issues)


def test_audit_overlap_uses_real_arrival_when_known() -> None:
    """ATA connue → l'escale se mesure sur le réel, pas sur l'ETA périmée."""
    base = datetime(2026, 10, 6, tzinfo=UTC)
    a = _planning_leg(1, 1, 10, 20, base, base + timedelta(days=30), stay=48, code="3CBRFR6")
    a.ata = base + timedelta(days=34)  # arrivée réelle en retard de 4 j
    b = _planning_leg(
        2, 1, 20, 30, base + timedelta(days=33), base + timedelta(days=63), code="3DFRBR6"
    )
    issues = audit_planning_sequence([a, b])
    overlap = next(i for i in issues if i.code == "port_stay_overlap")
    assert "(ATA)" in overlap.message


def test_audit_names_the_port_missing_coordinates() -> None:
    """Distance absente : dire POURQUOI (le port n'a pas de coordonnées)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    leg = _planning_leg(1, 1, 10, 20, base, base + timedelta(days=5), distance=None)
    ports = {
        10: SimpleNamespace(id=10, locode="FRFEC", latitude=49.75, longitude=0.37),
        20: SimpleNamespace(id=20, locode="REPDG", latitude=None, longitude=None),
    }
    issues = audit_planning_sequence([leg], ports=ports)
    missing = next(i for i in issues if i.code == "distance_missing")
    assert "REPDG" in missing.message
    assert "FRFEC" not in missing.message
    assert "Admin → Ports" in missing.message


def test_plan_downstream_shifts_uses_source_availability() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    downstream = [_planning_leg(2, 1, 20, 30, base + timedelta(days=6), base + timedelta(days=10))]
    planned = plan_downstream_shifts(
        downstream,
        delta=timedelta(0),
        source_eta=base + timedelta(days=7),
    )
    assert planned[2][0] == base + timedelta(days=7)
    assert planned[2][1] == base + timedelta(days=11)
