"""Tests for app.utils.timezones."""
from __future__ import annotations

from datetime import datetime, timezone

from app.utils.timezones import (
    TIMEZONE_CHOICES, from_utc, resolve_tz, to_utc, utc_offset_label,
)


def test_resolve_known_tz():
    z = resolve_tz("Europe/Paris")
    assert z.key == "Europe/Paris"


def test_resolve_unknown_tz_falls_back_to_utc():
    z = resolve_tz("Fictional/Atlantis")
    assert z.key == "UTC"


def test_resolve_none_returns_utc():
    z = resolve_tz(None)
    assert z.key == "UTC"


def test_utc_offset_label_for_utc():
    at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert utc_offset_label("UTC", at) == "+00:00"


def test_utc_offset_label_paris_summer():
    # July → CEST = +02:00
    at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert utc_offset_label("Europe/Paris", at) == "+02:00"


def test_utc_offset_label_paris_winter():
    # January → CET = +01:00
    at = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert utc_offset_label("Europe/Paris", at) == "+01:00"


def test_to_utc_from_paris():
    naive = datetime(2026, 7, 15, 14, 0)
    utc = to_utc(naive, "Europe/Paris")
    # 14:00 Paris in summer (UTC+2) = 12:00 UTC
    assert utc.hour == 12
    assert utc.tzinfo == timezone.utc


def test_from_utc_to_paris():
    utc_dt = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    paris = from_utc(utc_dt, "Europe/Paris")
    assert paris.hour == 14  # +02:00 in July


def test_timezone_choices_contain_port_local():
    keys = [k for k, _ in TIMEZONE_CHOICES]
    assert "port_local" in keys
    assert "UTC" in keys
