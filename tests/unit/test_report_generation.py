"""Tests unitaires — génération de rapports MRV & workflow (LOT 5).

Couvre ``app.services.report_generation`` sur un moteur SQLite en mémoire
(FK activées) + seed du référentiel de validation (seuils R14/R16) :

- **Noon** : payload dérivé de l'intervalle depuis l'événement précédent
  (chaîne de 2 événements) ;
- **Carbon** : assiette « hors mouillage » (cas avec 1 mouillage) et multi-GES
  exact (4,43625 t DO × 3,206 = 14,2226175 tCO₂) ;
- **Stopover** : écart ROB classé sur 4 niveaux via les bornes R14 ;
- **cycle de vie** : regénération brouillon OK / validé refusée ;
- **field modification** : R18 (justification vide refusée) + statut dérivé
  (pire cas ``under_conformity``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.leg import Leg
from app.models.nav_event import (
    ArrivalEvent,
    BeginAnchoringEvent,
    DepartureEvent,
    EndAnchoringEvent,
    NavEventEngineReading,
    NoonEvent,
)
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.services import referential_env
from app.services import report_generation as rg
from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines
from app.services.validation_engine import invalidate_cache, seed_reference_data

FACTOR = Decimal("0.001") * Decimal("0.845")  # litres → tonnes @ densité 0,845
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

DEP_FUEL = {
    "PME": 10000,
    "SME": 8000,
    "FWD_GEN": 5000,
    "AFT_GEN": 4000,
    "PORT_SHAFT_GEN": 2000,
    "STBD_SHAFT_GEN": 2000,
}
N1_FUEL = {
    "PME": 11000,
    "SME": 8600,
    "FWD_GEN": 5300,
    "AFT_GEN": 4200,
    "PORT_SHAFT_GEN": 2100,
    "STBD_SHAFT_GEN": 2100,
}
N2_FUEL = {
    "PME": 12000,
    "SME": 9200,
    "FWD_GEN": 5600,
    "AFT_GEN": 4400,
    "PORT_SHAFT_GEN": 2200,
    "STBD_SHAFT_GEN": 2200,
}
ARR_FUEL = {
    "PME": 12500,
    "SME": 9500,
    "FWD_GEN": 5750,
    "AFT_GEN": 4500,
    "PORT_SHAFT_GEN": 2250,
    "STBD_SHAFT_GEN": 2250,
}

AUTHOR = SimpleNamespace(id=1, full_name="Master Test", username="master", role="marins")


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
    invalidate_cache()
    referential_env.invalidate_emission_factor_cache()  # facteurs vides → repli 3,206
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        invalidate_cache()
        referential_env.invalidate_emission_factor_cache()


async def _base(db):
    vessel = Vessel(code="ANE", name="Anemos", imo_number="9876543")
    db.add(vessel)
    await db.flush()
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC", latitude=49.7, longitude=0.37)
    p2 = Port(name="Belem", country="BR", locode="BRBEL", latitude=-1.45, longitude=-48.5)
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
    )
    db.add(leg)
    await db.flush()
    db.add(User(id=1, username="master", email="m@t.test", hashed_password="x", role="marins"))
    await db.flush()
    return vessel, leg, engines


def _readings(engines, fuel_map):
    return [
        NavEventEngineReading(
            engine_id=engines[role].id,
            fuel_counter_l=Decimal(str(fuel)),
            is_counter_reset=False,
        )
        for role, fuel in fuel_map.items()
    ]


async def _reference_chain(db, vessel, leg, engines):
    """Dep(rob 100, laden) + 2 Noon + Arr — chaîne CFOTE_05 de référence."""
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
    dep.engine_readings = _readings(engines, DEP_FUEL)
    n1 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
        distance_to_go_nm=Decimal("2000"),
    )
    n1.engine_readings = _readings(engines, N1_FUEL)
    n2 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        lat_decimal=Decimal("44.0"),
        lon_decimal=Decimal("-5.0"),
    )
    n2.engine_readings = _readings(engines, N2_FUEL)
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=60),
        lat_decimal=Decimal("42.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("95.564"),
        vessel_condition="laden",
    )
    arr.engine_readings = _readings(engines, ARR_FUEL)
    db.add_all([dep, n1, n2, arr])
    await db.flush()
    return dep, n1, n2, arr


# ════════════════════════════════════════════════════════════ Noon


async def test_generate_noon_payload_from_two_event_chain(db):
    vessel, leg, engines = await _base(db)
    dep, n1, _n2, _arr = await _reference_chain(db, vessel, leg, engines)

    report = await rg.generate_noon_report(db, leg, n1, author_user_id=1)
    assert report.report_type == "noon"
    assert report.status == "brouillon"

    iv = report.payload["interval"]
    # Intervalle Dep→Noon1 : ME=1,352 / AE=0,4225 / total=1,7745 (CFOTE_05).
    assert Decimal(iv["conso_me_t"]) == Decimal("1.352")
    assert Decimal(iv["conso_ae_t"]) == Decimal("0.4225")
    assert Decimal(iv["conso_total_t"]) == Decimal("1.7745")
    assert 170 < float(iv["distance_nm"]) < 190
    assert Decimal(iv["duration_h"]) == Decimal("24")
    # ROB chaîné au Noon1 : 100 − 1,7745 = 98,2255.
    assert Decimal(report.payload["rob"]["calculated_t"]) == Decimal("98.2255")
    assert report.payload["previous_event_id"] == dep.id

    # Liens report ↔ événements : le Noon + son prédécesseur.
    assert {lk.event_id for lk in report.event_links} == {n1.id, dep.id}


# ════════════════════════════════════════════════════════════ Carbon


async def test_generate_carbon_multi_ghg_exact(db):
    vessel, leg, engines = await _base(db)
    await _reference_chain(db, vessel, leg, engines)

    report = await rg.generate_carbon_report(db, leg, author_user_id=1)
    totals = report.payload["totals"]
    # Sans mouillage : assiette = total = 4,43625 t.
    assert Decimal(totals["conso_total_t"]) == Decimal("4.43625")
    assert Decimal(totals["conso_mouillage_t"]) == Decimal("0")
    assert Decimal(totals["conso_hors_mouillage_t"]) == Decimal("4.43625")
    assert Decimal(totals["conso_me_t"]) == Decimal("3.380")
    assert Decimal(totals["conso_ae_t"]) == Decimal("1.05625")

    em = report.payload["emissions"]
    # CO₂ TtW = 4,43625 × 3,206 = 14,2226175 t.
    assert Decimal(em["co2_t"]) == Decimal("4.43625") * Decimal("3.206")
    assert Decimal(em["co2_t"]).quantize(Decimal("0.0001")) == Decimal("14.2226")
    # CH₄/N₂O en GRAMMES = conso × ef × 1e6 ; jamais sommés au CO₂ (t).
    assert Decimal(em["ch4_g"]) == Decimal("4.43625") * Decimal("0.00005") * Decimal("1000000")
    assert Decimal(em["n2o_g"]) == Decimal("4.43625") * Decimal("0.00018") * Decimal("1000000")
    # WtT distinct (facteur amont exposé, non additionné au TtW).
    assert Decimal(em["wtt_gco2eq_per_mj"]) == Decimal("17.7")
    assert Decimal(em["wtt_co2eq_t"]) > 0
    # Cargo B/L vs MRV distincts.
    assert Decimal(report.payload["cargo"]["cargo_bl_t"]) == Decimal("900.000")
    # Liens : tous les événements du voyage.
    assert len(report.event_links) == 4


async def test_carbon_assiette_excludes_anchoring(db):
    vessel, leg, engines = await _base(db)
    # Dep → Noon → Begin → End → Arr ; seul PME consomme (ME).
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("100"),
        vessel_condition="laden",
    )
    dep.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("10000"))
    ]
    noon = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("48.0"),
        lon_decimal=Decimal("-5.0"),
    )
    noon.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("11000"))
    ]  # Δ1000
    begin = BeginAnchoringEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=30),
        sequence_no=1,
    )
    begin.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("11500"))
    ]  # Δ500
    end = EndAnchoringEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=36),
        sequence_no=1,
    )
    end.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("11800"))
    ]  # Δ300 (mouillage)
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=48),
        lat_decimal=Decimal("42.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("98"),
        vessel_condition="laden",
    )
    arr.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("12000"))
    ]  # Δ200
    db.add_all([dep, noon, begin, end, arr])
    await db.flush()
    end.paired_event_id = begin.id
    await db.flush()

    report = await rg.generate_carbon_report(db, leg, author_user_id=1)
    totals = report.payload["totals"]
    # Total = (1000+500+300+200) L ; mouillage = intervalle Begin→End (300 L).
    assert Decimal(totals["conso_total_t"]) == Decimal("2000") * FACTOR
    assert Decimal(totals["conso_mouillage_t"]) == Decimal("300") * FACTOR
    assert Decimal(totals["conso_hors_mouillage_t"]) == Decimal("1700") * FACTOR
    # CO₂ calculé sur l'assiette HORS mouillage.
    assert Decimal(report.payload["emissions"]["co2_t"]) == (Decimal("1700") * FACTOR) * Decimal(
        "3.206"
    )
    assert any(a["sequence_no"] == 1 for a in report.payload["anchorings"])


# ════════════════════════════════════════════════════════════ Stopover


async def _portcall(cls, leg, vessel, engines, *, dt, rob, fuel_pme):
    ev = cls(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=dt,
        rob_t=rob,
        vessel_condition="laden",
    )
    ev.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal(str(fuel_pme)))
    ]
    return ev


@pytest.mark.parametrize(
    "rob_departure,expected",
    [
        (Decimal("49.831"), "conforme"),  # écart 0
        (Decimal("48.831"), "mineur"),  # écart 1,0 (0,5 < e ≤ 2)
        (Decimal("46.831"), "majeur"),  # écart 3,0 (2 < e ≤ 5)
        (Decimal("39.831"), "critique"),  # écart 10,0 (> 5)
    ],
)
async def test_stopover_ecart_four_levels(db, rob_departure, expected):
    vessel, leg, engines = await _base(db)
    # conso escale = 200 L × 0,000845 = 0,169 t ; ROB théorique départ = 50 − 0,169 = 49,831.
    hour = {"conforme": 1, "mineur": 2, "majeur": 3, "critique": 4}[expected]
    arr = await _portcall(
        ArrivalEvent,
        leg,
        vessel,
        engines,
        dt=T0 + timedelta(hours=hour),
        rob=Decimal("50.000"),
        fuel_pme=1000,
    )
    dep = await _portcall(
        DepartureEvent,
        leg,
        vessel,
        engines,
        dt=T0 + timedelta(hours=hour + 12),
        rob=rob_departure,
        fuel_pme=1200,
    )
    db.add_all([arr, dep])
    await db.flush()

    report = await rg.generate_stopover_report(db, arr, dep, author_user_id=1)
    rc = report.payload["rob_check"]
    assert Decimal(report.payload["consumption"]["conso_escale_t"]) == Decimal("200") * FACTOR
    assert Decimal(rc["theoretical_departure_t"]) == Decimal("50.000") - Decimal("200") * FACTOR
    assert rc["classification"] == expected


# ════════════════════════════════════════════════════════════ Cycle de vie


async def test_lifecycle_regenerate_draft_ok_validated_refused(db):
    vessel, leg, engines = await _base(db)
    await _reference_chain(db, vessel, leg, engines)

    r1 = await rg.generate_carbon_report(db, leg, author_user_id=1)
    rid = r1.id
    # Regénération en brouillon : remplace le payload (même rapport).
    r2 = await rg.generate_carbon_report(db, leg, author_user_id=1)
    assert r2.id == rid
    assert r2.status == "brouillon"

    # Validé Master → immuable : regénération refusée.
    await rg.validate_master(db, r2, AUTHOR)
    assert r2.status == "valide_master"
    with pytest.raises(rg.ReportImmutableError):
        await rg.generate_carbon_report(db, leg, author_user_id=1)


async def test_validate_siege_reserved_to_carbon(db):
    vessel, leg, engines = await _base(db)
    dep, n1, _n2, _arr = await _reference_chain(db, vessel, leg, engines)

    noon = await rg.generate_noon_report(db, leg, n1, author_user_id=1)
    await rg.validate_master(db, noon, AUTHOR)
    # Validation siège sur un Noon → refus propre.
    with pytest.raises(rg.SiegeValidationNotAllowedError):
        await rg.validate_siege(db, noon, AUTHOR)

    carbon = await rg.generate_carbon_report(db, leg, author_user_id=1)
    await rg.validate_master(db, carbon, AUTHOR)
    await rg.validate_siege(db, carbon, AUTHOR)
    assert carbon.status == "valide_siege"


# ════════════════════════════════════════════════════════════ Field modification


async def test_field_modification_requires_justification(db):
    vessel, leg, engines = await _base(db)
    await _reference_chain(db, vessel, leg, engines)
    report = await rg.generate_carbon_report(db, leg, author_user_id=1)
    await rg.validate_master(db, report, AUTHOR)

    # R18 — justification vide refusée.
    with pytest.raises(rg.JustificationRequiredError):
        await rg.apply_field_modification(
            db, report, "cargo_bl_t", "910", "   ", AUTHOR, "corrected"
        )


async def test_field_modification_derives_worst_quality_status(db):
    vessel, leg, engines = await _base(db)
    await _reference_chain(db, vessel, leg, engines)
    report = await rg.generate_carbon_report(db, leg, author_user_id=1)
    await rg.validate_master(db, report, AUTHOR)

    await rg.apply_field_modification(
        db, report, "cargo_bl_t", "910", "Correction du B/L", AUTHOR, "corrected"
    )
    await rg.apply_field_modification(
        db, report, "distance_nm", "1900", "Écart non résolu", AUTHOR, "under_conformity"
    )
    # Le payload reflète la correction (snapshot mis à jour).
    assert report.payload["cargo_bl_t"] == "910"
    # Statut dérivé = pire cas.
    assert await rg.report_quality_status(db, report.id) == "under_conformity"
    assert report.quality_status == "under_conformity"
