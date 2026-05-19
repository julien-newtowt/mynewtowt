"""Tests du service de signature SOF / noon / watch."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.signature import (
    compute_noon_hash,
    compute_sof_hash,
    compute_watch_hash,
    ensure_unlocked,
    sign_record,
    verify_hash,
)


def _fake_user(uid=1, name="Capt. Doe"):
    return SimpleNamespace(id=uid, full_name=name, username="cdoe")


def _make_sof():
    return SimpleNamespace(
        event_type="EOSP", occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        label="End of sea passage", latitude=49.5, longitude=-2.1, notes=None,
        signed_at=None, signed_by_id=None, signed_by_name=None,
        signature_hash=None, is_locked=False,
    )


def test_compute_hash_deterministic():
    e1 = _make_sof()
    e2 = _make_sof()
    assert compute_sof_hash(e1) == compute_sof_hash(e2)


def test_compute_hash_changes_when_content_changes():
    e1 = _make_sof()
    h1 = compute_sof_hash(e1)
    e1.label = "End of sea passage — modified"
    assert compute_sof_hash(e1) != h1


def test_sign_record_locks_and_hashes():
    e = _make_sof()
    u = _fake_user()
    sign_record(e, u, hash_fn=compute_sof_hash)
    assert e.is_locked is True
    assert e.signed_by_id == 1
    assert e.signed_by_name == "Capt. Doe"
    assert e.signature_hash is not None
    assert len(e.signature_hash) == 64  # SHA-256 hex


def test_sign_record_rejects_already_signed():
    from fastapi import HTTPException
    e = _make_sof()
    sign_record(e, _fake_user(), hash_fn=compute_sof_hash)
    with pytest.raises(HTTPException) as exc:
        sign_record(e, _fake_user(), hash_fn=compute_sof_hash)
    assert exc.value.status_code == 409


def test_ensure_unlocked_passes_when_unlocked():
    e = _make_sof()
    ensure_unlocked(e)  # no raise


def test_ensure_unlocked_raises_when_locked():
    from fastapi import HTTPException
    e = _make_sof()
    sign_record(e, _fake_user(), hash_fn=compute_sof_hash)
    with pytest.raises(HTTPException) as exc:
        ensure_unlocked(e)
    assert exc.value.status_code == 409


def test_verify_hash_detects_tampering():
    e = _make_sof()
    sign_record(e, _fake_user(), hash_fn=compute_sof_hash)
    assert verify_hash(e, hash_fn=compute_sof_hash) is True
    # Quelqu'un modifie le label après signature (en bypass-ant l'API)
    e.label = "Tampered label"
    assert verify_hash(e, hash_fn=compute_sof_hash) is False


def test_noon_hash_independent_from_sof():
    n = SimpleNamespace(
        leg_id=1, recorded_at=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        latitude=48.0, longitude=-5.0, sog_avg=8.5, cog_avg=270,
        wind_speed_kn=15, wind_direction_deg=200, distance_24h_nm=200,
        rob_fuel_l=1200, fuel_consumed_24h_l=80, remarks=None,
        signed_at=None, signed_by_id=None,
    )
    h = compute_noon_hash(n)
    assert isinstance(h, str) and len(h) == 64


def test_watch_hash_basic():
    from datetime import date
    w = SimpleNamespace(
        leg_id=1, watch_date=date(2026, 5, 19), watch_period="08-12",
        officer_on_watch="Lt. Smith", entry="All clear, course 270.",
        weather_summary="Wind WSW 12 kn", signed_at=None, signed_by_id=None,
    )
    h = compute_watch_hash(w)
    assert isinstance(h, str) and len(h) == 64
