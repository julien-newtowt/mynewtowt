"""Règles de gestion planification (audit 2026-07) — logique pure.

Couvre : parsing tz-aware des formulaires, machine à états des statuts,
simulation de cascade (2 passes), avertissements de continuité.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.planning import (
    InvalidLegDates,
    LegOverlap,
    continuity_warnings,
    ensure_utc,
    parse_form_datetime,
    plan_downstream_shifts,
    refresh_leg_status,
)

# ───────────────────────── parse_form_datetime ─────────────────────────


def test_parse_form_datetime_is_always_aware_utc() -> None:
    dt = parse_form_datetime("2026-07-03T10:00")
    assert dt is not None and dt.tzinfo is not None
    assert dt == datetime(2026, 7, 3, 10, 0, tzinfo=UTC)


def test_parse_form_datetime_rejects_garbage() -> None:
    with pytest.raises(InvalidLegDates):
        parse_form_datetime("not-a-date")
    with pytest.raises(InvalidLegDates):
        parse_form_datetime("")
    assert parse_form_datetime("", allow_empty=True) is None


def test_ensure_utc_mixes_naive_and_aware_safely() -> None:
    naive = datetime(2026, 7, 3, 10, 0)
    aware = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
    # Le bug historique : naive - aware → TypeError. ensure_utc l'élimine.
    delta = ensure_utc(naive) - ensure_utc(aware)
    assert delta == timedelta(hours=2)


# ───────────────────────── refresh_leg_status ──────────────────────────


def _leg(status="planned", atd=None, ata=None, closure_approved_at=None):
    return SimpleNamespace(status=status, atd=atd, ata=ata, closure_approved_at=closure_approved_at)


def test_status_planned_without_reality() -> None:
    leg = _leg(status="in_progress")  # valeur incohérente héritée
    assert refresh_leg_status(leg) == "planned"


def test_status_in_progress_from_atd_or_ata() -> None:
    now = datetime.now(UTC)
    assert refresh_leg_status(_leg(atd=now)) == "in_progress"
    assert refresh_leg_status(_leg(ata=now)) == "in_progress"


def test_status_completed_only_on_closure_approval() -> None:
    now = datetime.now(UTC)
    leg = _leg(atd=now, ata=now)
    assert refresh_leg_status(leg) == "in_progress"
    leg.closure_approved_at = now
    assert refresh_leg_status(leg) == "completed"


def test_status_cancelled_is_sticky() -> None:
    leg = _leg(status="cancelled", atd=datetime.now(UTC))
    assert refresh_leg_status(leg) == "cancelled"


# ──────────────────────── plan_downstream_shifts ────────────────────────


def _lane_leg(id, etd, eta, atd=None, code=None):
    return SimpleNamespace(id=id, etd=etd, eta=eta, atd=atd, leg_code=code or f"L{id}")


BASE = datetime(2026, 3, 1, tzinfo=UTC)


def test_rigid_shift_preserves_relative_schedule() -> None:
    dn = [
        _lane_leg(2, BASE + timedelta(days=30), BASE + timedelta(days=50)),
        _lane_leg(3, BASE + timedelta(days=60), BASE + timedelta(days=80)),
    ]
    pos = plan_downstream_shifts(dn, delta=timedelta(days=5), source_eta=BASE + timedelta(days=25))
    assert pos[2] == (BASE + timedelta(days=35), BASE + timedelta(days=55))
    assert pos[3] == (BASE + timedelta(days=65), BASE + timedelta(days=85))


def test_eta_extension_pushes_downstream_without_etd_delta() -> None:
    """Pur allongement d'ETA (delta ETD nul) → le leg suivant est repoussé."""
    dn = [_lane_leg(2, BASE + timedelta(days=20), BASE + timedelta(days=40))]
    pos = plan_downstream_shifts(dn, delta=timedelta(0), source_eta=BASE + timedelta(days=25))
    # ETD repoussé à la fin du leg source, durée conservée (20 j).
    assert pos[2] == (BASE + timedelta(days=25), BASE + timedelta(days=45))


def test_push_chains_across_multiple_legs() -> None:
    dn = [
        _lane_leg(2, BASE + timedelta(days=20), BASE + timedelta(days=40)),
        _lane_leg(3, BASE + timedelta(days=41), BASE + timedelta(days=60)),
    ]
    pos = plan_downstream_shifts(dn, delta=timedelta(0), source_eta=BASE + timedelta(days=30))
    assert pos[2] == (BASE + timedelta(days=30), BASE + timedelta(days=50))
    # Le push se propage : leg 3 démarrait avant la nouvelle fin du leg 2.
    assert pos[3] == (BASE + timedelta(days=50), BASE + timedelta(days=69))


def test_sailed_leg_never_moves_and_blocks_resolution() -> None:
    """RÈGLE D'OR : un leg déjà appareillé (ATD posé) ne bouge jamais."""
    sailed = _lane_leg(
        2, BASE + timedelta(days=20), BASE + timedelta(days=40), atd=BASE + timedelta(days=20)
    )
    pos = plan_downstream_shifts(
        [sailed], delta=timedelta(days=5), source_eta=BASE + timedelta(days=15)
    )
    assert pos[2] == (BASE + timedelta(days=20), BASE + timedelta(days=40))  # immobile
    with pytest.raises(LegOverlap):
        plan_downstream_shifts([sailed], delta=timedelta(0), source_eta=BASE + timedelta(days=25))


# ───────────────────────── continuity_warnings ──────────────────────────


def test_continuity_warnings_detects_hole() -> None:
    ports = {
        1: SimpleNamespace(locode="FRFEC"),
        2: SimpleNamespace(locode="BRSSO"),
        3: SimpleNamespace(locode="USNYC"),
    }
    legs = [
        SimpleNamespace(
            vessel_id=1,
            status="planned",
            leg_code="1AFRBR6",
            departure_port_id=1,
            arrival_port_id=2,
            etd=BASE,
            eta=BASE + timedelta(days=20),
        ),
        SimpleNamespace(
            vessel_id=1,
            status="planned",
            leg_code="1BUSFR6",
            departure_port_id=3,
            arrival_port_id=1,  # part de USNYC ≠ BRSSO
            etd=BASE + timedelta(days=30),
            eta=BASE + timedelta(days=50),
        ),
    ]
    warnings = continuity_warnings(legs, ports)
    assert len(warnings) == 1
    assert "1AFRBR6" in warnings[0] and "1BUSFR6" in warnings[0]


def test_continuity_warnings_ignores_cancelled() -> None:
    ports = {1: SimpleNamespace(locode="FRFEC"), 2: SimpleNamespace(locode="BRSSO")}
    legs = [
        SimpleNamespace(
            vessel_id=1,
            status="cancelled",
            leg_code="X",
            departure_port_id=2,
            arrival_port_id=2,
            etd=BASE,
            eta=BASE + timedelta(days=5),
        ),
        SimpleNamespace(
            vessel_id=1,
            status="planned",
            leg_code="1AFRBR6",
            departure_port_id=1,
            arrival_port_id=2,
            etd=BASE + timedelta(days=10),
            eta=BASE + timedelta(days=30),
        ),
    ]
    assert continuity_warnings(legs, ports) == []
