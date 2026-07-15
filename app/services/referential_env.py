"""Référentiel navire & facteurs d'émission multi-GES — MRV lot 1.

Deux responsabilités distinctes :

1. **Référentiel navire** (cuves/moteurs) : lecture par navire +
   initialisation idempotente (``ensure_vessel_env_defaults``) — 5 cuves et
   6 moteurs par navire, appelée depuis l'écran ``/admin/flotte-env``.

2. **Facteurs d'émission** (``emission_factors``) : résolution du facteur
   applicable pour un carburant/une date (``resolve_emission_factor``), avec
   cache 60 s et repli **fail-closed** sur des constantes codées — même
   pattern que le cache de ``services.co2`` (facteurs) et de
   ``permissions.py`` (overrides ARC-04) : lecture best-effort, toute erreur
   DB retombe sur la valeur par défaut, résultat mis en cache pour ne pas
   marteler une base en échec.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vessel import Vessel
from app.models.vessel_env import (
    ENGINE_ROLES,
    TANK_CODES,
    VesselEngine,
    VesselTank,
)

# ════════════════════════════════════════════ Référentiel navire (cuves/moteurs)

# Rôle moteur → groupe d'agrégation MRV (dictionnaire de données §2.1) :
# PME/SME → ME ; FWD_GEN/AFT_GEN → AE ; lignes d'arbre → NULL (hors totaux).
ENGINE_ROLE_TO_GROUP: dict[str, str | None] = {
    "PME": "ME",
    "SME": "ME",
    "FWD_GEN": "AE",
    "AFT_GEN": "AE",
    "PORT_SHAFT_GEN": None,
    "STBD_SHAFT_GEN": None,
}


async def get_vessel_tanks(db: AsyncSession, vessel_id: int) -> list[VesselTank]:
    rows = await db.execute(
        select(VesselTank).where(VesselTank.vessel_id == vessel_id).order_by(VesselTank.tank_code)
    )
    return list(rows.scalars().all())


async def get_vessel_engines(db: AsyncSession, vessel_id: int) -> list[VesselEngine]:
    rows = await db.execute(
        select(VesselEngine)
        .where(VesselEngine.vessel_id == vessel_id)
        .order_by(VesselEngine.display_order, VesselEngine.id)
    )
    return list(rows.scalars().all())


@dataclass(frozen=True)
class VesselEnvInitResult:
    """Résultat d'``ensure_vessel_env_defaults`` — ce qui a été créé (si rien, no-op)."""

    tanks_created: tuple[str, ...]
    engines_created: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.tanks_created or self.engines_created)


async def ensure_vessel_env_defaults(db: AsyncSession, vessel: Vessel) -> VesselEnvInitResult:
    """Crée les cuves/moteurs par défaut d'un navire — **idempotent**.

    5 cuves (``vessel_env.TANK_CODES`` : 14/15/16/17/other) + 6 moteurs
    (``vessel_env.ENGINE_ROLES``, groupe ME/AE dérivé de
    ``ENGINE_ROLE_TO_GROUP``). N'ajoute que ce qui manque encore pour ce
    navire : un appel répété (navire déjà initialisé, ou partiellement) ne
    crée jamais de doublon et ne modifie aucune ligne existante — un navire
    s'ajoute donc en admin sans écrire de code (acceptation lot 1).
    """
    existing_tanks = set(
        (await db.execute(select(VesselTank.tank_code).where(VesselTank.vessel_id == vessel.id)))
        .scalars()
        .all()
    )
    tanks_created: list[str] = []
    for code in TANK_CODES:
        if code in existing_tanks:
            continue
        db.add(VesselTank(vessel_id=vessel.id, tank_code=code))
        tanks_created.append(code)

    existing_engines = set(
        (
            await db.execute(
                select(VesselEngine.engine_role).where(VesselEngine.vessel_id == vessel.id)
            )
        )
        .scalars()
        .all()
    )
    engines_created: list[str] = []
    for order, role in enumerate(ENGINE_ROLES, start=1):
        if role in existing_engines:
            continue
        db.add(
            VesselEngine(
                vessel_id=vessel.id,
                engine_role=role,
                engine_group=ENGINE_ROLE_TO_GROUP.get(role),
                display_order=order,
            )
        )
        engines_created.append(role)

    if tanks_created or engines_created:
        await db.flush()
    return VesselEnvInitResult(
        tanks_created=tuple(tanks_created), engines_created=tuple(engines_created)
    )


