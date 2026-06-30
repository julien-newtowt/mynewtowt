"""Tests unitaires des calculs CO₂ purs + détection d'appareil (socle P0).

``co2.estimate`` / ``co2.co2_equivalences`` (sans DB) alimentent devis,
certificats et storytelling RSE ; ``device_detection`` calcule l'empreinte
d'appareil (sécurité). Aucune I/O ⇒ testables unitairement.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import co2
from app.services import device_detection as dd


# ───────────────────────── co2.estimate ─────────────────────────
def test_estimate_default_factors_known_values() -> None:
    e = co2.estimate(distance_nm=Decimal("1000"), tonnage_t=Decimal("100"))
    assert e.distance_km == Decimal("1852.00")  # 1000 NM × 1.852
    assert e.towt_co2_kg == Decimal("277.800")  # facteur vélique 1,5 g/t·km
    assert e.conventional_co2_kg == Decimal("2537.240")  # référence 13,7 g/t·km
    assert e.avoided_co2_kg == Decimal("2259.440")
    assert e.avoided_co2_kg == e.conventional_co2_kg - e.towt_co2_kg
    assert e.avoidance_pct == Decimal("89.1")


def test_estimate_zero_distance_is_zero_and_pct_safe() -> None:
    e = co2.estimate(distance_nm=Decimal("0"), tonnage_t=Decimal("100"))
    assert e.conventional_co2_kg == 0
    assert e.avoided_co2_kg == 0
    assert e.avoidance_pct == Decimal("0")  # pas de division par zéro


def test_estimate_custom_factors_override_defaults() -> None:
    from app.services.co2 import Co2Factors

    f = Co2Factors(
        towt_ef_g_tkm=Decimal("2"), conventional_ef_g_tkm=Decimal("12"), source_version=None
    )
    e = co2.estimate(distance_nm=Decimal("1000"), tonnage_t=Decimal("100"), factors=f)
    # tkm = 185200 ; towt = 185200*2/1000 ; conv = 185200*12/1000
    assert e.towt_co2_kg == Decimal("370.400")
    assert e.conventional_co2_kg == Decimal("2222.400")


# ───────────────────────── co2.co2_equivalences ─────────────────────────
def test_co2_equivalences_known() -> None:
    eq = co2.co2_equivalences(525_000)  # 525 t évitées = exactement 1 vol Paris-NYC
    assert eq["avoided_t"] == Decimal("525.00")
    assert eq["flights_paris_nyc"] == Decimal("1.00")
    assert eq["containers_asia_eu"] == Decimal("210.0")


def test_co2_equivalences_none_is_zero() -> None:
    eq = co2.co2_equivalences(None)
    assert eq["avoided_t"] == Decimal("0.00")
    assert eq["flights_paris_nyc"] == Decimal("0.00")
    assert eq["containers_asia_eu"] == Decimal("0.0")


# ───────────────────────── device_detection (pur) ─────────────────────────
def test_ip_prefix_v4_v6_and_invalid() -> None:
    assert dd._ip_prefix("192.168.1.42") == "192.168.1"
    assert dd._ip_prefix("2001:db8:cafe::1") == "2001:0db8:cafe"
    assert dd._ip_prefix(None) == ""
    assert dd._ip_prefix("not-an-ip") == ""


@pytest.mark.parametrize(
    "ua,expected",
    [
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537", "Chrome / Windows"),
        ("Mozilla/5.0 (Macintosh) Version/17 Safari/605", "Safari / macOS"),
        ("Mozilla/5.0 (iPhone) Safari/604", "Safari / iOS"),
        ("curl/8.4.0", "curl / OS inconnu"),
        ("", "Inconnu"),
        (None, "Inconnu"),
    ],
)
def test_human_label(ua, expected) -> None:
    assert dd._human_label(ua) == expected


def test_fingerprint_is_stable_and_discriminating() -> None:
    a = dd.compute_fingerprint(ua="Chrome/120", ip="192.168.1.42")
    again = dd.compute_fingerprint(ua="Chrome/120", ip="192.168.1.99")  # même /24
    other = dd.compute_fingerprint(ua="Firefox/121", ip="192.168.1.42")
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)
    assert a == again  # l'empreinte ne dépend que du préfixe /24
    assert a != other  # un UA différent change l'empreinte
