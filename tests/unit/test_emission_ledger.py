"""Tests unitaires — grand livre d'émissions unifié (LOT 9).

Couvre ``app.services.emission_ledger`` :

- **fallback ``legacy_noon``** : un leg sans événement rend exactement les
  chiffres de l'ancien ``services.carbon`` (constantes figées AVANT
  rebranchement — mêmes valeurs que ``tests/unit/test_carbon.py``) ;
- **matérialisation** : ``refresh_summary`` idempotent (2 appels → 1 seule
  ligne, à jour) — le summary est un cache recalculable ;
- **hook event_capture** : la finalisation puis la validation d'un événement
  rematérialisent le summary du voyage ;
- **chaîne du facteur préservée** : ``do_co2_ef`` (``co2_variables``,
  /admin/co2) reste honoré quand ``emission_factors`` est vide ;
- **méthode C réelle** : ``cargo_mrv`` disponible (événements) ⇒ EF C calculé
  (``kpi_env.leg_ef``/``aggregate_ef`` + ``ef_method_c`` du ledger) ;
- **conso d'escale (G12)** : formule R14b résolue pour ``Consommation_escale``
  (ROB déclarés + soutages), repli compteurs (G2) si un ROB manque, ``None``
  tant que le Departure suivant n'est pas finalisé ;
- **CO2eq GWP-100 (G13)** : ``emissions_breakdown`` calcule désormais le TtW
  en équivalent CO₂ (Annexe I EU 2015/757), distinct du WtT ;
- **provider kpi_env** : lit le summary quand il existe, repli ``LegKPI`` sinon.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.bunker import BunkerOperation
from app.models.co2_variable import Co2Variable
from app.models.finance import LegKPI
from app.models.leg import Leg
from app.models.nav_event import ArrivalEvent, DepartureEvent, NavEventEngineReading, NoonEvent
from app.models.noon_report import NoonReport
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.services import emission_ledger, event_capture, referential_env
from app.services.co2 import invalidate_factors_cache
from app.services.kpi_env import (
    NA_BALLAST,
    NA_CARGO_MRV,
    LegEmissionRecord,
    aggregate_ef,
    leg_ef,
)
from app.services.referential_env import (
    ResolvedEmissionFactor,
    ensure_vessel_env_defaults,
    get_vessel_engines,
)
from app.services.validation_engine import invalidate_cache, seed_reference_data

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
OCC = Decimal("70")
CAP = Decimal("1100")


def _naive(dt: datetime) -> datetime:
    """Normalise naïf-UTC (SQLite restitue naïf, la maj Python est aware)."""
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _reset_module_caches() -> None:
    invalidate_cache()
    invalidate_factors_cache()
    referential_env.invalidate_emission_factor_cache()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    _reset_module_caches()
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        _reset_module_caches()


async def _base(db):
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    p1 = Port(name="Fecamp", locode="FRFEC", country="FR", latitude=49.7, longitude=0.37)
    p2 = Port(name="Belem", locode="BRBEL", country="BR", latitude=-1.45, longitude=-48.5)
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=T0,
        eta_ref=T0 + timedelta(days=5),
        etd=T0,
        eta=T0 + timedelta(days=5),
        distance_nm=Decimal("200"),
    )
    db.add(leg)
    await db.flush()
    return vessel, leg


async def _legacy_noons(db, leg):
    db.add(
        NoonReport(leg_id=leg.id, recorded_at=T0, latitude=0, longitude=0, total_consumption_t=1.3)
    )
    db.add(
        NoonReport(
            leg_id=leg.id,
            recorded_at=T0 + timedelta(days=1),
            latitude=0,
            longitude=0,
            total_consumption_t=0.7,
        )
    )
    await db.flush()


async def _events_chain(db, vessel, leg):
    """Dep (laden, cargo B/L 900 / MRV 950) + Noon — PME seul (Δ 1000 L)."""
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("100.000"),
        vessel_condition="laden",
        cargo_bl_t=Decimal("900.000"),
        cargo_mrv_t=Decimal("950.000"),
    )
    dep.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("10000"))
    ]
    noon = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
    )
    noon.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("11000"))
    ]
    db.add_all([dep, noon])
    await db.flush()
    return dep, noon


# ══════════════════════════════════════ Fallback legacy_noon (chiffres figés)


async def test_legacy_fallback_matches_pre_lot9_figures(db):
    """Leg sans événement → mêmes chiffres que l'ancien compute_carbon_for_leg.

    Constantes figées AVANT rebranchement (logique carbon.py historique) :
    Σ noon 1,3 + 0,7 = 2,0 t DO ; × 3,206 = 6,412 t CO₂.
    """
    _vessel, leg = await _base(db)
    await _legacy_noons(db, leg)

    r = await emission_ledger.compute_for_leg(db, leg)
    assert r.source == "legacy_noon"
    assert r.do_consumed_t == Decimal("2.0")
    assert r.conso_total_t == Decimal("2.0")
    assert r.conso_hors_mouillage_t == Decimal("2.0")
    assert r.conso_mouillage_t is None  # pas de granularité intervalle en legacy
    assert r.co2_emitted_t == Decimal("2.0") * Decimal("3.206")
    assert r.co2_emitted_t == Decimal("6.4120")
    assert r.do_co2_factor == Decimal("3.206")
    # CH₄/N₂O en grammes, WtT distinct — mêmes formules que le Carbon v2.
    assert r.ch4_g == Decimal("2.0") * Decimal("0.00005") * Decimal("1000000")
    assert r.n2o_g == Decimal("2.0") * Decimal("0.00018") * Decimal("1000000")
    assert r.wtt_co2eq_t == Decimal("2.0") * Decimal("42700") * Decimal("17.7") / Decimal("1000000")
    # G13 — CO2eq GWP-100 (Annexe I EU 2015/757 : CH4=25, N2O=298), distinct du WtT.
    assert r.co2eq_t == Decimal("2.0") * (
        Decimal("3.206") + Decimal("0.00005") * Decimal("25") + Decimal("0.00018") * Decimal("298")
    )
    assert r.co2eq_t != r.wtt_co2eq_t
    # Distance canonique = leg.distance_nm ; cargo B/L = bookings (aucun → 0).
    assert r.distance_nm == Decimal("200")
    assert r.cargo_bl_t == Decimal("0.000")
    assert r.cargo_mrv_t is None  # méthode C indisponible en legacy


async def test_do_co2_ef_variable_chain_preserved(db):
    """Chaîne /admin/co2 : sans emission_factors, ``do_co2_ef`` versionné prime."""
    _vessel, leg = await _base(db)
    await _legacy_noons(db, leg)
    db.add(
        Co2Variable(
            name="do_co2_ef", value=Decimal("3.5"), effective_date=date(2026, 1, 1), is_current=True
        )
    )
    await db.flush()
    referential_env.invalidate_emission_factor_cache()

    r = await emission_ledger.compute_for_leg(db, leg)
    assert r.do_co2_factor == Decimal("3.5")
    assert r.co2_emitted_t == Decimal("2.0") * Decimal("3.5")
    # Le reste du multi-GES garde les replis codés (CH₄/N₂O/WtT).
    assert r.factor.ef_ch4_kg_per_kg == Decimal("0.00005")


# ══════════════════════════════ CO2eq GWP-100 (G13) ══════════════════════════════

_MDO_FACTOR = ResolvedEmissionFactor(
    fuel_type="MDO",
    ef_co2_kg_per_kg=Decimal("3.206"),
    ef_ch4_kg_per_kg=Decimal("0.00005"),
    ef_n2o_kg_per_kg=Decimal("0.00018"),
    wtt_gco2eq_per_mj=Decimal("17.7"),
    source_reference="test",
    valid_from=None,
    valid_to=None,
    is_current=True,
    is_fallback=True,
)


def test_emissions_breakdown_computes_co2eq_gwp100():
    """G13 — CO2eq GWP-100 tank-to-wake (Annexe I EU 2015/757 : CH4=25, N2O=298)
    ≈ 3,261 kgCO2eq/kgFuel pour le MDO (architecture §2.1), distinct du WtT."""
    em = emission_ledger.emissions_breakdown(Decimal("10"), _MDO_FACTOR)
    expected_per_kg = (
        Decimal("3.206") + Decimal("0.00005") * Decimal("25") + Decimal("0.00018") * Decimal("298")
    )
    assert expected_per_kg.quantize(Decimal("0.001")) == Decimal("3.261")
    assert Decimal(em["co2eq_t"]) == Decimal("10") * expected_per_kg
    assert em["co2eq_t"] != em["wtt_co2eq_t"]


def test_emissions_breakdown_co2eq_none_without_conso():
    em = emission_ledger.emissions_breakdown(None, _MDO_FACTOR)
    assert em["co2eq_t"] is None


# ═══════════════════════════════════════ refresh_summary (cache, idempotent)


async def test_refresh_summary_idempotent_upsert(db):
    _vessel, leg = await _base(db)
    await _legacy_noons(db, leg)

    s1 = await emission_ledger.refresh_summary(db, leg)
    first_computed_at = s1.computed_at
    s2 = await emission_ledger.refresh_summary(db, leg)

    rows = (
        (
            await db.execute(
                select(VoyageEmissionSummary).where(VoyageEmissionSummary.leg_id == leg.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # deux appels → UNE ligne (upsert)
    assert s2.id == s1.id
    assert _naive(s2.computed_at) >= _naive(first_computed_at)
    assert s2.source == "legacy_noon"
    assert s2.co2_t == Decimal("2.0") * Decimal("3.206")
    assert s2.conso_total_t == Decimal("2.0")
    assert s2.distance_nm == Decimal("200")
    assert s2.factors_ref is None  # repli codé → aucune ligne emission_factors
    # G13 — CO2eq GWP-100 (Annexe I EU 2015/757), persisté et distinct du WtT.
    assert s2.co2eq_t == Decimal("2.0") * (
        Decimal("3.206") + Decimal("0.00005") * Decimal("25") + Decimal("0.00018") * Decimal("298")
    )
    assert s2.co2eq_t != s2.wtt_co2eq_t


async def test_refresh_summary_follows_source_switch(db):
    """Le summary suit la source de vérité : noon legacy puis événements."""
    vessel, leg = await _base(db)
    await _legacy_noons(db, leg)
    s = await emission_ledger.refresh_summary(db, leg)
    assert s.source == "legacy_noon"

    await _events_chain(db, vessel, leg)
    s = await emission_ledger.refresh_summary(db, leg)
    assert s.source == "events"
    # Δ 1000 L × 0,000845 = 0,845 t (PME/ME) — l'assiette vient des événements.
    assert s.conso_total_t == Decimal("0.845000")
    assert s.cargo_mrv_t == Decimal("950.000")
    rows = (await db.execute(select(func.count()).select_from(VoyageEmissionSummary))).scalar_one()
    assert rows == 1


# ═══════════════════════════════ Hook event_capture (finalisation/validation)


async def test_finalize_and_validate_hooks_refresh_summary(db):
    vessel, leg = await _base(db)
    author = User(username="master", email="m@t.test", hashed_password="x", role="marins")
    validator = User(username="siege", email="s@t.test", hashed_password="x", role="administrateur")
    db.add_all([author, validator])
    await db.flush()

    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 12), "timezone": "UTC"},
    )
    # Brouillon : aucun summary (les brouillons sont exclus de tout calcul).
    count = (await db.execute(select(func.count()).select_from(VoyageEmissionSummary))).scalar_one()
    assert count == 0

    await event_capture.finalize(db, ev, author)
    summary = (
        await db.execute(
            select(VoyageEmissionSummary).where(VoyageEmissionSummary.leg_id == leg.id)
        )
    ).scalar_one()
    assert summary.source == "events"
    first_computed_at = summary.computed_at

    await event_capture.validate(db, ev, validator)
    summary2 = (
        await db.execute(
            select(VoyageEmissionSummary).where(VoyageEmissionSummary.leg_id == leg.id)
        )
    ).scalar_one()
    assert summary2.id == summary.id  # toujours une seule ligne
    assert _naive(summary2.computed_at) >= _naive(first_computed_at)


# ══════════════════════════════════ Méthode C réelle (cargo MRV disponible)


def test_leg_ef_method_c_real_when_cargo_mrv_present():
    record = LegEmissionRecord(
        leg_id=1,
        leg_code="1AFRBR6",
        vessel_id=1,
        co2_emitted_t=Decimal("50"),
        cargo_t=Decimal("500"),
        distance_nm=Decimal("1000"),
        etd=datetime(2026, 1, 5, tzinfo=UTC),
        ata=datetime(2026, 1, 25, tzinfo=UTC),
        has_kpi=True,
        cargo_mrv_t=Decimal("950"),
    )
    result = leg_ef(record, method="C", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert result.na_reason is None
    # 50 t × 1e6 / (950 t × 1852 km) = 28,42 gCO₂/t·km.
    assert result.value_gco2_tkm == Decimal("28.42")


def test_leg_ef_method_c_ballast_mrv_is_na():
    record = LegEmissionRecord(
        leg_id=2,
        leg_code="1BBRFR6",
        vessel_id=1,
        co2_emitted_t=Decimal("30"),
        cargo_t=Decimal("0"),
        distance_nm=Decimal("800"),
        etd=datetime(2026, 2, 1, tzinfo=UTC),
        ata=None,
        has_kpi=True,
        cargo_mrv_t=Decimal("0"),  # ballast ⇒ cargo MRV = 0
    )
    result = leg_ef(record, method="C", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert result.value_gco2_tkm is None
    assert result.na_reason == NA_BALLAST


def test_aggregate_ef_method_c_mixed_records():
    """Voyage avec cargo MRV + voyage legacy (None) : dénominateur = MRV seul,
    numérateur = tout le CO₂ (le legacy émet aussi)."""
    with_mrv = LegEmissionRecord(
        leg_id=1,
        leg_code="1AFRBR6",
        vessel_id=1,
        co2_emitted_t=Decimal("50"),
        cargo_t=Decimal("500"),
        distance_nm=Decimal("1000"),
        etd=None,
        ata=None,
        has_kpi=True,
        cargo_mrv_t=Decimal("950"),
    )
    legacy = LegEmissionRecord(
        leg_id=2,
        leg_code="1BBRFR6",
        vessel_id=1,
        co2_emitted_t=Decimal("30"),
        cargo_t=Decimal("400"),
        distance_nm=Decimal("800"),
        etd=None,
        ata=None,
        has_kpi=True,  # cargo_mrv_t=None (défaut) — legacy
    )
    ef, denom = aggregate_ef([with_mrv, legacy], method="C", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert ef.na_reason is None
    # Dénominateur : 950 × 1852 = 1 759 400 t·km (le legacy est exclu).
    assert denom == Decimal("1759400.000")
    # Numérateur : 80 t → 80e6/1 759 400 = 45,47.
    assert ef.value_gco2_tkm == Decimal("45.47")

    # Aucun voyage avec cargo MRV → N/A motivé (comportement legacy conservé).
    ef_na, denom_na = aggregate_ef([legacy], method="C", occupancy_pct=OCC, capacity_ref_t=CAP)
    assert ef_na.value_gco2_tkm is None
    assert ef_na.na_reason == NA_CARGO_MRV
    assert denom_na == Decimal(0)


async def test_ledger_ef_method_c_from_events(db):
    vessel, leg = await _base(db)
    await _events_chain(db, vessel, leg)

    r = await emission_ledger.compute_for_leg(db, leg)
    assert r.source == "events"
    assert r.cargo_mrv_t == Decimal("950.000")
    assert r.ef_method_c is not None
    # EF C = co2 × 1e6 / (cargo_mrv × distance_km) — vérifié par recomposition.
    distance_km = r.distance_nm * Decimal("1.852")
    expected = r.co2_emitted_t * Decimal("1000000") / (Decimal("950.000") * distance_km)
    assert r.ef_method_c == expected.quantize(Decimal("0.0001"))
    # A (cargo B/L 900) et B (1100 × 70 %) également posés.
    assert r.ef_method_a is not None
    assert r.ef_method_b is not None


# ═══════════════════════════ Conso d'escale (G12 — formule R14b résolue)


async def _second_leg(db, vessel, *, leg_code="2AFRBR6"):
    p3 = Port(name="Belem2", locode="BRBE2", country="BR", latitude=-1.5, longitude=-48.6)
    db.add(p3)
    await db.flush()
    leg2 = Leg(
        leg_code=leg_code,
        vessel_id=vessel.id,
        departure_port_id=p3.id,
        arrival_port_id=p3.id,
        etd_ref=T0 + timedelta(days=3),
        eta_ref=T0 + timedelta(days=5),
        etd=T0 + timedelta(days=3),
        eta=T0 + timedelta(days=5),
        distance_nm=Decimal("100"),
    )
    db.add(leg2)
    await db.flush()
    return leg2


async def test_escale_consumption_rob_solved(db):
    """ROB_arrivée + Σ soutage − ROB_départ, entre l'Arrival de CE leg et le
    Departure du leg suivant du même navire (architecture §2.4)."""
    vessel, leg = await _base(db)
    dep1 = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        rob_t=Decimal("100.000"),
    )
    arr1 = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        rob_t=Decimal("50.000"),
    )
    db.add_all([dep1, arr1])
    await db.flush()

    leg2 = await _second_leg(db, vessel)
    dep2 = DepartureEvent(
        leg_id=leg2.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=52),
        rob_t=Decimal("55.000"),
    )
    db.add(dep2)
    db.add(
        BunkerOperation(
            vessel_id=vessel.id,
            bdn_number="BDN-ESCALE-1",
            port_locode="BRBEL",
            delivery_datetime_utc=T0 + timedelta(hours=50),
            mass_t=Decimal("10.000"),
            density_15c_t_m3=Decimal("0.845"),
            status="valide_master",
        )
    )
    await db.flush()

    r = await emission_ledger.compute_for_leg(db, leg)
    # 50,000 + 10,000 − 55,000 = 5,000.
    assert r.conso_escale_t == Decimal("5.000")


async def test_escale_consumption_falls_back_to_counters_without_declared_rob(db):
    """ROB déclaré manquant à une des deux bornes → repli sur le delta de
    compteurs moteur (méthode disponible depuis G2)."""
    vessel, leg = await _base(db)
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    dep1 = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        rob_t=Decimal("100.000"),
    )
    arr1 = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        rob_t=Decimal("50.000"),
    )
    arr1.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("5000"))
    ]
    db.add_all([dep1, arr1])
    await db.flush()

    leg2 = await _second_leg(db, vessel)
    dep2 = DepartureEvent(
        leg_id=leg2.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=52),
        rob_t=None,  # ROB non déclaré au départ suivant → repli compteurs.
    )
    dep2.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("6000"))
    ]
    db.add(dep2)
    await db.flush()

    r = await emission_ledger.compute_for_leg(db, leg)
    # Δ 1000 L × 0,000845 = 0,845 t (même densité de repli que les autres tests).
    assert r.conso_escale_t == Decimal("1000") * Decimal("0.001") * Decimal("0.845")


async def test_escale_consumption_none_while_next_departure_not_finalized(db):
    """Escale en cours (pas encore de Departure suivant finalisé) → None,
    jamais une estimation prématurée."""
    vessel, leg = await _base(db)
    dep1 = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        rob_t=Decimal("100.000"),
    )
    arr1 = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        rob_t=Decimal("50.000"),
    )
    db.add_all([dep1, arr1])
    await db.flush()

    r = await emission_ledger.compute_for_leg(db, leg)
    assert r.conso_escale_t is None


# ═══════════════════════ Provider kpi_env : summary d'abord, LegKPI en repli


async def test_kpi_env_provider_reads_summary_with_legkpi_fallback(db):
    from app.services.kpi_env import _emissions_provider

    vessel, leg = await _base(db)
    await _legacy_noons(db, leg)
    # Un second leg SANS summary, couvert par un LegKPI legacy.
    leg2 = Leg(
        leg_code="1BBRFR6",
        vessel_id=vessel.id,
        departure_port_id=leg.arrival_port_id,
        arrival_port_id=leg.departure_port_id,
        etd_ref=T0,
        eta_ref=T0 + timedelta(days=5),
        etd=T0,
        eta=T0 + timedelta(days=5),
        distance_nm=Decimal("800"),
    )
    db.add(leg2)
    await db.flush()
    db.add(LegKPI(leg_id=leg2.id, tonnage_kg=Decimal("400000"), co2_emitted_kg=Decimal("30000")))
    await db.flush()

    await emission_ledger.refresh_summary(db, leg)  # summary pour leg 1 seulement

    records = {r.leg_id: r for r in await _emissions_provider(db)}
    # Leg 1 : servi par le summary du grand livre.
    assert records[leg.id].co2_emitted_t == Decimal("2.0") * Decimal("3.206")
    assert records[leg.id].has_kpi is True
    assert records[leg.id].cargo_mrv_t is None
    # Leg 2 : repli LegKPI — identique à l'avant-lot-9.
    assert records[leg2.id].co2_emitted_t == Decimal("30")
    assert records[leg2.id].cargo_t == Decimal("400")
    assert records[leg2.id].distance_nm == Decimal("800")
    assert records[leg2.id].has_kpi is True
