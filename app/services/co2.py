"""CO2 calculations + certificate issuance.

Uses NEWTOWT default emission factors from the V2 admin parameters:
- towt_co2_ef = 1.5 gCO2/t.km
- conventional_co2_ef = 13.7 gCO2/t.km
- nm_to_km = 1.852

Depuis ENV-02, les facteurs d'émission sont paramétrables et versionnés
en base (table ``co2_variables``, écran /admin/co2). Les constantes
ci-dessous restent les fallbacks documentés : elles s'appliquent tant que
la table est vide (ou inaccessible) et alimentent ``estimate()`` quand
aucun ``Co2Factors`` n'est fourni — les appelants historiques restent
donc inchangés.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

TOWT_CO2_EF_G_PER_TKM = Decimal("1.5")
CONV_CO2_EF_G_PER_TKM = Decimal("13.7")
NM_TO_KM = Decimal("1.852")

# Facteur d'émission CO₂ du MDO/DO consommé — source MEPC.391(81) : combustion
# de 1 g de DO → 3,206 gCO₂ (Carbon Report officiel CFOTE_09). Donc 1 t DO →
# 3,206 tCO₂.
DO_CO2_G_PER_G = Decimal("3.206")

# Noms des variables versionnées en base (cf. app.models.co2_variable).
TOWT_EF_VARIABLE = "towt_co2_ef"
CONV_EF_VARIABLE = "conventional_co2_ef"
DO_CO2_EF_VARIABLE = "do_co2_ef"


async def get_do_co2_factor(db: AsyncSession) -> Decimal:
    """Facteur CO₂ par tonne de DO (tCO₂/tDO) — DB versionnée → fallback.

    Lit la variable ``do_co2_ef`` (``co2_variables``, écran /admin/co2) si
    présente, sinon retombe sur la constante MEPC.391(81) (3,206). Lecture
    seule, tolérante (toute erreur DB → constante).
    """
    try:
        from app.models.co2_variable import Co2Variable

        row = (
            await db.execute(
                select(Co2Variable).where(
                    Co2Variable.is_current.is_(True),
                    Co2Variable.name == DO_CO2_EF_VARIABLE,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return Decimal(row.value)
    except Exception:
        pass
    return DO_CO2_G_PER_G


@dataclass(frozen=True)
class Co2Factors:
    """Facteurs d'émission effectifs (DB versionnée ou fallbacks codés)."""

    towt_ef_g_tkm: Decimal
    conventional_ef_g_tkm: Decimal
    source_version: str | None


_DEFAULT_FACTORS = Co2Factors(
    towt_ef_g_tkm=TOWT_CO2_EF_G_PER_TKM,
    conventional_ef_g_tkm=CONV_CO2_EF_G_PER_TKM,
    source_version=None,
)

# Cache module-level (TTL 60 s) — invalidé par /admin/co2/update.
_FACTORS_TTL_SECONDS = 60.0
_factors_cache: Co2Factors | None = None
_factors_loaded_at: float = 0.0


def invalidate_factors_cache() -> None:
    """Force la relecture DB au prochain ``get_factors()`` (post-update admin)."""
    global _factors_cache, _factors_loaded_at
    _factors_cache = None
    _factors_loaded_at = 0.0


