"""FIN-03 — émissions NOx / SOx évitées par la propulsion vélique.

Reprise V2 (``EmissionParameter`` + ``kpi_router``) : compare les facteurs
d'émission d'un navire conventionnel à ceux d'un voilier-cargo, et calcule la
masse de NOx / SOx **évitée** par leg ::

    évité = cargo_t × distance_nm × (facteur_conventionnel − facteur_voile)

Les facteurs (kg par tonne-mille nautique) sont paramétrables : on lit les
lignes courantes de ``co2_variables`` (mécanisme de versionnage commun aux
facteurs CO₂) et on retombe sur les constantes V2 si elles sont absentes.
Lecture seule, best-effort : toute erreur DB retombe sur les constantes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Constantes V2 (kg par tonne-mille nautique).
CONV_NOX_PER_TNM = Decimal("0.000406")
SAIL_NOX_PER_TNM = Decimal("0.0000528")
CONV_SOX_PER_TNM = Decimal("0.0000812")
SAIL_SOX_PER_TNM = Decimal("0.00001056")

# Noms de variables dans ``co2_variables`` (paramétrage admin).
NOX_CONV_VAR = "conventional_nox_per_tnm"
NOX_SAIL_VAR = "sail_nox_per_tnm"
SOX_CONV_VAR = "conventional_sox_per_tnm"
SOX_SAIL_VAR = "sail_sox_per_tnm"

_VAR_NAMES = (NOX_CONV_VAR, NOX_SAIL_VAR, SOX_CONV_VAR, SOX_SAIL_VAR)


@dataclass(frozen=True)
class EmissionFactors:
    conv_nox: Decimal
    sail_nox: Decimal
    conv_sox: Decimal
    sail_sox: Decimal


_DEFAULT_FACTORS = EmissionFactors(
    conv_nox=CONV_NOX_PER_TNM,
    sail_nox=SAIL_NOX_PER_TNM,
    conv_sox=CONV_SOX_PER_TNM,
    sail_sox=SAIL_SOX_PER_TNM,
)


@dataclass(frozen=True)
class EmissionResult:
    nox_conventional_kg: Decimal
    nox_sail_kg: Decimal
    nox_avoided_kg: Decimal
    sox_conventional_kg: Decimal
    sox_sail_kg: Decimal
    sox_avoided_kg: Decimal


async def get_emission_factors(db: AsyncSession) -> EmissionFactors:
    """Facteurs courants depuis ``co2_variables`` ; repli sur les constantes V2."""
    from app.models.co2_variable import Co2Variable

    values = {
        NOX_CONV_VAR: CONV_NOX_PER_TNM,
        NOX_SAIL_VAR: SAIL_NOX_PER_TNM,
        SOX_CONV_VAR: CONV_SOX_PER_TNM,
        SOX_SAIL_VAR: SAIL_SOX_PER_TNM,
    }
    try:
        rows = (
            (
                await db.execute(
                    select(Co2Variable).where(
                        Co2Variable.is_current.is_(True),
                        Co2Variable.name.in_(_VAR_NAMES),
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            values[r.name] = Decimal(r.value)
    except Exception:
        return _DEFAULT_FACTORS
    return EmissionFactors(
        conv_nox=values[NOX_CONV_VAR],
        sail_nox=values[NOX_SAIL_VAR],
        conv_sox=values[SOX_CONV_VAR],
        sail_sox=values[SOX_SAIL_VAR],
    )


def estimate_avoided(
    *,
    cargo_t: Decimal | float | None,
    distance_nm: Decimal | float | None,
    factors: EmissionFactors | None = None,
) -> EmissionResult:
    """Masses NOx / SOx conventionnelles, voile et évitées (kg) pour un leg.

    Sans cargo ou distance, tout est nul (rien à comparer).
    """
    f = factors or _DEFAULT_FACTORS
    cargo = Decimal(str(cargo_t)) if cargo_t else Decimal("0")
    dist = Decimal(str(distance_nm)) if distance_nm else Decimal("0")
    tnm = cargo * dist

    def _q(v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.001"))

    nox_conv = _q(tnm * f.conv_nox)
    nox_sail = _q(tnm * f.sail_nox)
    sox_conv = _q(tnm * f.conv_sox)
    sox_sail = _q(tnm * f.sail_sox)
    return EmissionResult(
        nox_conventional_kg=nox_conv,
        nox_sail_kg=nox_sail,
        nox_avoided_kg=_q(nox_conv - nox_sail),
        sox_conventional_kg=sox_conv,
        sox_sail_kg=sox_sail,
        sox_avoided_kg=_q(sox_conv - sox_sail),
    )
