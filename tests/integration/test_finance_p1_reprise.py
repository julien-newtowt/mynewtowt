"""Finance/KPI P1 — reprise (FIN-05 équivalences CO₂)."""

from __future__ import annotations

from decimal import Decimal


def test_co2_equivalences():
    from app.services.co2 import co2_equivalences

    # 1 050 000 kg = 1050 t évités → 2 vols Paris-NYC (525 t), 420 conteneurs (2.5 t).
    eq = co2_equivalences(1_050_000)
    assert eq["avoided_t"] == Decimal("1050.00")
    assert eq["flights_paris_nyc"] == Decimal("2.00")
    assert eq["containers_asia_eu"] == Decimal("420.0")


def test_co2_equivalences_zero_and_none():
    from app.services.co2 import co2_equivalences

    for v in (0, None):
        eq = co2_equivalences(v)
        assert eq["flights_paris_nyc"] == Decimal("0.00")
        assert eq["containers_asia_eu"] == Decimal("0.0")
