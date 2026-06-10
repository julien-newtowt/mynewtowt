"""CO2 calculations + certificate issuance.

Uses NEWTOWT default emission factors from the V2 admin parameters:
- towt_co2_ef = 1.5 gCO2/t.km
- conventional_co2_ef = 13.7 gCO2/t.km
- nm_to_km = 1.852
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

TOWT_CO2_EF_G_PER_TKM = Decimal("1.5")
CONV_CO2_EF_G_PER_TKM = Decimal("13.7")
NM_TO_KM = Decimal("1.852")


@dataclass(frozen=True)
class EmissionEstimate:
    distance_nm: Decimal
    distance_km: Decimal
    tonnage_t: Decimal
    towt_co2_kg: Decimal
    conventional_co2_kg: Decimal
    avoided_co2_kg: Decimal

    @property
    def avoidance_pct(self) -> Decimal:
        if self.conventional_co2_kg == 0:
            return Decimal("0")
        return (Decimal("100") * self.avoided_co2_kg / self.conventional_co2_kg).quantize(
            Decimal("0.1")
        )


def estimate(*, distance_nm: Decimal, tonnage_t: Decimal) -> EmissionEstimate:
    """Pure estimation — no DB. Used both for booking quotes and certificates."""
    distance_km = (distance_nm * NM_TO_KM).quantize(Decimal("0.01"))
    tkm = (distance_km * tonnage_t).quantize(Decimal("0.01"))
    towt_kg = (tkm * TOWT_CO2_EF_G_PER_TKM / Decimal("1000")).quantize(Decimal("0.001"))
    conv_kg = (tkm * CONV_CO2_EF_G_PER_TKM / Decimal("1000")).quantize(Decimal("0.001"))
    avoided = (conv_kg - towt_kg).quantize(Decimal("0.001"))
    return EmissionEstimate(
        distance_nm=distance_nm,
        distance_km=distance_km,
        tonnage_t=tonnage_t,
        towt_co2_kg=towt_kg,
        conventional_co2_kg=conv_kg,
        avoided_co2_kg=avoided,
    )