# ════════════════════════════════════════════ Facteurs d'émission multi-GES

# Fail-closed — mêmes valeurs que ``services.co2.DO_CO2_G_PER_G`` (CO₂) et le
# Carbon Report officiel (MEPC.391(81) + CFOTE_09 Rev02). Dernier repli si
# ``emission_factors`` est vide, inaccessible, ou ne couvre pas le carburant
# demandé — V1 n'exploitant que le MDO, un ``fuel_type`` inconnu retombe
# aussi dessus (repli documenté, pas un mélange de carburants).
FALLBACK_EF_CO2_KG_PER_KG = Decimal("3.206")
FALLBACK_EF_CH4_KG_PER_KG = Decimal("0.00005")
FALLBACK_EF_N2O_KG_PER_KG = Decimal("0.00018")
FALLBACK_WTT_GCO2EQ_PER_MJ = Decimal("17.7")
FALLBACK_SOURCE_REFERENCE = "MEPC.391(81) + CFOTE_09 Rev02 (constante codée)"


@dataclass(frozen=True)
class ResolvedEmissionFactor:
    """Facteur applicable — ligne ``emission_factors`` ou repli codé (``is_fallback``)."""

    fuel_type: str
    ef_co2_kg_per_kg: Decimal
    ef_ch4_kg_per_kg: Decimal
    ef_n2o_kg_per_kg: Decimal
    wtt_gco2eq_per_mj: Decimal
    source_reference: str | None
    valid_from: _date | None
    valid_to: _date | None
    is_current: bool
    is_fallback: bool


def _fallback_factor(fuel_type: str) -> ResolvedEmissionFactor:
    return ResolvedEmissionFactor(
        fuel_type=fuel_type,
        ef_co2_kg_per_kg=FALLBACK_EF_CO2_KG_PER_KG,
        ef_ch4_kg_per_kg=FALLBACK_EF_CH4_KG_PER_KG,
        ef_n2o_kg_per_kg=FALLBACK_EF_N2O_KG_PER_KG,
        wtt_gco2eq_per_mj=FALLBACK_WTT_GCO2EQ_PER_MJ,
        source_reference=FALLBACK_SOURCE_REFERENCE,
        valid_from=None,
        valid_to=None,
        is_current=True,
        is_fallback=True,
    )


@dataclass(frozen=True)
class _FactorSnapshot:
    """Copie immuable d'une ligne ``EmissionFactor`` — détachable, cache-safe."""

    fuel_type: str
    ef_co2_kg_per_kg: Decimal
    ef_ch4_kg_per_kg: Decimal
    ef_n2o_kg_per_kg: Decimal
    wtt_gco2eq_per_mj: Decimal
    source_reference: str | None
    valid_from: _date
    valid_to: _date | None
    is_current: bool


# Cache module-level (TTL 60 s) — invalidé par /admin/emission-factors/create.
# On cache TOUTES les lignes en dataclasses détachées (comme
# permissions._overrides_cache met en cache tous les overrides) : la
# résolution dépend de (fuel_type, at_date), propres à chaque appel — un
# cache à une seule valeur (comme co2._factors_cache) ne conviendrait pas.
_EF_TTL_SECONDS = 60.0
_ef_rows_cache: list[_FactorSnapshot] | None = None
_ef_rows_loaded_at: float = 0.0


