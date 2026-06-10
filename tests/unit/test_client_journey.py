"""Pure-logic tests for the client-journey services (no DB)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services import anemos, invoicing


# --- invoicing ---------------------------------------------------------------

def test_compute_amounts_french_vat() -> None:
    excl, vat, incl = invoicing.compute_amounts(Decimal("100"))
    assert excl == Decimal("100.00")
    assert vat == Decimal("20.00")
    assert incl == Decimal("120.00")


def test_compute_amounts_rounds_to_cents() -> None:
    excl, vat, incl = invoicing.compute_amounts(Decimal("33.33"))
    assert vat == Decimal("6.67")  # 33.33 * 0.20 = 6.666 → 6.67
    assert incl == excl + vat


def test_invoice_reference_format() -> None:
    ref = invoicing.generate_reference(2026)
    assert ref.startswith("INV-2026-")
    assert len(ref) == len("INV-2026-") + 6  # token_hex(3) = 6 hex chars


# --- anemos distance resolver ------------------------------------------------

def test_resolve_distance_prefers_persisted_leg_value() -> None:
    leg = SimpleNamespace(distance_nm=Decimal("2750"))
    assert anemos.resolve_distance_nm(leg, None, None) == Decimal("2750")


def test_resolve_distance_haversine_from_coords() -> None:
    leg = SimpleNamespace(distance_nm=None)
    pol = SimpleNamespace(latitude=49.76, longitude=0.37, locode="FRFEC")
    pod = SimpleNamespace(latitude=40.71, longitude=-74.01, locode="USNYC")
    d = anemos.resolve_distance_nm(leg, pol, pod)
    assert 2800 < float(d) < 3400  # Fécamp → New York ~3060 nm


def test_resolve_distance_table_fallback_when_no_coords() -> None:
    leg = SimpleNamespace(distance_nm=None)
    pol = SimpleNamespace(latitude=None, longitude=None, locode="FRFEC")
    pod = SimpleNamespace(latitude=None, longitude=None, locode="USNYC")
    assert anemos.resolve_distance_nm(leg, pol, pod) == Decimal("3200")


def test_table_distance_default() -> None:
    assert anemos._table_distance("XXAAA", "YYBBB") == Decimal("3000")
    assert anemos._table_distance(None, None) == Decimal("3000")
