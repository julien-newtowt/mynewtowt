"""Tests unitaires des calculs purs sous-couverts (socle technique P0).

Cible des fonctions sans I/O : facteurs d'émissions évitées (NOx/SOx),
montants de facturation (TVA transport maritime international = 0 %) et
libellés de classes IMDG.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.services import invoicing
from app.services.emissions import EmissionFactors, estimate_avoided
from app.services.imdg import imdg_label


# ───────────────────────── emissions.estimate_avoided ─────────────────────────
def test_estimate_avoided_known_values() -> None:
    # cargo 100 t × 50 NM = 5000 t·NM avec les facteurs par défaut.
    r = estimate_avoided(cargo_t=100, distance_nm=50)
    assert r.nox_conventional_kg == Decimal("2.030")
    assert r.nox_sail_kg == Decimal("0.264")
    assert r.nox_avoided_kg == Decimal("1.766")
    assert r.sox_conventional_kg == Decimal("0.406")
    assert r.sox_avoided_kg == r.sox_conventional_kg - r.sox_sail_kg


def test_estimate_avoided_is_positive_and_consistent() -> None:
    r = estimate_avoided(cargo_t=Decimal("250.5"), distance_nm=Decimal("3200"))
    # La voile émet strictement moins que le conventionnel → évité > 0.
    assert r.nox_avoided_kg > 0 and r.sox_avoided_kg > 0
    assert r.nox_avoided_kg == r.nox_conventional_kg - r.nox_sail_kg
    assert r.sox_sail_kg < r.sox_conventional_kg


@pytest.mark.parametrize(
    "cargo,dist",
    [(None, 100), (100, None), (0, 100), (100, 0), (None, None)],
)
def test_estimate_avoided_zero_without_cargo_or_distance(cargo, dist) -> None:
    r = estimate_avoided(cargo_t=cargo, distance_nm=dist)
    assert r.nox_conventional_kg == 0
    assert r.nox_avoided_kg == 0
    assert r.sox_avoided_kg == 0


def test_estimate_avoided_custom_factors() -> None:
    factors = EmissionFactors(
        conv_nox=Decimal("0.001"),
        sail_nox=Decimal("0.0002"),
        conv_sox=Decimal("0.0005"),
        sail_sox=Decimal("0.0001"),
    )
    r = estimate_avoided(cargo_t=10, distance_nm=100, factors=factors)  # tnm = 1000
    assert r.nox_conventional_kg == Decimal("1.000")
    assert r.nox_avoided_kg == Decimal("0.800")
    assert r.sox_avoided_kg == Decimal("0.400")


# ───────────────────────── invoicing (pur) ─────────────────────────
def test_compute_amounts_vat_exempt() -> None:
    excl, vat, incl = invoicing.compute_amounts(Decimal("1234.5"))
    assert excl == Decimal("1234.50")
    assert vat == Decimal("0.00")  # transport maritime international exonéré
    assert incl == Decimal("1234.50")


def test_compute_amounts_none_is_zero() -> None:
    assert invoicing.compute_amounts(None) == (
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )


def test_generate_reference_format() -> None:
    ref = invoicing.generate_reference(2026)
    assert ref.startswith("INV-2026-")
    assert re.fullmatch(r"INV-2026-[0-9A-F]{6}", ref)
    # Deux appels → suffixes différents (token aléatoire).
    assert invoicing.generate_reference(2026) != invoicing.generate_reference(2026)


# ───────────────────────── imdg_label ─────────────────────────
def test_imdg_label_known_code() -> None:
    fr = imdg_label("3", "fr")
    en = imdg_label("3", "en")
    assert fr.startswith("3 — ") and "inflammables" in fr.lower()
    assert en.startswith("3 — ") and "flammable" in en.lower()


def test_imdg_label_unknown_returns_code_and_empty() -> None:
    assert imdg_label("99", "fr") == "99"
    assert imdg_label(None, "fr") == ""
    assert imdg_label("", "fr") == ""


def test_imdg_label_non_en_defaults_to_fr() -> None:
    assert imdg_label("3", "es") == imdg_label("3", "fr")
