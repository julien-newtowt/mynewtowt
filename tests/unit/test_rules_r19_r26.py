"""LOT 8 — règles R19-R26 : tests table-driven.

- **R19** : PORTÉE PAR ``services.draft_reminders`` (lot 4) — volontairement
  HORS registre ``RULES`` (un doublon re-notifierait les Masters à chaque run) ;
  ici : test documentaire (seedée au catalogue, service en place, testé dans
  ``test_draft_reminders.py``).
- **R20** (Info tant que D10), **R21** (durée entre rapports), **R22** (Carbon
  vs Noon — le Carbon n'est JAMAIS correcteur), **R23** (masse/volumes soutage,
  capacités en Info Q11), **R24** (soutage sans FLGO Received → warning routé
  admin), **R25** (2 volets FLGO), **R26** (chaînage des voyages) — ≥ 3 cas
  chacun (pass / fail / limite exacte).
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
from app.models.bunker import BunkerOperation, BunkerTankAllocation
from app.models.env_report import EnvReport
from app.models.flgo import FlgoReading
from app.models.leg import Leg
from app.models.nav_event import DepartureEvent
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.vessel_env import VesselTank
from app.services.validation_engine import (
    RULE_SEED,
    RULES,
    RuleContext,
    invalidate_cache,
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


def _ctx(db, rid, subjects, index=0, *, vessel=None, leg=None, now=NOW) -> RuleContext:
    return RuleContext(db=db, rule_id=rid, subject=subjects[index],
                       subjects=list(subjects), index=index, now=now,
                       vessel=vessel, leg=leg)


async def _run(db, rid, subjects, index=0, **kw):
    return await RULES[rid](_ctx(db, rid, subjects, index, **kw))


async def _vessel(db, code="ANE"):
    v = Vessel(code=code, name="Anemos")
    db.add(v)
    await db.flush()
    return v


async def _leg(db, vessel, *, code="1AFRBR6", dep="FRFEC", arr="BRBEL",
               dep_c="FR", arr_c="BR", etd=T0, status="planned"):
    from sqlalchemy import select

    async def _port(locode, country):
        existing = (
            await db.execute(select(Port).where(Port.locode == locode))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        p = Port(name=locode, country=country, locode=locode)
        db.add(p)
        await db.flush()
        return p

    p1 = await _port(dep, dep_c)
    p2 = await _port(arr, arr_c)
    leg = Leg(leg_code=code, vessel_id=vessel.id,
              departure_port_id=p1.id, arrival_port_id=p2.id,
              etd_ref=etd, eta_ref=etd + timedelta(days=5),
              etd=etd, eta=etd + timedelta(days=5), status=status)
    db.add(leg)
    await db.flush()
    return leg


# ═════════════════════════════════════════════ R19 — documentaire (lot 4)


def test_r19_carried_by_draft_reminders_not_registry():
    """R19 vit dans ``services.draft_reminders`` (rappel Master 24 h + alerte
    siège 2ᵉ seuil, cron /api/mrv/draft-reminders, idempotent) — pas dans le
    registre : une règle doublon re-notifierait à chaque run nocturne."""
    assert "R19" not in RULES
    assert any(r[0] == "R19" for r in RULE_SEED)  # seedée au catalogue (journal)
    from app.services import draft_reminders

    assert callable(draft_reminders.run_draft_reminders)
    assert draft_reminders.SIEGE_MRV_ROLES  # routage siège en place


# ═════════════════════════════════════════════ R20 — Cargo MRV vs B/L (Info)


async def _r20_leg(db, *, cargo_bl, cargo_mrv, condition="laden"):
    vessel = await _vessel(db)
    leg = await _leg(db, vessel)
    dep = DepartureEvent(
        leg_id=leg.id, vessel_id=vessel.id, status="finalise", datetime_utc=T0,
        rob_t=Decimal("100"), vessel_condition=condition,
        cargo_bl_t=cargo_bl, cargo_mrv_t=cargo_mrv,
    )
    db.add(dep)
    await db.flush()
    return vessel, leg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cargo_mrv,result",
    [
        (Decimal("950"), "pass"),   # MRV ≥ B/L
        (Decimal("895"), "pass"),   # limite : 895 + seuil 5 == 900 (non <)
        (Decimal("894"), "fail"),   # 894 + 5 < 900
    ],
)
async def test_r20_cargo_mrv_vs_bl_exact_boundary(db, cargo_mrv, result):
    vessel, leg = await _r20_leg(db, cargo_bl=Decimal("900"), cargo_mrv=cargo_mrv)
    out = await _run(db, "R20", [leg], vessel=vessel, leg=leg)
    assert out[0].result == result
    if result == "fail":
        # Sévérité Info ACTÉE tant que D10 (rattachement commercial) n'est pas
        # câblé au certificat (lot 9) — cf. Matrice §3 (R20).
        assert out[0].severity == "info"
        assert out[0].details["d10_pending"] is True


@pytest.mark.asyncio
async def test_r20_ballast_voyage_out_of_scope(db):
    vessel, leg = await _r20_leg(db, cargo_bl=Decimal("900"),
                                 cargo_mrv=Decimal("0"), condition="ballast")
    assert await _run(db, "R20", [leg], vessel=vessel, leg=leg) == []


# ═════════════════════════════════════════════ R21 — durée entre rapports


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declared_h,result",
    [
        (Decimal("24"), "pass"),
        (Decimal("26"), "pass"),   # limite : |26 − 24| == tolérance 2
        (Decimal("27"), "fail"),
    ],
)
async def test_r21_declared_vs_real_duration_exact_boundary(db, declared_h, result):
    prev = SimpleNamespace(event_type="noon", datetime_utc=T0)
    cur = SimpleNamespace(event_type="noon", datetime_utc=T0 + timedelta(hours=24),
                          time_from_last_report_h=declared_h)
    out = await _run(db, "R21", [prev, cur], 1)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r21_derives_from_sosp_cumulative(db):
    """Sans durée déclarée directe, R21 dérive du delta ``time_from_sosp_h``."""
    prev = SimpleNamespace(event_type="noon", datetime_utc=T0,
                           time_from_sosp_h=Decimal("10"))
    cur = SimpleNamespace(event_type="noon", datetime_utc=T0 + timedelta(hours=24),
                          time_from_sosp_h=Decimal("40"))  # Δ déclaré = 30 vs réel 24
    out = await _run(db, "R21", [prev, cur], 1)
    assert out[0].result == "fail"


@pytest.mark.asyncio
async def test_r21_abstains_without_declared_duration(db):
    prev = SimpleNamespace(event_type="noon", datetime_utc=T0)
    cur = SimpleNamespace(event_type="noon", datetime_utc=T0 + timedelta(hours=24))
    assert await _run(db, "R21", [prev, cur], 1) == []


# ═════════════════════════════════════════════ R22 — Carbon vs Noon


async def _r22_reports(db, *, carbon_total, noon_consos):
    vessel = await _vessel(db)
    leg = await _leg(db, vessel)
    carbon = EnvReport(leg_id=leg.id, report_type="carbon", status="valide_master",
                       payload={"totals": {"conso_total_t": str(carbon_total)}})
    db.add(carbon)
    for c in noon_consos:
        db.add(EnvReport(leg_id=leg.id, report_type="noon", status="valide_master",
                         payload={"interval": {"conso_total_t": str(c)}}))
    await db.flush()
    return leg, carbon


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "noon_consos,result",
    [
        ([Decimal("5"), Decimal("4.5")], "pass"),   # Σ 9,5 → écart 0,5 ≤ 1
        ([Decimal("5"), Decimal("4")], "pass"),     # limite : écart 1 == tolérance
        ([Decimal("5"), Decimal("3")], "fail"),     # écart 2 > 1
    ],
)
async def test_r22_carbon_vs_noon_exact_boundary(db, noon_consos, result):
    leg, carbon = await _r22_reports(db, carbon_total=Decimal("10"),
                                     noon_consos=noon_consos)
    out = await _run(db, "R22", [carbon], leg=leg)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"
        # Arbitrage acté : SIGNALER, ne jamais corriger depuis le Carbon.
        assert out[0].details["carbon_corrector"] is False
        assert "jamais correcteur" in out[0].message


@pytest.mark.asyncio
async def test_r22_abstains_without_noon_reports_or_on_noon_subject(db):
    leg, carbon = await _r22_reports(db, carbon_total=Decimal("10"), noon_consos=[])
    assert await _run(db, "R22", [carbon], leg=leg) == []
    noon = SimpleNamespace(report_type="noon", leg_id=leg.id, payload={})
    assert await _run(db, "R22", [noon], leg=leg) == []


# ═════════════════════════════════════════════ R23 — soutage masse/volumes


async def _r23_bunker(db, *, mass_t, alloc_volume, alloc_density, capacity=None):
    vessel = await _vessel(db)
    tank = VesselTank(vessel_id=vessel.id, tank_code="14", capacity_m3=capacity)
    db.add(tank)
    await db.flush()
    bunker = BunkerOperation(
        vessel_id=vessel.id, bdn_number="BDN-23", port_locode="FRFEC",
        delivery_datetime_utc=T0, fuel_type="MDO",
        mass_t=mass_t, density_15c_t_m3=Decimal("0.845"), status="brouillon",
    )
    db.add(bunker)
    await db.flush()
    db.add(BunkerTankAllocation(bunker_id=bunker.id, tank_id=tank.id,
                                volume_m3=alloc_volume, density_t_m3=alloc_density))
    await db.flush()
    return vessel, bunker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alloc_volume,result",
    [
        (Decimal("20"), "pass"),   # 20 × 1,0 = 20 t == masse → écart 0
        (Decimal("22"), "pass"),   # limite : écart 2 == tolérance (status ok)
        (Decimal("25"), "fail"),   # écart 5 > 2×tolérance → ecart_majeur
    ],
)
async def test_r23_mass_vs_allocations_exact_boundary(db, alloc_volume, result):
    vessel, bunker = await _r23_bunker(db, mass_t=Decimal("20"),
                                       alloc_volume=alloc_volume,
                                       alloc_density=Decimal("1.0"))
    out = await _run(db, "R23", [bunker], vessel=vessel)
    fails = [o for o in out if o.result == "fail"]
    if result == "pass":
        assert not fails
    else:
        assert fails and fails[0].severity == "warning"


@pytest.mark.asyncio
async def test_r23_capacity_exceeded_is_info_only(db):
    """Volet capacités : dégradé en **Info** tant que les capacités officielles
    manquent (Q11) — écart ASSUMÉ vs Matrice (spécifié bloquant), documenté."""
    vessel, bunker = await _r23_bunker(db, mass_t=Decimal("20"),
                                       alloc_volume=Decimal("20"),
                                       alloc_density=Decimal("1.0"),
                                       capacity=Decimal("15"))
    out = await _run(db, "R23", [bunker], vessel=vessel)
    fails = [o for o in out if o.result == "fail"]
    assert fails
    assert fails[0].severity == "info"
    assert fails[0].details["q11_pending"] is True


# ═════════════════════════════════════════════ R24 — soutage sans FLGO Received


async def _r24_bunker(db, vessel, *, delivery=T0):
    bunker = BunkerOperation(
        vessel_id=vessel.id, bdn_number="BDN-36039", port_locode="FRFEC",
        delivery_datetime_utc=delivery, fuel_type="MDO",
        mass_t=Decimal("20"), density_15c_t_m3=Decimal("0.845"), status="valide_master",
    )
    db.add(bunker)
    await db.flush()
    return bunker


async def _received(db, vessel, at):
    db.add(FlgoReading(vessel_id=vessel.id, action_type="received",
                       product_name="Diesel Oil", reading_datetime=at,
                       total_volume_m3=Decimal("20"), total_rob_m3=Decimal("50"),
                       source="api"))
    await db.flush()


@pytest.mark.asyncio
async def test_r24_matched_received_passes(db):
    vessel = await _vessel(db)
    bunker = await _r24_bunker(db, vessel)
    await _received(db, vessel, T0 + timedelta(days=1))
    out = await _run(db, "R24", [bunker], vessel=vessel)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r24_received_at_exact_window_boundary_passes(db):
    """Limite : Received exactement à +5 j (fenêtre inclusive) → recoupé."""
    vessel = await _vessel(db)
    bunker = await _r24_bunker(db, vessel)
    await _received(db, vessel, T0 + timedelta(days=5))
    out = await _run(db, "R24", [bunker], vessel=vessel)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r24_unmatched_bdn_warns_routed_admin(db):
    """Cas réel §3.3 (BDN 36039 Artemis) : soutage sans Received ≤ 5 j →
    warning ROUTÉ Administrateur (complétude manuelle dans Marad)."""
    vessel = await _vessel(db)
    bunker = await _r24_bunker(db, vessel)
    await _received(db, vessel, T0 + timedelta(days=6))  # hors fenêtre
    out = await _run(db, "R24", [bunker], vessel=vessel)
    assert out[0].result == "fail" and out[0].severity == "warning"
    assert out[0].details["route_roles"] == ["administrateur"]
    assert "36039" in out[0].message


# ═════════════════════════════════════════════ R25 — cohérence FLGO (2 volets)


async def _reading(db, vessel, *, at, action="measurement", total=Decimal("30"),
                   rob=Decimal("30"), compartments=()):
    from app.services import flgo_sync as fs

    reading, _created = await fs._upsert_reading(
        db, vessel_id=vessel.id, action_type=action, product_name="Diesel Oil",
        reading_datetime=at, total_volume_m3=total, total_rob_m3=rob,
        remarks=None, source="api",
        compartments=[fs.CompartmentInput(c[0], c[1], None) for c in compartments],
    )
    return reading


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "compartments,result",
    [
        ((("A", Decimal("15")), ("B", Decimal("15"))), "pass"),  # Σ 30 == total
        ((("A", Decimal("14")), ("B", Decimal("14"))), "pass"),  # limite : écart 2 == tol
        ((("A", Decimal("14")), ("B", Decimal("13"))), "fail"),  # écart 3 > 2
    ],
)
async def test_r25_internal_consistency_exact_boundary(db, compartments, result):
    vessel = await _vessel(db)
    reading = await _reading(db, vessel, at=T0, compartments=compartments)
    out = await _run(db, "R25", [reading], vessel=vessel)
    fails = [o for o in out if o.result == "fail"]
    if result == "pass":
        assert not fails
    else:
        assert fails and fails[0].severity == "warning"
        assert fails[0].details["volet"] == "interne"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cur_rob,result,reason",
    [
        (Decimal("29"), "pass", ""),                                # baisse normale
        (Decimal("32"), "pass", ""),                                # limite : +2 == tol
        (Decimal("35"), "fail", "rob_hausse_sans_reception"),       # +5 sans réception
    ],
)
async def test_r25_consecutive_progression_measurement(db, cur_rob, result, reason):
    """Volet 2 (Matrice §5 — réconciliation lot 7) : un jaugeage ne peut pas
    voir le ROB MONTER sans réception intercalée (cas réels §2.7 / §3.2)."""
    vessel = await _vessel(db)
    r1 = await _reading(db, vessel, at=T0, rob=Decimal("30"))
    r2 = await _reading(db, vessel, at=T0 + timedelta(days=1), rob=cur_rob)
    out = await _run(db, "R25", [r1, r2], 1, vessel=vessel)
    fails = [o for o in out if o.result == "fail"]
    if result == "pass":
        assert not fails
    else:
        assert fails and fails[0].details["volet"] == "consecutif"
        assert fails[0].details["reason"] == reason


@pytest.mark.asyncio
async def test_r25_received_progression_checks_received_volume(db):
    """Cas réel §3.2 (Artemis) : Received 38,8 m³ mais ROB n'augmente que de
    ~0 → réception incohérente (signalée, JAMAIS corrigée)."""
    vessel = await _vessel(db)
    r1 = await _reading(db, vessel, at=T0, rob=Decimal("28.6"))
    r2 = await _reading(db, vessel, at=T0 + timedelta(hours=6), action="received",
                        total=Decimal("38.8"), rob=Decimal("28.6"))
    out = await _run(db, "R25", [r1, r2], 1, vessel=vessel)
    fails = [o for o in out if o.result == "fail" and o.details.get("volet") == "consecutif"]
    assert fails and fails[0].details["reason"] == "reception_incoherente"
    # Jamais corrigé : les lectures restent intactes.
    assert r2.total_rob_m3 == Decimal("28.6")


@pytest.mark.asyncio
async def test_r25_coherent_reception_passes(db):
    vessel = await _vessel(db)
    r1 = await _reading(db, vessel, at=T0, rob=Decimal("30"))
    r2 = await _reading(db, vessel, at=T0 + timedelta(hours=6), action="received",
                        total=Decimal("20"), rob=Decimal("50"))  # +20 == volume reçu
    out = await _run(db, "R25", [r1, r2], 1, vessel=vessel)
    assert not [o for o in out if o.result == "fail"]


# ═════════════════════════════════════════════ R26 — chaînage des voyages


@pytest.mark.asyncio
async def test_r26_chained_voyages_pass(db):
    vessel = await _vessel(db)
    leg1 = await _leg(db, vessel, code="1AFRBR6", dep="FRFEC", arr="BRBEL",
                      dep_c="FR", arr_c="BR", etd=T0)
    await _leg(db, vessel, code="1BBRFR6", dep="BRBEL", arr="FRFEC",
               dep_c="BR", arr_c="FR", etd=T0 + timedelta(days=10))
    out = await _run(db, "R26", [leg1], vessel=vessel, leg=leg1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r26_broken_chain_warns(db):
    """Cas réel §3.19-3 : rupture arrivée(N) ≠ départ(N+1) sans voyage
    intermédiaire codifié → code voyage manquant/mal déclaré."""
    vessel = await _vessel(db)
    leg1 = await _leg(db, vessel, code="1AFRBR6", dep="FRFEC", arr="BRBEL",
                      dep_c="FR", arr_c="BR", etd=T0)
    await _leg(db, vessel, code="1BVNFR6", dep="VNSGN", arr="FRFEC",
               dep_c="VN", arr_c="FR", etd=T0 + timedelta(days=10))
    out = await _run(db, "R26", [leg1], vessel=vessel, leg=leg1)
    assert out[0].result == "fail" and out[0].severity == "warning"
    assert out[0].details["arr_locode"] == "BRBEL"
    assert out[0].details["dep_locode"] == "VNSGN"


@pytest.mark.asyncio
async def test_r26_no_next_or_cancelled_next_passes(db):
    vessel = await _vessel(db)
    leg1 = await _leg(db, vessel, code="1AFRBR6", dep="FRFEC", arr="BRBEL",
                      dep_c="FR", arr_c="BR", etd=T0)
    # Aucun voyage suivant → non applicable.
    out = await _run(db, "R26", [leg1], vessel=vessel, leg=leg1)
    assert out[0].result == "pass"
    # Voyage suivant ANNULÉ → ignoré (pas une rupture).
    await _leg(db, vessel, code="1BVNFR6", dep="VNSGN", arr="FRFEC",
               dep_c="VN", arr_c="FR", etd=T0 + timedelta(days=10), status="cancelled")
    out2 = await _run(db, "R26", [leg1], vessel=vessel, leg=leg1)
    assert out2[0].result == "pass"
