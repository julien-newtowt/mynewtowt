"""Tests Noon Report — alignement formulaire officiel TOWT (CFOTE_05)."""

from __future__ import annotations

from datetime import UTC

from app.models.noon_report import (
    NOON_ENGINES,
    NOON_TIME_SLOTS,
    NoonReport,
)
from app.routers.onboard_router import (
    _attach_noon_children,
    _maybe_bool,
    _maybe_dt,
)


def test_constants():
    assert len(NOON_ENGINES) == 6
    assert NOON_ENGINES[0] == "Port Main Engine"
    assert len(NOON_TIME_SLOTS) == 6
    assert NOON_TIME_SLOTS[0] == "16:00"


def test_maybe_bool():
    assert _maybe_bool("1") is True
    assert _maybe_bool("on") is True
    assert _maybe_bool(None) is False
    assert _maybe_bool("") is False


def test_maybe_dt():
    dt = _maybe_dt("2026-01-14T08:30")
    assert dt is not None
    assert dt.tzinfo == UTC
    assert dt.year == 2026 and dt.hour == 8
    assert _maybe_dt("") is None
    assert _maybe_dt("pas une date") is None


def test_attach_children_skips_empty_rows():
    nr = NoonReport(leg_id=1)
    # Un seul moteur, un seul créneau météo, un seul créneau voilure remplis.
    form = {
        "eng_rh_0": "19",
        "eng_do_0": "0.49",
        "w_tws_0": "10",
        "w_spd_0": "5.1",
        "s_j0_0": "1",
        "s_boost_0": "27",
    }
    _attach_noon_children(nr, form)
    assert len(nr.engines) == 1
    assert nr.engines[0].engine == "Port Main Engine"
    assert nr.engines[0].running_hours_h == 19
    assert len(nr.weather_rows) == 1
    assert nr.weather_rows[0].slot_time == "16:00"
    assert nr.weather_rows[0].tws_kn == 10
    assert len(nr.sail_rows) == 1
    assert nr.sail_rows[0].j0 is True
    assert nr.sail_rows[0].sail_boost == 27


def test_attach_children_empty_form_creates_nothing():
    nr = NoonReport(leg_id=1)
    _attach_noon_children(nr, {})
    assert nr.engines == []
    assert nr.weather_rows == []
    assert nr.sail_rows == []