def invalidate_emission_factor_cache() -> None:
    """Force la relecture DB au prochain ``resolve_emission_factor`` (post-write admin)."""
    global _ef_rows_cache, _ef_rows_loaded_at
    _ef_rows_cache = None
    _ef_rows_loaded_at = 0.0


async def _load_emission_factor_rows(db: AsyncSession) -> list[_FactorSnapshot]:
    global _ef_rows_cache, _ef_rows_loaded_at
    now = time.monotonic()
    if _ef_rows_cache is not None and (now - _ef_rows_loaded_at) < _EF_TTL_SECONDS:
        return _ef_rows_cache

    snapshot: list[_FactorSnapshot] = []
    try:
        from app.models.emission_factor import EmissionFactor

        rows = (await db.execute(select(EmissionFactor))).scalars().all()
        snapshot = [
            _FactorSnapshot(
                fuel_type=r.fuel_type,
                ef_co2_kg_per_kg=Decimal(r.ef_co2_kg_per_kg),
                ef_ch4_kg_per_kg=Decimal(r.ef_ch4_kg_per_kg),
                ef_n2o_kg_per_kg=Decimal(r.ef_n2o_kg_per_kg),
                wtt_gco2eq_per_mj=Decimal(r.wtt_gco2eq_per_mj),
                source_reference=r.source_reference,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                is_current=r.is_current,
            )
            for r in rows
        ]
    except Exception:
        # Lecture best-effort : table absente (avant migration), connexion HS,
        # etc. → liste vide, mise en cache (n'insiste pas sur une DB en échec).
        # resolve_emission_factor bascule alors sur le repli codé.
        snapshot = []

    _ef_rows_cache = snapshot
    _ef_rows_loaded_at = now
    return snapshot


def _to_result(row: _FactorSnapshot) -> ResolvedEmissionFactor:
    return ResolvedEmissionFactor(
        fuel_type=row.fuel_type,
        ef_co2_kg_per_kg=row.ef_co2_kg_per_kg,
        ef_ch4_kg_per_kg=row.ef_ch4_kg_per_kg,
        ef_n2o_kg_per_kg=row.ef_n2o_kg_per_kg,
        wtt_gco2eq_per_mj=row.wtt_gco2eq_per_mj,
        source_reference=row.source_reference,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        is_current=row.is_current,
        is_fallback=False,
    )


async def resolve_emission_factor(
    db: AsyncSession,
    fuel_type: str = "MDO",
    at_date: _date | None = None,
) -> ResolvedEmissionFactor:
    """Facteur applicable pour ``fuel_type`` — fenêtre datée, sinon courant, sinon repli.

    Ordre de résolution (plan §2.2/§2.5) :

    1. ``at_date`` fourni → ligne dont la fenêtre couvre cette date
       (``valid_from <= at_date`` et ``valid_to IS NULL or valid_to >= at_date``) ;
       plusieurs candidates (ne devrait pas arriver) → la plus récente par
       ``valid_from``.
    2. Sinon (pas de date, ou aucune fenêtre ne couvre) → ligne ``is_current=True``.
    3. Sinon (rien pour ce carburant, table vide/inaccessible) → **fail-closed**
       sur les constantes codées (``is_fallback=True``).

    Lecture seule, mise en cache 60 s (bulk — cf. ``invalidate_emission_factor_cache``).
    """
    rows = await _load_emission_factor_rows(db)
    candidates = [r for r in rows if r.fuel_type == fuel_type]

    if at_date is not None:
        windowed = [
            r
            for r in candidates
            if r.valid_from <= at_date and (r.valid_to is None or r.valid_to >= at_date)
        ]
        if windowed:
            windowed.sort(key=lambda r: r.valid_from, reverse=True)
            return _to_result(windowed[0])

    current = [r for r in candidates if r.is_current]
    if current:
        current.sort(key=lambda r: r.valid_from, reverse=True)
        return _to_result(current[0])

    return _fallback_factor(fuel_type)
