"""LOT 8 — règles R08-R13 (scope event, mesures) : tests table-driven.

R08 (consommation + complétude escale amendée), R09 v1/v2 (distance vs
trajectoire — v1 scopé à la position manuellement justifiée depuis G16 —,
datetime d'escale vs référence), R28 (G4 — distance haversine vs distance
loguée SOSP, Matrice §8), R10 complet (régression compteur :
warning routé admin / reset confirmé / escalade bloquante) — ≥ 3 cas chacun
(pass / fail / limite exacte au seuil). R11/R12/R13 sont déjà couverts
table-driven dans ``tests/unit/test_validation_engine.py`` (lot 2) — leur
sémantique lot 2 est CONSERVÉE (réconciliation documentée dans
``validation_engine`` et ``validation_rules_catalog``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.database import Base
from app.services.validation_engine import (
    RULES,
    RuleContext,
    invalidate_cache,
    run_rules,
    seed_reference_data,
)

T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def _ctx(db, rid, subjects, index, *, vessel=None, leg=None, now=NOW) -> RuleContext:
    return RuleContext(
        db=db,
        rule_id=rid,
        subject=subjects[index],
        subjects=list(subjects),
        index=index,
        now=now,
        vessel=vessel,
        leg=leg,
    )


async def _run(db, rid, subjects, index, **kw):
    return await RULES[rid](_ctx(db, rid, subjects, index, **kw))


def _ev(et, dt, fuel=None, **extra):
    """Sujet événement synthétique — compteur carburant scalaire (litres)."""
    ns = SimpleNamespace(event_type=et, datetime_utc=dt, **extra)
    if fuel is not None:
        ns.fuel_counter_l = Decimal(str(fuel))
        ns.is_counter_reset = extra.get("is_counter_reset", False)
    return ns


# ═════════════════════════════════════════════ R08 — consommation


@pytest.mark.asyncio
async def test_r08_negative_consumption_blocking(db):
    seq = [_ev("noon", T0, fuel=1000), _ev("noon", T0 + timedelta(hours=24), fuel=900)]
    out = await _run(db, "R08", seq, 1)
    assert out[0].result == "fail" and out[0].severity == "bloquant"


@pytest.mark.asyncio
async def test_r08_zero_consumption_on_noon_warns(db):
    seq = [_ev("noon", T0, fuel=1000), _ev("noon", T0 + timedelta(hours=24), fuel=1000)]
    out = await _run(db, "R08", seq, 1)
    assert out[0].result == "fail" and out[0].severity == "warning"
    assert "nulle" in out[0].message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fuel_cur,result",
    [
        (700, "pass"),  # 700 L/j < 750
        (750, "pass"),  # limite exacte == seuil (borne incluse)
        (751, "fail"),  # > seuil cible
    ],
)
async def test_r08_daily_threshold_exact_boundary(db, fuel_cur, result):
    seq = [_ev("noon", T0, fuel=0), _ev("noon", T0 + timedelta(hours=24), fuel=fuel_cur)]
    out = await _run(db, "R08", seq, 1)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r08_amended_port_stay_estimation_traced(db):
    """Amendement R08 (Matrice §5) : escale de 3 j (> 2 j) sans aucune conso →
    estimation par défaut 0,21 t/j TRACÉE dans details (jamais silencieuse)."""
    seq = [
        _ev("arrival", T0, fuel=1000),
        _ev("departure", T0 + timedelta(hours=72), fuel=1000),  # Δ = 0 L sur 3 j
    ]
    out = await _run(db, "R08", seq, 1)
    assert out[0].result == "fail" and out[0].severity == "warning"
    d = out[0].details
    assert d["traced"] is True
    assert Decimal(d["conso_estimee_defaut_t_j"]) == Decimal("0.21")
    assert Decimal(d["conso_estimee_t"]) == Decimal("0.63")  # 0,21 × 3 j


@pytest.mark.asyncio
async def test_r08_short_port_stay_without_conso_passes(db):
    """Escale de 1 j (≤ seuil 2 j) sans conso → pas d'alerte complétude."""
    seq = [
        _ev("arrival", T0, fuel=1000),
        _ev("departure", T0 + timedelta(hours=24), fuel=1000),
    ]
    out = await _run(db, "R08", seq, 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r08_abstains_without_counters_or_prev(db):
    assert await _run(db, "R08", [_ev("noon", T0, fuel=100)], 0) == []
    seq = [
        SimpleNamespace(event_type="noon", datetime_utc=T0),
        SimpleNamespace(event_type="noon", datetime_utc=T0 + timedelta(hours=24)),
    ]
    assert await _run(db, "R08", seq, 1) == []


# ══════════════════ R08 (G2) — compteurs moteur obligatoires hors Noon ══════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("etype", ["departure", "arrival", "anchoring_begin", "anchoring_end"])
async def test_r08_missing_engine_readings_blocks_portcall_and_anchoring(db, etype):
    """G2 — compteurs moteur manquants à Departure/Arrival/Anchoring : bloquant
    dès lors que le navire a des moteurs référencés (sans quoi l'intervalle
    produirait une consommation silencieusement vide, jamais détectée)."""
    from app.models.vessel import Vessel
    from app.models.vessel_env import VesselEngine

    vessel = Vessel(id=1, code="ANE", name="Anemos")
    db.add(vessel)
    await db.flush()
    db.add(VesselEngine(vessel_id=1, engine_role="PME", engine_group="ME"))
    await db.flush()

    subject = SimpleNamespace(event_type=etype, datetime_utc=T0, engine_readings=[])
    out = await _run(db, "R08", [subject], 0, vessel=vessel)
    assert len(out) == 1
    assert out[0].result == "fail" and out[0].severity == "bloquant"
    assert "compteurs moteur" in out[0].message


@pytest.mark.asyncio
async def test_r08_engine_readings_present_lifts_the_gate(db):
    """Des relevés présents lèvent le garde G2 — la règle retombe ensuite sur
    les volets delta habituels (abstention ici, faute de ``prev``)."""
    from app.models.nav_event import NavEventEngineReading
    from app.models.vessel import Vessel
    from app.models.vessel_env import VesselEngine

    vessel = Vessel(id=2, code="ART", name="Artemis")
    db.add(vessel)
    await db.flush()
    engine = VesselEngine(vessel_id=2, engine_role="PME", engine_group="ME")
    db.add(engine)
    await db.flush()

    subject = SimpleNamespace(
        event_type="departure",
        datetime_utc=T0,
        engine_readings=[
            NavEventEngineReading(
                engine_id=engine.id,
                fuel_counter_l=Decimal("1000"),
                running_hours_counter_h=Decimal("500"),
            )
        ],
    )
    assert await _run(db, "R08", [subject], 0, vessel=vessel) == []


@pytest.mark.asyncio
async def test_r08_missing_engine_readings_abstains_without_vessel_or_engines(db):
    """Règle duck-typée (cf. principes du catalogue) : s'abstient si le
    contexte ne permet pas de trancher — pas de navire, ou navire sans aucun
    moteur référencé."""
    from app.models.vessel import Vessel

    subject = SimpleNamespace(event_type="departure", datetime_utc=T0)
    assert await _run(db, "R08", [subject], 0) == []  # pas de vessel dans le contexte

    vessel = Vessel(id=3, code="NOENG", name="No Engine")
    db.add(vessel)
    await db.flush()
    assert await _run(db, "R08", [subject], 0, vessel=vessel) == []  # 0 moteur référencé


# ═════════════════════════════════════════════ R09 — distance / datetime escale


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declared,result",
    [
        (Decimal("5"), "pass"),
        (Decimal("20"), "pass"),  # limite exacte == tolérance (calc = 0 nm)
        (Decimal("20.1"), "fail"),  # au-delà
    ],
)
async def test_r09_v1_declared_vs_computed_distance(db, declared, result):
    """v1 — distance déclarée vs trajectoire calculée, position COURANTE
    manuellement justifiée (G16, Matrice §3) : positions identiques →
    distance calculée = 0 nm exactement, la tolérance devient la borne."""
    prev = _ev("noon", T0, lat_decimal=Decimal("10"), lon_decimal=Decimal("10"))
    cur = _ev(
        "noon",
        T0 + timedelta(hours=24),
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_nm=declared,
        position_source="manuel_justifie",
    )
    out = await _run(db, "R09", [prev, cur], 1)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r09_v1_derives_declared_from_sosp_cumulative(db):
    """NoonEvent réel : pas de ``distance_nm`` direct — la distance déclarée
    de l'intervalle dérive du delta de ``distance_from_sosp_nm`` (cumul)."""
    prev = _ev(
        "noon",
        T0,
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_from_sosp_nm=Decimal("100"),
    )
    cur = _ev(
        "noon",
        T0 + timedelta(hours=24),
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_from_sosp_nm=Decimal("150"),
        position_source="manuel_justifie",
    )  # Δ déclaré 50 nm vs calc 0
    out = await _run(db, "R09", [prev, cur], 1)
    assert out[0].result == "fail" and out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r09_v1_abstains_without_manual_position_source(db):
    """G16 — hors position manuellement justifiée (route normale, y compris
    louvoiement/dérive météo d'une flotte vélique), le volet v1 ne s'applique
    plus (Matrice §3) : aucun faux positif, même avec un écart énorme."""
    prev = _ev("noon", T0, lat_decimal=Decimal("10"), lon_decimal=Decimal("10"))
    cur = _ev(
        "noon",
        T0 + timedelta(hours=24),
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_nm=Decimal("500"),  # écart énorme vs calc = 0 nm
        position_source="thalos_auto",
    )
    out = await _run(db, "R09", [prev, cur], 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gap_h,result",
    [
        (5, "pass"),
        (6, "pass"),  # limite exacte == tolérance
        (7, "fail"),  # au-delà
    ],
)
async def test_r09_v2_portcall_datetime_vs_reference(db, gap_h, result):
    """v2 — datetime d'escale vs référence AIS/SOF (ATD/ETD du leg)."""
    leg = SimpleNamespace(atd=T0, etd=T0, ata=None, eta=None)
    cur = _ev("departure", T0 + timedelta(hours=gap_h))
    out = await _run(db, "R09", [cur], 0, leg=leg)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


