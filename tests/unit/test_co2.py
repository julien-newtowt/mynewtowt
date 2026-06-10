"""CO2 calculation tests."""
from __future__ import annotations

from decimal import Decimal

from app.services.co2 import estimate


def test_typical_voyage() -> None:
    # 12 palettes × 0.1 t = 1.2 t on Fécamp → NYC (3200 NM)
    e = estimate(distance_nm=Decimal("3200"), tonnage_t=Decimal("1.2"))
    assert e.distance_km == (Decimal("3200") * Decimal("1.852")).quantize(Decimal("0.01"))
    # towt = 1.5 g/tkm = ~10.6 kg
    # conventional = 13.7 g/tkm = ~97.3 kg
    # avoided = ~86.7 kg
    assert e.towt_co2_kg < e.conventional_co2_kg
    assert e.avoided_co2_kg > 0
    assert e.avoidance_pct > Decimal("85")


def test_zero_distance() -> None:
    e = estimate(distance_nm=Decimal("0"), tonnage_t=Decimal("1"))
    assert e.towt_co2_kg == Decimal("0.000")
    assert e.conventional_co2_kg == Decimal("0.000")
    assert e.avoided_co2_kg == Decimal("0.000")


def test_zero_tonnage() -> None:
    e = estimate(distance_nm=Decimal("1000"), tonnage_t=Decimal("0"))
    assert e.avoided_co2_kg == Decimal("0.000")
