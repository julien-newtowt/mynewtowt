"""Tests — calculs inter-événements MRV (LOT 3, les plus lourds du chantier).

Couvre ``app.services.inter_event_compute`` :

- **chaîne de référence CFOTE_05** (Departure + 2 Noon + Arrival) : conso par
  moteur ``= ΔL × 0,001 × 0,845``, agrégats ME/AE (shaft gens exclus), ROB
  chaîné, distance/vitesse ;
- gestion reset compteur R10 (confirmé ⇒ conso = valeur aval ; non confirmé ⇒
  anomalie, conso None) ;
- brouillon intercalé exclu de la chaîne (CDC §9.1) ;
- cargo MRV (hydrostatiques/interpolation, repli saisie, ballast = 0) ;
- appariement Begin/End mouillage + duration_h.

Moteur SQLite en mémoire (FK activées) + seed du référentiel de validation
(densité R16 = 0,845).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

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
from app.models.vessel_env import VesselHydrostatics
from app.services import inter_event_compute as iec
from app.services.ports import haversine_nm
from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines
from app.services.validation_engine import invalidate_cache, seed_reference_data

# Facteur conso CFOTE_05 : litres → tonnes à densité 0,845 (t/m³).
FACTOR = Decimal("0.001") * Decimal("0.845")

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

# Compteurs carburant (litres bruts) par moteur, par événement de la chaîne.
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
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        invalidate_cache()


async def _base(db):
    vessel = Vessel(code="ANE", name="Anemos")
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
    return vessel, leg, engines


def _readings(engines, fuel_map, *, hours_map=None):
    out = []
    for role, fuel in fuel_map.items():
        out.append(
            NavEventEngineReading(
                engine_id=engines[role].id,
                fuel_counter_l=Decimal(str(fuel)),
                running_hours_counter_h=(Decimal(str(hours_map[role])) if hours_map else None),
                is_counter_reset=False,
            )
        )
    return out


# ════════════════════════════════════════════ Chaîne de référence CFOTE_05


async def test_cfote05_chain_full(db):
    vessel, leg, engines = await _base(db)
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("100.000"),
        vessel_condition="laden",
    )
    dep.engine_readings = _readings(engines, DEP_FUEL)
    n1 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
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
    )
    arr.engine_readings = _readings(engines, ARR_FUEL)
    db.add_all([dep, n1, n2, arr])
    await db.flush()

    comp = await iec.compute_leg(db, leg)

    # 4 événements finalisés, ordonnés.
    assert [type(e).__name__ for e in comp.events] == [
        "DepartureEvent",
        "NoonEvent",
        "NoonEvent",
        "ArrivalEvent",
    ]
    assert len(comp.intervals) == 3

    # ── Intervalle Dep→Noon1 : conso par moteur = ΔL × 0,000845 ──────────
    i0 = comp.intervals[0]
    pme = engines["PME"].id
    sme = engines["SME"].id
    fwd = engines["FWD_GEN"].id
    aft = engines["AFT_GEN"].id
    psg = engines["PORT_SHAFT_GEN"].id

    assert i0.engines[pme].conso_t == Decimal("1000") * FACTOR  # 0.845
    assert i0.engines[sme].conso_t == Decimal("600") * FACTOR  # 0.507
    assert i0.engines[fwd].conso_t == Decimal("300") * FACTOR  # 0.2535
    assert i0.engines[aft].conso_t == Decimal("200") * FACTOR  # 0.169
    # « exacte au centième » (arrondi commercial ROUND_HALF_UP : 0,845 → 0,85).
    assert i0.engines[pme].conso_t.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(
        "0.85"
    )

    # Agrégats ME/AE + total (shaft gens exclus).
    assert i0.group_conso_t["ME"] == Decimal("1.352")
    assert i0.group_conso_t["AE"] == Decimal("0.4225")
    assert i0.total_conso_t == Decimal("1.7745")
    # Les lignes d'arbre consomment mais n'entrent PAS dans les totaux.
    assert i0.engines[psg].conso_t == Decimal("100") * FACTOR
    assert i0.engines[psg].engine_group is None
    assert i0.total_conso_t == i0.group_conso_t["ME"] + i0.group_conso_t["AE"]

    # ── Distance / vitesse cohérentes (haversine, 3° de latitude) ────────
    expected_d = Decimal(str(haversine_nm(50.0, -5.0, 47.0, -5.0)))
    assert i0.distance_nm == expected_d
    assert 170 < float(i0.distance_nm) < 190  # ~180 nm
    assert i0.duration_h == Decimal("24")
    assert i0.speed_kn == expected_d / Decimal("24")

    # ── ROB chaîné exact au centième ─────────────────────────────────────
    rob = {p.event_id: p.rob_calculated_t for p in comp.rob_chain}
    assert rob[dep.id] == Decimal("100.000")
    assert rob[n1.id] == Decimal("98.2255")  # 100 − 1.7745
    assert rob[n2.id] == Decimal("96.451")  # − 1.7745
    assert rob[arr.id] == Decimal("95.56375")  # − 0.88725
    assert rob[arr.id].quantize(Decimal("0.01")) == Decimal("95.56")

    # ── Totaux du leg ────────────────────────────────────────────────────
    assert comp.totals.conso_me_t == Decimal("3.380")
    assert comp.totals.conso_ae_t == Decimal("1.05625")
    assert comp.totals.conso_total_t == Decimal("4.43625")


# ════════════════════════════════════════════ Reset compteur (R10)


def _two_events(engines, prev_fuel, cur_fuel, *, reset, reset_by):
    prev = NoonEvent(datetime_utc=T0, lat_decimal=Decimal("50.0"), lon_decimal=Decimal("-5.0"))
    prev.engine_readings = [
        NavEventEngineReading(
            engine_id=engines["PME"].id,
            fuel_counter_l=Decimal(str(prev_fuel)),
            is_counter_reset=False,
        )
    ]
    cur = NoonEvent(
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
    )
    cur.engine_readings = [
        NavEventEngineReading(
            engine_id=engines["PME"].id,
            fuel_counter_l=Decimal(str(cur_fuel)),
            is_counter_reset=reset,
            reset_confirmed_by=reset_by,
        )
    ]
    return prev, cur


async def test_counter_reset_confirmed_uses_downstream_value(db):
    vessel, leg, engines = await _base(db)
    user = User(username="admin", email="a@t.test", hashed_password="x", role="administrateur")
    db.add(user)
    await db.flush()
    # Compteur : 5000 → 200 (chute) mais reset CONFIRMÉ par l'admin.
    prev, cur = _two_events(engines, 5000, 200, reset=True, reset_by=user.id)
    res = iec.compute_interval(prev, cur, {engines["PME"].id: engines["PME"]}, Decimal("0.845"))
    ec = res.engines[engines["PME"].id]
    assert ec.reset_applied is True
    assert ec.counter_anomaly is False
    # conso = valeur aval (le compteur est reparti de ~0) = 200 × 0,000845.
    assert ec.conso_t == Decimal("200") * FACTOR
    assert res.counter_anomaly is False
    assert res.total_conso_t == Decimal("200") * FACTOR


async def test_counter_reset_unconfirmed_flags_anomaly(db):
    vessel, leg, engines = await _base(db)
    prev, cur = _two_events(engines, 5000, 200, reset=False, reset_by=None)
    res = iec.compute_interval(prev, cur, {engines["PME"].id: engines["PME"]}, Decimal("0.845"))
    ec = res.engines[engines["PME"].id]
    assert ec.conso_t is None
    assert ec.counter_anomaly is True
    assert res.counter_anomaly is True
    assert res.total_conso_t is None


# ════════════════════════════════════════════ Brouillon exclu (CDC §9.1)


async def test_draft_event_excluded_from_chain(db):
    vessel, leg, engines = await _base(db)
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50.0"),
        lon_decimal=Decimal("-5.0"),
        rob_t=Decimal("100"),
    )
    dep.engine_readings = _readings(engines, DEP_FUEL)
    n1 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("47.0"),
        lon_decimal=Decimal("-5.0"),
    )
    n1.engine_readings = _readings(engines, N1_FUEL)
    # Brouillon INTERCALÉ (entre Noon1 et Noon2) — doit être ignoré.
    draft = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="brouillon",
        datetime_utc=T0 + timedelta(hours=36),
        lat_decimal=Decimal("45.0"),
        lon_decimal=Decimal("-5.0"),
    )
    draft.engine_readings = _readings(engines, N2_FUEL)
    n2 = NoonEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="valide",
        datetime_utc=T0 + timedelta(hours=48),
        lat_decimal=Decimal("44.0"),
        lon_decimal=Decimal("-5.0"),
    )
    n2.engine_readings = _readings(engines, N2_FUEL)
    db.add_all([dep, n1, draft, n2])
    await db.flush()

    comp = await iec.compute_leg(db, leg)
    ids = [e.id for e in comp.events]
    assert draft.id not in ids
    assert ids == [dep.id, n1.id, n2.id]
    assert len(comp.intervals) == 2


# ════════════════════════════════════════════ Cargo MRV


async def test_cargo_mrv_hydrostatics_interpolation(db):
    vessel, leg, engines = await _base(db)
    vessel.lightweight_t = Decimal("300")
    vessel.water_density_default_t_m3 = Decimal("1.0")  # simplifie : m³ ≡ t
    await db.flush()
    hydro = [
        VesselHydrostatics(
            vessel_id=vessel.id, draft_m=Decimal("4.0"), displacement_m3=Decimal("900")
        ),
        VesselHydrostatics(
            vessel_id=vessel.id, draft_m=Decimal("5.0"), displacement_m3=Decimal("1200")
        ),
    ]
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        vessel_condition="laden",
        draft_fwd_m=Decimal("4.5"),
        draft_aft_m=Decimal("4.5"),
    )
    res = iec.compute_cargo_mrv(dep, vessel, hydro)
    # tirant moyen 4,5 → interpolation 900↔1200 = 1050 m³ ; ×1,0 − 300 = 750.
    assert res.method == "hydrostatics"
    assert res.mean_draft_m == Decimal("4.5")
    assert res.displacement_m3 == Decimal("1050")
    assert res.cargo_mrv_t == Decimal("750.0")


async def test_cargo_mrv_fallback_declared_when_no_hydrostatics(db):
    vessel, leg, engines = await _base(db)
    vessel.lightweight_t = Decimal("300")
    await db.flush()
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        vessel_condition="laden",
        draft_fwd_m=Decimal("4.5"),
        draft_aft_m=Decimal("4.5"),
        cargo_mrv_t=Decimal("512.5"),
    )
    res = iec.compute_cargo_mrv(dep, vessel, [])  # pas d'hydrostatiques
    assert res.method == "declared_fallback"
    assert res.cargo_mrv_t == Decimal("512.5")


async def test_cargo_mrv_ballast_is_zero(db):
    vessel, leg, engines = await _base(db)
    vessel.lightweight_t = Decimal("300")
    vessel.water_density_default_t_m3 = Decimal("1.0")
    await db.flush()
    hydro = [
        VesselHydrostatics(
            vessel_id=vessel.id, draft_m=Decimal("4.0"), displacement_m3=Decimal("900")
        ),
        VesselHydrostatics(
            vessel_id=vessel.id, draft_m=Decimal("5.0"), displacement_m3=Decimal("1200")
        ),
    ]
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        vessel_condition="ballast",
        draft_fwd_m=Decimal("4.5"),
        draft_aft_m=Decimal("4.5"),
    )
    res = iec.compute_cargo_mrv(dep, vessel, hydro)
    assert res.method == "ballast_zero"
    assert res.cargo_mrv_t == Decimal("0")


# ════════════════════════════════════════════ Appariement mouillage


async def test_anchoring_pairing_and_duration(db):
    vessel, leg, engines = await _base(db)
    begin = BeginAnchoringEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=6),
        sequence_no=1,
        reason="météo",
    )
    end = EndAnchoringEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=10),
        sequence_no=1,
    )
    db.add_all([begin, end])
    await db.flush()
    end.paired_event_id = begin.id
    await db.flush()

    events = await iec.finalized_events_for_leg(db, leg.id)
    pairs = iec.pair_anchorings(events)
    assert len(pairs) == 1
    assert pairs[0].begin_event_id == begin.id
    assert pairs[0].end_event_id == end.id
    assert pairs[0].sequence_no == 1
    assert pairs[0].duration_h == Decimal("4")  # 10:00 − 06:00

    # Appariement par sequence_no si paired_event_id absent.
    assert iec.anchoring_duration_h(begin, end) == Decimal("4")