# ═════════════════════════════════════ R28 — haversine vs distance loguée (SOSP)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sosp_delta,result",
    [
        (Decimal("5"), "pass"),
        (Decimal("20"), "pass"),  # limite exacte == tolérance (calc = 0 nm)
        (Decimal("20.1"), "fail"),  # au-delà
    ],
)
async def test_r28_haversine_vs_logged_distance(db, sosp_delta, result):
    """Positions identiques → distance haversine = 0 nm exactement, la
    tolérance devient la borne (même patron que R09 v1, seuil distinct)."""
    prev = _ev(
        "noon",
        T0,
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_from_sosp_nm=Decimal("100"),
    )
    cur = _ev(
        "noon",
        T0 + timedelta(hours=24),
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_from_sosp_nm=Decimal("100") + sosp_delta,
    )
    out = await _run(db, "R28", [prev, cur], 1)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"


@pytest.mark.asyncio
async def test_r28_abstains_on_non_noon_event(db):
    """Champ ``distance_from_sosp_nm`` propre au Noon — pas de contrôle sur
    un Departure/Arrival/Anchoring."""
    prev = _ev(
        "noon",
        T0,
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_from_sosp_nm=Decimal("0"),
    )
    cur = _ev(
        "departure", T0 + timedelta(hours=24), lat_decimal=Decimal("10"), lon_decimal=Decimal("10")
    )
    out = await _run(db, "R28", [prev, cur], 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r28_abstains_on_first_event_in_sequence(db):
    cur = _ev(
        "noon",
        T0,
        lat_decimal=Decimal("10"),
        lon_decimal=Decimal("10"),
        distance_from_sosp_nm=Decimal("0"),
    )
    out = await _run(db, "R28", [cur], 0)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r28_abstains_without_logged_sosp_distance(db):
    prev = _ev("noon", T0, lat_decimal=Decimal("10"), lon_decimal=Decimal("10"))
    cur = _ev(
        "noon", T0 + timedelta(hours=24), lat_decimal=Decimal("10"), lon_decimal=Decimal("10")
    )
    out = await _run(db, "R28", [prev, cur], 1)
    assert out[0].result == "pass"


# ═════════════════════════════════════════════ R10 — compteurs (amendé)


@pytest.mark.asyncio
async def test_r10_monotonic_passes(db):
    seq = [
        _ev("noon", NOW - timedelta(hours=25), fuel=1000),
        _ev("noon", NOW - timedelta(hours=1), fuel=1000),
    ]  # Δ = 0 : limite incluse
    out = await _run(db, "R10", seq, 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r10_unconfirmed_regression_warns_and_routes_admin(db):
    """Cas réel : compteur régressant NON confirmé → warning ROUTÉ
    Administrateur (Matrice §3, amendement R10) — plus de blocage automatique."""
    seq = [
        _ev("noon", NOW - timedelta(hours=25), fuel=1000),
        _ev("noon", NOW - timedelta(hours=1), fuel=900),
    ]
    out = await _run(db, "R10", seq, 1)
    assert out[0].result == "fail" and out[0].severity == "warning"
    assert out[0].details["route_roles"] == ["administrateur"]
    assert out[0].details["escalated"] is False


@pytest.mark.asyncio
async def test_r10_confirmed_reset_passes(db):
    """Reset confirmé par l'Administrateur → nouvelle base de référence, R10 passe."""
    cur = _ev("noon", NOW - timedelta(hours=1), fuel=900, is_counter_reset=True)
    cur.reset_confirmed = True
    seq = [_ev("noon", NOW - timedelta(hours=25), fuel=1000), cur]
    out = await _run(db, "R10", seq, 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "age_days,expected_severity",
    [
        (1, "warning"),  # < délai de confirmation (3 j)
        (3, "warning"),  # limite exacte == délai (pas encore escaladé)
        (4, "bloquant"),  # au-delà → escalade (Matrice §3, point 4)
    ],
)
async def test_r10_escalation_after_confirmation_delay(db, age_days, expected_severity):
    dt_cur = NOW - timedelta(days=age_days)
    seq = [_ev("noon", dt_cur - timedelta(hours=24), fuel=1000), _ev("noon", dt_cur, fuel=900)]
    out = await _run(db, "R10", seq, 1)
    assert out[0].result == "fail"
    assert out[0].severity == expected_severity
    assert out[0].details["escalated"] is (expected_severity == "bloquant")


# ═════════════════ R11/R12/R13 — sémantique lot 2 conservée (référence)


@pytest.mark.asyncio
async def test_r11_r12_r13_still_registered_with_lot2_semantics(db):
    """Réconciliation documentée : R11 = bornes plausibles paramétrées, R12 =
    copier-coller, R13 = chronologie — la couverture table-driven vit dans
    ``test_validation_engine.py`` (lot 2). Ici : sanity de non-régression."""
    assert (await _run(db, "R11", [SimpleNamespace(conso_l_j=Decimal("800"))], 0))[
        0
    ].result == "fail"
    a = SimpleNamespace(latitude=49.0, longitude=-1.0)
    b = SimpleNamespace(latitude=49.0, longitude=-1.0)
    assert (await _run(db, "R12", [a, b], 1))[0].result == "fail"
    seq = [SimpleNamespace(recorded_at=T0), SimpleNamespace(recorded_at=T0)]
    assert (await _run(db, "R13", seq, 1))[0].result == "fail"


# ═════════════════ persistance : run event complet sur une séquence


@pytest.mark.asyncio
async def test_run_rules_sequence_snapshots_r08_thresholds(db):
    seq = [
        SimpleNamespace(
            event_type="noon", datetime_utc=T0, fuel_counter_l=Decimal("0"), leg_id=1, vessel_id=1
        ),
        SimpleNamespace(
            event_type="noon",
            datetime_utc=T0 + timedelta(hours=24),
            fuel_counter_l=Decimal("800"),
            leg_id=1,
            vessel_id=1,
        ),
    ]
    summary = await run_rules(db, "event", seq, run_id="r08seq")
    r08 = [r for r in summary.results if r.rule_id == "R08" and r.result == "fail"]
    assert r08 and r08[0].severity_applied == "warning"
    used = (r08[0].details or {}).get("thresholds_used") or []
    assert any(
        u["parameter_name"] == "seuil_conso_ref_l_j" and Decimal(u["value"]) == 750 for u in used
    )
