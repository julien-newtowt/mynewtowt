"""Tests du fingerprinting device."""
from __future__ import annotations

from app.services.device_detection import (
    _human_label, _ip_prefix, compute_fingerprint,
)


def test_ip_prefix_v4_slash24():
    assert _ip_prefix("192.168.1.42") == "192.168.1"
    assert _ip_prefix("10.0.0.255") == "10.0.0"


def test_ip_prefix_v6_slash48():
    # IPv6 doc range — vérifier les 3 premiers groupes hex
    assert _ip_prefix("2001:db8:cafe:beef::1").startswith("2001:0db8:cafe")


def test_ip_prefix_invalid_returns_empty():
    assert _ip_prefix(None) == ""
    assert _ip_prefix("") == ""
    assert _ip_prefix("not an ip") == ""


def test_fingerprint_stable_same_inputs():
    a = compute_fingerprint(ua="Mozilla/5.0 Chrome/120", ip="192.168.1.42")
    b = compute_fingerprint(ua="Mozilla/5.0 Chrome/120", ip="192.168.1.42")
    assert a == b
    assert len(a) == 64


def test_fingerprint_tolerates_minor_ip_change():
    """Même /24 → même fingerprint (NAT mobile)."""
    a = compute_fingerprint(ua="Chrome", ip="192.168.1.10")
    b = compute_fingerprint(ua="Chrome", ip="192.168.1.99")
    assert a == b


def test_fingerprint_changes_with_different_subnet():
    a = compute_fingerprint(ua="Chrome", ip="192.168.1.10")
    b = compute_fingerprint(ua="Chrome", ip="10.0.0.10")
    assert a != b


def test_fingerprint_changes_with_different_browser():
    a = compute_fingerprint(ua="Mozilla/5.0 Chrome/120", ip="1.2.3.4")
    b = compute_fingerprint(ua="Mozilla/5.0 Firefox/120", ip="1.2.3.4")
    assert a != b


def test_fingerprint_normalizes_case():
    a = compute_fingerprint(ua="CHROME", ip="1.2.3.4")
    b = compute_fingerprint(ua="Chrome", ip="1.2.3.4")
    assert a == b


def test_human_label_known_combos():
    assert "Chrome" in _human_label("Mozilla/5.0 (Windows) Chrome/120")
    assert "Windows" in _human_label("Mozilla/5.0 (Windows NT 10.0) Chrome/120")
    assert "Safari" in _human_label("Mozilla/5.0 (Macintosh; Intel Mac OS X) Safari/17")
    assert "macOS" in _human_label("Mozilla/5.0 (Macintosh) Safari/17")
    assert "iOS" in _human_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)")


def test_human_label_falls_back_gracefully():
    assert _human_label(None) == "Inconnu"
    assert _human_label("") == "Inconnu"
    assert "OS inconnu" in _human_label("CustomBot/1.0")