async def get_factors(db: AsyncSession) -> Co2Factors:
    """Facteurs courants — lignes ``is_current`` de ``co2_variables``.

    Chemin lecture seule : aucune écriture DB ici. Variable absente (ou
    erreur DB, ex. table pas encore migrée) → fallback sur les constantes
    du module. Résultat mis en cache 60 s au niveau module.
    """
    global _factors_cache, _factors_loaded_at
    now = time.monotonic()
    if _factors_cache is not None and (now - _factors_loaded_at) < _FACTORS_TTL_SECONDS:
        return _factors_cache

    towt = TOWT_CO2_EF_G_PER_TKM
    conv = CONV_CO2_EF_G_PER_TKM
    version_parts: list[str] = []
    try:
        from app.models.co2_variable import Co2Variable

        rows = (
            (
                await db.execute(
                    select(Co2Variable).where(
                        Co2Variable.is_current.is_(True),
                        Co2Variable.name.in_((TOWT_EF_VARIABLE, CONV_EF_VARIABLE)),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_name = {r.name: r for r in rows}
        towt_row = by_name.get(TOWT_EF_VARIABLE)
        conv_row = by_name.get(CONV_EF_VARIABLE)
        if towt_row is not None:
            towt = Decimal(towt_row.value)
            version_parts.append(
                f"{TOWT_EF_VARIABLE}={towt} "
                f"({towt_row.source or 'admin'}, {towt_row.effective_date.isoformat()})"
            )
        if conv_row is not None:
            conv = Decimal(conv_row.value)
            version_parts.append(
                f"{CONV_EF_VARIABLE}={conv} "
                f"({conv_row.source or 'admin'}, {conv_row.effective_date.isoformat()})"
            )
    except Exception:
        # Lecture best-effort : toute erreur DB retombe sur les constantes
        # codées (et on cache ce fallback pour ne pas marteler la DB).
        towt = TOWT_CO2_EF_G_PER_TKM
        conv = CONV_CO2_EF_G_PER_TKM
        version_parts = []

    factors = Co2Factors(
        towt_ef_g_tkm=towt,
        conventional_ef_g_tkm=conv,
        source_version="; ".join(version_parts) or None,
    )
    _factors_cache = factors
    _factors_loaded_at = now
    return factors


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


def estimate(
    *,
    distance_nm: Decimal,
    tonnage_t: Decimal,
    factors: Co2Factors | None = None,
) -> EmissionEstimate:
    """Pure estimation — no DB. Used both for booking quotes and certificates.

    ``factors`` est optionnel : ``None`` → constantes du module (compat
    ascendante). Pour des facteurs versionnés, passer le résultat de
    ``await get_factors(db)``.
    """
    f = factors if factors is not None else _DEFAULT_FACTORS
    distance_km = (distance_nm * NM_TO_KM).quantize(Decimal("0.01"))
    tkm = (distance_km * tonnage_t).quantize(Decimal("0.01"))
    towt_kg = (tkm * f.towt_ef_g_tkm / Decimal("1000")).quantize(Decimal("0.001"))
    conv_kg = (tkm * f.conventional_ef_g_tkm / Decimal("1000")).quantize(Decimal("0.001"))
    avoided = (conv_kg - towt_kg).quantize(Decimal("0.001"))
    return EmissionEstimate(
        distance_nm=distance_nm,
        distance_km=distance_km,
        tonnage_t=tonnage_t,
        towt_co2_kg=towt_kg,
        conventional_co2_kg=conv_kg,
        avoided_co2_kg=avoided,
    )


# ───────────────────── FIN-05 — équivalences pédagogiques CO₂ ──────────────────
# Facteurs V2 (tonnes de CO₂ par unité) — storytelling RSE.
CO2_PER_FLIGHT_PARIS_NYC_T = Decimal("525")  # 1 vol Paris-NYC (~300 pax)
CO2_PER_CONTAINER_ASIA_EU_T = Decimal("2.5")  # 1 conteneur Asie→Europe (conventionnel)


def co2_equivalences(co2_avoided_kg: Decimal | float | int | None) -> dict:
    """Traduit une masse de CO₂ évité (kg) en équivalences pédagogiques.

    Renvoie le nombre de vols Paris-NYC et de conteneurs Asie-Europe évités.
    """
    avoided_t = Decimal(str(co2_avoided_kg or 0)) / Decimal("1000")
    flights = (avoided_t / CO2_PER_FLIGHT_PARIS_NYC_T) if CO2_PER_FLIGHT_PARIS_NYC_T else Decimal(0)
    containers = (
        (avoided_t / CO2_PER_CONTAINER_ASIA_EU_T) if CO2_PER_CONTAINER_ASIA_EU_T else Decimal(0)
    )
    return {
        "avoided_t": avoided_t.quantize(Decimal("0.01")),
        "flights_paris_nyc": flights.quantize(Decimal("0.01")),
        "containers_asia_eu": containers.quantize(Decimal("0.1")),
    }
