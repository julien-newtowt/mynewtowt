"""Tests — référentiel navire & facteurs d'émission multi-GES (MRV lot 1).

Suit le patron ``tests/unit/test_carbon.py`` (moteur SQLite en mémoire créé
par test, sans fixture partagée) : ces tests couvrent
``app.services.referential_env`` (résolution de facteur, idempotence de
l'initialisation référentiel) et la délégation dans
``app.services.co2.get_do_co2_factor``.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — enregistre tous les modèles (dont EmissionFactor)
from app.database import Base
from app.models.co2_variable import Co2Variable
from app.models.emission_factor import EmissionFactor
from app.models.vessel import Vessel
from app.services import co2, referential_env


def _run(coro_factory):
    """Exécute ``coro_factory(session)`` dans un moteur SQLite jetable.

    Invalide systématiquement le cache module-level de
    ``referential_env`` avant ET après : ce cache (TTL 60 s, cf. pattern
    ``services.co2``/``permissions.py``) est un état *process-wide*, pas lié
    à un moteur de test — sans cette précaution, une lecture d'un test
    polluerait le suivant.
    """
    referential_env.invalidate_emission_factor_cache()

    async def _inner():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                return await coro_factory(s)
        finally:
            await eng.dispose()

    try:
        return asyncio.run(_inner())
    finally:
        referential_env.invalidate_emission_factor_cache()


# ════════════════════════════════════════════ resolve_emission_factor


def test_resolve_emission_factor_fails_closed_when_table_empty():
    async def scenario(s):
        return await referential_env.resolve_emission_factor(s, fuel_type="MDO")

    result = _run(scenario)
    assert result.is_fallback is True
    assert result.ef_co2_kg_per_kg == Decimal("3.206")
    assert result.ef_ch4_kg_per_kg == Decimal("0.00005")
    assert result.ef_n2o_kg_per_kg == Decimal("0.00018")
    assert result.wtt_gco2eq_per_mj == Decimal("17.7")


def test_resolve_emission_factor_fails_closed_for_unknown_fuel():
    async def scenario(s):
        s.add(
            EmissionFactor(
                fuel_type="MDO",
                ef_co2_kg_per_kg=Decimal("3.206"),
                ef_ch4_kg_per_kg=Decimal("0.00005"),
                ef_n2o_kg_per_kg=Decimal("0.00018"),
                wtt_gco2eq_per_mj=Decimal("17.7"),
                valid_from=date(2025, 1, 1),
                is_current=True,
            )
        )
        await s.flush()
        return await referential_env.resolve_emission_factor(s, fuel_type="LNG")

    result = _run(scenario)
    assert result.is_fallback is True
    assert result.fuel_type == "LNG"


def test_resolve_emission_factor_uses_current_row():
    async def scenario(s):
        s.add(
            EmissionFactor(
                fuel_type="MDO",
                ef_co2_kg_per_kg=Decimal("3.500"),
                ef_ch4_kg_per_kg=Decimal("0.00006"),
                ef_n2o_kg_per_kg=Decimal("0.00020"),
                wtt_gco2eq_per_mj=Decimal("18.1"),
                source_reference="Test override",
                valid_from=date(2026, 1, 1),
                is_current=True,
            )
        )
        await s.flush()
        return await referential_env.resolve_emission_factor(s, fuel_type="MDO")

    result = _run(scenario)
    assert result.is_fallback is False
    assert result.ef_co2_kg_per_kg == Decimal("3.500")
    assert result.source_reference == "Test override"


def test_resolve_emission_factor_windowed_by_date_picks_period_row_over_current():
    """Une fenêtre datée qui couvre ``at_date`` gagne sur la ligne is_current."""

    async def scenario(s):
        s.add_all(
            [
                EmissionFactor(
                    fuel_type="MDO",
                    ef_co2_kg_per_kg=Decimal("3.100"),
                    ef_ch4_kg_per_kg=Decimal("0.00004"),
                    ef_n2o_kg_per_kg=Decimal("0.00017"),
                    wtt_gco2eq_per_mj=Decimal("17.0"),
                    valid_from=date(2024, 1, 1),
                    valid_to=date(2024, 12, 31),
                    is_current=False,
                ),
                EmissionFactor(
                    fuel_type="MDO",
                    ef_co2_kg_per_kg=Decimal("3.206"),
                    ef_ch4_kg_per_kg=Decimal("0.00005"),
                    ef_n2o_kg_per_kg=Decimal("0.00018"),
                    wtt_gco2eq_per_mj=Decimal("17.7"),
                    valid_from=date(2025, 1, 1),
                    valid_to=None,
                    is_current=True,
                ),
            ]
        )
        await s.flush()
        return await referential_env.resolve_emission_factor(
            s, fuel_type="MDO", at_date=date(2024, 6, 15)
        )

    result = _run(scenario)
    assert result.is_fallback is False
    assert result.ef_co2_kg_per_kg == Decimal("3.100")
    assert result.is_current is False


def test_resolve_emission_factor_date_outside_any_window_falls_back_to_current():
    async def scenario(s):
        s.add(
            EmissionFactor(
                fuel_type="MDO",
                ef_co2_kg_per_kg=Decimal("3.206"),
                ef_ch4_kg_per_kg=Decimal("0.00005"),
                ef_n2o_kg_per_kg=Decimal("0.00018"),
                wtt_gco2eq_per_mj=Decimal("17.7"),
                valid_from=date(2025, 1, 1),
                valid_to=None,
                is_current=True,
            )
        )
        await s.flush()
        # Date antérieure à toute fenêtre connue : aucune fenêtre ne couvre,
        # on retombe sur la ligne is_current (étape 2 de la résolution).
        return await referential_env.resolve_emission_factor(
            s, fuel_type="MDO", at_date=date(2020, 1, 1)
        )

    result = _run(scenario)
    assert result.is_fallback is False
    assert result.ef_co2_kg_per_kg == Decimal("3.206")


def test_resolve_emission_factor_cache_invalidation_sees_new_row():
    async def scenario(s):
        first = await referential_env.resolve_emission_factor(s, fuel_type="MDO")
        s.add(
            EmissionFactor(
                fuel_type="MDO",
                ef_co2_kg_per_kg=Decimal("9.999"),
                ef_ch4_kg_per_kg=Decimal("0.00005"),
                ef_n2o_kg_per_kg=Decimal("0.00018"),
                wtt_gco2eq_per_mj=Decimal("17.7"),
                valid_from=date(2025, 1, 1),
                is_current=True,
            )
        )
        await s.flush()
        # Sans invalidation, le cache 60s renverrait encore le repli codé.
        still_cached = await referential_env.resolve_emission_factor(s, fuel_type="MDO")
        referential_env.invalidate_emission_factor_cache()
        after_invalidate = await referential_env.resolve_emission_factor(s, fuel_type="MDO")
        return first, still_cached, after_invalidate

    first, still_cached, after_invalidate = _run(scenario)
    assert first.is_fallback is True
    assert still_cached.is_fallback is True  # cache pas encore invalidé
    assert after_invalidate.is_fallback is False
    assert after_invalidate.ef_co2_kg_per_kg == Decimal("9.999")


# ════════════════════════════════════════════ ensure_vessel_env_defaults


def test_ensure_vessel_env_defaults_creates_5_tanks_and_6_engines():
    async def scenario(s):
        v = Vessel(code="ANE", name="Anemos")
        s.add(v)
        await s.flush()
        result = await referential_env.ensure_vessel_env_defaults(s, v)
        tanks = await referential_env.get_vessel_tanks(s, v.id)
        engines = await referential_env.get_vessel_engines(s, v.id)
        return result, tanks, engines

    result, tanks, engines = _run(scenario)
    assert result.changed is True
    assert set(result.tanks_created) == {"14", "15", "16", "17", "other"}
    assert set(result.engines_created) == {
        "PME",
        "SME",
        "FWD_GEN",
        "AFT_GEN",
        "PORT_SHAFT_GEN",
        "STBD_SHAFT_GEN",
    }
    assert len(tanks) == 5
    assert len(engines) == 6


def test_ensure_vessel_env_defaults_engine_groups_follow_aggregation_rule():
    async def scenario(s):
        v = Vessel(code="ART", name="Artemis")
        s.add(v)
        await s.flush()
        await referential_env.ensure_vessel_env_defaults(s, v)
        engines = await referential_env.get_vessel_engines(s, v.id)
        return {e.engine_role: e.engine_group for e in engines}

    groups = _run(scenario)
    assert groups["PME"] == "ME"
    assert groups["SME"] == "ME"
    assert groups["FWD_GEN"] == "AE"
    assert groups["AFT_GEN"] == "AE"
    assert groups["PORT_SHAFT_GEN"] is None
    assert groups["STBD_SHAFT_GEN"] is None


def test_ensure_vessel_env_defaults_is_idempotent():
    """Un second appel ne crée rien de plus (aucun doublon, rien modifié)."""

    async def scenario(s):
        v = Vessel(code="ATL", name="Atlantis")
        s.add(v)
        await s.flush()
        first = await referential_env.ensure_vessel_env_defaults(s, v)
        second = await referential_env.ensure_vessel_env_defaults(s, v)
        tanks = await referential_env.get_vessel_tanks(s, v.id)
        engines = await referential_env.get_vessel_engines(s, v.id)
        return first, second, tanks, engines

    first, second, tanks, engines = _run(scenario)
    assert first.changed is True
    assert second.changed is False
    assert second.tanks_created == ()
    assert second.engines_created == ()
    assert len(tanks) == 5
    assert len(engines) == 6


def test_ensure_vessel_env_defaults_fills_gap_without_touching_existing_rows():
    """Navire partiellement initialisé (ex. import manuel) : ne complète que ce qui manque."""

    async def scenario(s):
        v = Vessel(code="POS", name="Poseidon")
        s.add(v)
        await s.flush()
        from app.models.vessel_env import VesselTank

        # Une cuve déjà présente, avec une note qui ne doit pas être touchée.
        s.add(VesselTank(vessel_id=v.id, tank_code="14", note="cuve déjà notée"))
        await s.flush()
        result = await referential_env.ensure_vessel_env_defaults(s, v)
        tanks = await referential_env.get_vessel_tanks(s, v.id)
        return result, tanks

    result, tanks = _run(scenario)
    assert set(result.tanks_created) == {"15", "16", "17", "other"}
    by_code = {t.tank_code: t for t in tanks}
    assert by_code["14"].note == "cuve déjà notée"
    assert len(tanks) == 5


# ════════════════════════════════════════════ Délégation co2.get_do_co2_factor


def test_get_do_co2_factor_delegates_to_emission_factors_first():
    async def scenario(s):
        s.add(
            EmissionFactor(
                fuel_type="MDO",
                ef_co2_kg_per_kg=Decimal("3.500"),
                ef_ch4_kg_per_kg=Decimal("0.00005"),
                ef_n2o_kg_per_kg=Decimal("0.00018"),
                wtt_gco2eq_per_mj=Decimal("17.7"),
                valid_from=date(2025, 1, 1),
                is_current=True,
            )
        )
        # co2_variables porte aussi une valeur : emission_factors doit
        # gagner (étage 1 avant étage 2).
        s.add(
            Co2Variable(
                name=co2.DO_CO2_EF_VARIABLE,
                value=Decimal("2.900"),
                effective_date=date(2025, 1, 1),
                is_current=True,
            )
        )
        await s.flush()
        return await co2.get_do_co2_factor(s)

    factor = _run(scenario)
    assert factor == Decimal("3.500")


def test_get_do_co2_factor_falls_back_to_co2_variable_when_no_emission_factor_row():
    async def scenario(s):
        s.add(
            Co2Variable(
                name=co2.DO_CO2_EF_VARIABLE,
                value=Decimal("2.900"),
                effective_date=date(2025, 1, 1),
                is_current=True,
            )
        )
        await s.flush()
        return await co2.get_do_co2_factor(s)

    factor = _run(scenario)
    assert factor == Decimal("2.900")


def test_get_do_co2_factor_constant_when_both_referentials_empty():
    async def scenario(s):
        return await co2.get_do_co2_factor(s)

    factor = _run(scenario)
    assert factor == Decimal("3.206")
    assert factor == co2.DO_CO2_G_PER_G
