"""LOT 8 — règles R14-R18 (cohérence ROB / soutage / FLGO / traçabilité).

Table-driven sur données réelles (SQLite mémoire + FK + référentiel seedé) :

- **R14a/b + hiérarchie v2** : cross-check ``rob_declared_t`` (PortCall
  UNIQUEMENT — jamais Noon) vs ``rob_calculated_t`` (chaîne
  ``inter_event_compute``), classement mineur/majeur/critique via les 3 bornes,
  bloquant si critique ;
- **R15** : conso voyage vs cible (750 L/j) et vs référence FLGO
  (``FlgoVoyageConsumptionRef``) ;
- **R16** : densité BDN dans [défaut ± tolérance] — promotion du contrôle
  ``bunkering.check_density`` en règle persistée ;
- **R17** : ROB déclaré vs FLGO (date la plus proche), déclassé Info au-delà
  de la tolérance temporelle ;
- **R18** : modification sans justification → bloquant (formalisation de la
  garde du service en règle persistée).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.models.bunker import BunkerOperation
from app.models.env_report import EnvFieldModification, EnvReport
from app.models.flgo import FlgoReading, FlgoVoyageConsumptionRef
from app.models.leg import Leg
from app.models.nav_event import ArrivalEvent, DepartureEvent, NavEventEngineReading
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel
from app.services.referential_env import ensure_vessel_env_defaults, get_vessel_engines
from app.services.validation_engine import (
    RULES,
    RuleContext,
    invalidate_cache,
    run_rules,
    seed_reference_data,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine.sync_engine, "connect")
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
    """Vessel + moteurs par défaut + ports + leg + user — socle des cas R14-R17."""
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    await ensure_vessel_env_defaults(db, vessel)
    engines = {e.engine_role: e for e in await get_vessel_engines(db, vessel.id)}
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC")
    p2 = Port(name="Belem", country="BR", locode="BRBEL")
    db.add_all([p1, p2])
    db.add(User(id=1, username="adm", email="a@t.test", hashed_password="x", role="administrateur"))
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


async def _chain(db, vessel, leg, engines, *, dep_rob, arr_rob, delta_l=1000):
    """Departure(rob) + Arrival(rob) à 24 h, PME Δ ``delta_l`` litres.

    conso = Δ × 0,001 × 0,845 ; ROB calculé arrivée = dep_rob − conso.
    """
    dep = DepartureEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0,
        lat_decimal=Decimal("50"),
        lon_decimal=Decimal("-5"),
        rob_t=Decimal(str(dep_rob)),
        vessel_condition="laden",
    )
    dep.engine_readings = [
        NavEventEngineReading(engine_id=engines["PME"].id, fuel_counter_l=Decimal("10000"))
    ]
    arr = ArrivalEvent(
        leg_id=leg.id,
        vessel_id=vessel.id,
        status="finalise",
        datetime_utc=T0 + timedelta(hours=24),
        lat_decimal=Decimal("45"),
        lon_decimal=Decimal("-5"),
        rob_t=(Decimal(str(arr_rob)) if arr_rob is not None else None),
        vessel_condition="laden",
    )
    arr.engine_readings = [
        NavEventEngineReading(
            engine_id=engines["PME"].id, fuel_counter_l=Decimal(str(10000 + delta_l))
        )
    ]
    db.add_all([dep, arr])
    await db.flush()
    return dep, arr


def _ctx(db, rid, leg, *, vessel=None, now=NOW) -> RuleContext:
    return RuleContext(
        db=db, rule_id=rid, subject=leg, subjects=[leg], index=0, now=now, vessel=vessel, leg=leg
    )


# ═════════════════════════════════════════════ R14 — continuité ROB


# conso = 1000 L × 0,000845 = 0,845 t → ROB calculé arrivée = 99,155 t.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arr_rob,expected_class,expected_severity",
    [
        (Decimal("99.155"), None, None),  # écart 0 → conforme
        (Decimal("98.655"), None, None),  # écart 0,5 == mineur → conforme (limite)
        (Decimal("98.155"), "mineur", "warning"),  # écart 1,0
        (Decimal("96.000"), "majeur", "warning"),  # écart 3,155
        (Decimal("90.000"), "critique", "bloquant"),  # écart 9,155 → bloquant
    ],
)
async def test_r14_rob_classification(db, arr_rob, expected_class, expected_severity):
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=100, arr_rob=arr_rob)
    out = await RULES["R14"](_ctx(db, "R14", leg, vessel=vessel))
    if expected_class is None:
        assert out[0].result == "pass"
    else:
        assert out[0].result == "fail"
        assert out[0].severity == expected_severity
        assert out[0].details["classification"] == expected_class
        assert Decimal(out[0].details["rob_declared_t"]) == arr_rob


@pytest.mark.asyncio
async def test_r14_reference_is_portcall_never_noon(db):
    """Hiérarchie v2 : seuls les PortCall portent un ROB déclaré — la chaîne
    n'a AUCUN point Noon à cross-checker (le modèle n'a pas de ROB au Noon)."""
    from app.services import inter_event_compute as iec

    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=100, arr_rob=Decimal("99.155"))
    comp = await iec.compute_leg(db, leg)
    declared_points = [p for p in comp.rob_chain if p.rob_declared_t is not None]
    assert {p.event_type for p in declared_points} == {"departure", "arrival"}


@pytest.mark.asyncio
async def test_r14_persists_with_threshold_snapshot(db):
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=100, arr_rob=Decimal("98.155"))
    summary = await run_rules(db, "voyage", [leg], vessel=vessel, leg=leg, run_id="r14p")
    r14 = [r for r in summary.results if r.rule_id == "R14" and r.result == "fail"]
    assert r14 and r14[0].severity_applied == "warning"
    used = (r14[0].details or {}).get("thresholds_used") or []
    names = {u["parameter_name"] for u in used}
    assert {
        "seuil_rob_ecart_mineur_t",
        "seuil_rob_ecart_majeur_t",
        "seuil_rob_ecart_critique_t",
    } <= names


# ═════════════════════════════════════════════ R15 — conso vs référence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delta_l,result",
    [
        (700, "pass"),
        (750, "pass"),  # limite exacte == 750 L/j
        (751, "fail"),
    ],
)
async def test_r15_daily_target_exact_boundary(db, delta_l, result):
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=100, arr_rob=None, delta_l=delta_l)
    out = await RULES["R15"](_ctx(db, "R15", leg, vessel=vessel))
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r15_flgo_reference_crosscheck(db):
    """Écart conso calculée vs ``FlgoVoyageConsumptionRef`` (CheckConsumption)."""
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=100, arr_rob=None, delta_l=700)
    # conso calculée = 0,5915 t (ME) ; référence 5 t → écart 4,4085 > 2 t.
    db.add(
        FlgoVoyageConsumptionRef(
            leg_id=leg.id, me_consumption_t=Decimal("5"), ae_consumption_t=Decimal("0")
        )
    )
    await db.flush()
    out = await RULES["R15"](_ctx(db, "R15", leg, vessel=vessel))
    fails = [o for o in out if o.result == "fail"]
    assert fails and "référence FLGO" in fails[0].message
    assert fails[0].severity == "warning"


# ═════════════════════════════════════════════ R16 — densité BDN


async def _bunker(db, vessel, density, bdn="BDN-1"):
    b = BunkerOperation(
        vessel_id=vessel.id,
        bdn_number=bdn,
        port_locode="FRFEC",
        delivery_datetime_utc=T0,
        fuel_type="MDO",
        mass_t=Decimal("20"),
        density_15c_t_m3=density,
        status="brouillon",
    )
    db.add(b)
    await db.flush()
    return b


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "density,result",
    [
        (Decimal("0.845"), "pass"),
        (Decimal("0.860"), "pass"),  # limite exacte == borne haute (0,845 + 0,015)
        (Decimal("0.861"), "fail"),
        (Decimal("0.829"), "fail"),  # sous la borne basse (0,830)
    ],
)
async def test_r16_density_bounds(db, density, result):
    vessel, _leg, _engines = await _base(db)
    bunker = await _bunker(db, vessel, density)
    ctx = RuleContext(
        db=db, rule_id="R16", subject=bunker, subjects=[bunker], index=0, now=NOW, vessel=vessel
    )
    out = await RULES["R16"](ctx)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r16_missing_density_flagged(db):
    """Densité absente (sujet transitoire — la colonne DB est NOT NULL) →
    flaggé par ``check_density`` et donc par R16."""
    vessel, _leg, _engines = await _base(db)
    ghost = SimpleNamespace(bdn_number="BDN-X", vessel_id=vessel.id, density_15c_t_m3=None)
    ctx = RuleContext(
        db=db, rule_id="R16", subject=ghost, subjects=[ghost], index=0, now=NOW, vessel=vessel
    )
    out = await RULES["R16"](ctx)
    assert out[0].result == "fail" and out[0].severity == "warning"


# ═════════════════════════════════════════════ R17 — ROB vs FLGO


async def _flgo(db, vessel, *, rob_m3, at):
    r = FlgoReading(
        vessel_id=vessel.id,
        action_type="measurement",
        product_name="Diesel Oil",
        reading_datetime=at,
        total_volume_m3=rob_m3,
        total_rob_m3=rob_m3,
        source="api",
    )
    db.add(r)
    await db.flush()
    return r


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declared_rob,result",
    [
        (Decimal("84.500"), "pass"),  # 100 m³ × 0,845 = 84,5 t — écart 0
        (Decimal("85.000"), "pass"),  # écart 0,5 == mineur (limite incluse)
        (Decimal("86.000"), "fail"),  # écart 1,5 > mineur
    ],
)
async def test_r17_rob_vs_flgo_exact_boundary(db, declared_rob, result):
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=declared_rob, arr_rob=None)
    await _flgo(db, vessel, rob_m3=Decimal("100"), at=T0 + timedelta(hours=1))
    out = await RULES["R17"](_ctx(db, "R17", leg, vessel=vessel))
    fails = [o for o in out if o.result == "fail"]
    if result == "pass":
        assert not fails
    else:
        assert fails and fails[0].severity == "warning"
        assert fails[0].details["within_tolerance"] is True


@pytest.mark.asyncio
async def test_r17_downgraded_to_info_beyond_time_tolerance(db):
    """Matrice §3 (R17 précisé) : lecture FLGO la plus proche à > 120 h →
    rapprochement déclassé Info (peu significatif), jamais warning/bloquant."""
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=Decimal("86"), arr_rob=None)
    await _flgo(db, vessel, rob_m3=Decimal("100"), at=T0 + timedelta(hours=130))
    out = await RULES["R17"](_ctx(db, "R17", leg, vessel=vessel))
    fails = [o for o in out if o.result == "fail"]
    assert fails
    assert fails[0].severity == "info"
    assert fails[0].details["within_tolerance"] is False
    assert "déclassé Info" in fails[0].message


@pytest.mark.asyncio
async def test_r17_no_flgo_passes(db):
    vessel, leg, engines = await _base(db)
    await _chain(db, vessel, leg, engines, dep_rob=Decimal("86"), arr_rob=None)
    out = await RULES["R17"](_ctx(db, "R17", leg, vessel=vessel))
    assert out[0].result == "pass"


# ═════════════════════════════════════════════ R18 — modification justifiée


async def _report(db, leg, payload=None):
    r = EnvReport(
        leg_id=leg.id, report_type="carbon", status="valide_master", payload=payload or {}
    )
    db.add(r)
    await db.flush()
    return r


@pytest.mark.asyncio
async def test_r18_no_modification_passes(db):
    _vessel, leg, _engines = await _base(db)
    report = await _report(db, leg)
    ctx = RuleContext(db=db, rule_id="R18", subject=report, subjects=[report], index=0, now=NOW)
    assert (await RULES["R18"](ctx))[0].result == "pass"


@pytest.mark.asyncio
async def test_r18_justified_modification_passes(db):
    _vessel, leg, _engines = await _base(db)
    report = await _report(db, leg)
    db.add(
        EnvFieldModification(
            report_id=report.id,
            field_name="cargo_bl_t",
            justification_text="Correction B/L",
            resulting_quality_status="corrected",
        )
    )
    await db.flush()
    ctx = RuleContext(db=db, rule_id="R18", subject=report, subjects=[report], index=0, now=NOW)
    assert (await RULES["R18"](ctx))[0].result == "pass"


@pytest.mark.asyncio
async def test_r18_unjustified_modification_blocking(db):
    """Défense en profondeur : une ``EnvFieldModification`` à justification
    vide (donnée corrompue/importée hors service) est détectée bloquante par
    la règle persistée — la garde applicative (R18 service) reste en place."""
    _vessel, leg, _engines = await _base(db)
    report = await _report(db, leg)
    db.add(
        EnvFieldModification(
            report_id=report.id,
            field_name="distance_nm",
            justification_text="   ",
            resulting_quality_status="corrected",
        )
    )
    await db.flush()
    ctx = RuleContext(db=db, rule_id="R18", subject=report, subjects=[report], index=0, now=NOW)
    out = await RULES["R18"](ctx)
    assert out[0].result == "fail" and out[0].severity == "bloquant"
    assert out[0].details["fields"] == ["distance_nm"]


@pytest.mark.asyncio
async def test_r18_abstains_on_non_report_subject(db):
    ctx = RuleContext(
        db=db,
        rule_id="R18",
        subject=SimpleNamespace(mass_t=1),
        subjects=[SimpleNamespace(mass_t=1)],
        index=0,
        now=NOW,
    )
    assert await RULES["R18"](ctx) == []
