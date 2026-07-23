"""Tests unitaires du moteur de règles de validation MRV (LOT 2).

Couvre : résolution de seuils (override navire > global > défaut codé,
fail-closed sur DB en erreur), cache + invalidation, chaque règle codée
(R01/R02/R11/R12/R13, table-driven pass/fail/limite — R12 inclut désormais
son volet fréquence météo, G7 ; R29 complétude voilure/température Noon,
G6), persistance de ``run_rules`` avec snapshot des seuils, et la
robustesse d'une règle qui lève une exception (fail/info sans crash).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre les modèles sur Base.metadata
from app.database import Base
from app.models.validation import QualityCheckResult, ValidationRuleThreshold
from app.services.validation_engine import (
    RULES,
    RuleContext,
    get_threshold,
    invalidate_cache,
    run_rules,
    seed_reference_data,
)


@pytest_asyncio.fixture
async def db():
    # Unité : pas d'enforcement FK (isolation) — on exerce la logique du moteur
    # sur des sujets duck-typés référençant des vessel_id/leg_id fictifs, sans
    # matérialiser navires/legs. L'intégrité FK est couverte par l'intégration.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_threshold_cache():
    """Le cache est module-global : on le vide autour de chaque test pour
    éviter toute fuite entre bases in-memory."""
    invalidate_cache()
    yield
    invalidate_cache()


def _ctx(db, rid, subjects, index, *, vessel=None, leg=None) -> RuleContext:
    return RuleContext(
        db=db,
        rule_id=rid,
        subject=subjects[index],
        subjects=list(subjects),
        index=index,
        now=datetime.now(UTC),
        vessel=vessel,
        leg=leg,
    )


async def _run_rule(db, rid, subjects, index, **kw):
    return await RULES[rid](_ctx(db, rid, subjects, index, **kw))


# ───────────────────────── Résolution de seuils ─────────────────────────


@pytest.mark.asyncio
async def test_threshold_resolution_vessel_over_global_over_coded(db):
    await seed_reference_data(db)
    invalidate_cache()
    # Global seedé = 750.
    tv = await get_threshold(db, "R08", "seuil_conso_ref_l_j")
    assert tv is not None and tv.value == Decimal("750") and tv.source == "global"

    # Override navire = 900 (prime sur le global).
    db.add(
        ValidationRuleThreshold(
            rule_id="R08",
            vessel_id=1,
            parameter_name="seuil_conso_ref_l_j",
            value=Decimal("900"),
            unit="L/j",
            provisional=False,
        )
    )
    await db.flush()
    invalidate_cache()
    tv_v = await get_threshold(db, "R08", "seuil_conso_ref_l_j", vessel_id=1)
    assert tv_v.value == Decimal("900") and tv_v.source == "vessel"
    # Sans navire → toujours le global.
    tv_g = await get_threshold(db, "R08", "seuil_conso_ref_l_j")
    assert tv_g.value == Decimal("750") and tv_g.source == "global"


@pytest.mark.asyncio
async def test_threshold_coded_default_when_no_row(db):
    # Aucune ligne en base → repli sur le défaut codé.
    tv = await get_threshold(db, "R08", "seuil_conso_ref_l_j")
    assert tv is not None and tv.value == Decimal("750") and tv.source == "coded_default"
    # Paramètre totalement inconnu → None.
    assert await get_threshold(db, "R08", "parametre_totalement_inconnu") is None


@pytest.mark.asyncio
async def test_threshold_fail_closed_on_db_error():
    """DB en erreur → repli sur le défaut codé (jamais de crash)."""

    class _BrokenDB:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    invalidate_cache()
    tv = await get_threshold(_BrokenDB(), "R08", "seuil_conso_ref_l_j")
    assert tv is not None and tv.value == Decimal("750") and tv.source == "coded_default"


@pytest.mark.asyncio
async def test_cache_holds_until_invalidation(db):
    await seed_reference_data(db)
    invalidate_cache()
    first = await get_threshold(db, "R08", "seuil_conso_ref_l_j")
    assert first.value == Decimal("750")
    # Mutation directe en base SANS invalidation → cache 60 s = valeur ancienne.
    row = (
        await db.execute(
            select(ValidationRuleThreshold).where(
                ValidationRuleThreshold.rule_id == "R08",
                ValidationRuleThreshold.parameter_name == "seuil_conso_ref_l_j",
                ValidationRuleThreshold.vessel_id.is_(None),
            )
        )
    ).scalar_one()
    row.value = Decimal("800")
    await db.flush()
    cached = await get_threshold(db, "R08", "seuil_conso_ref_l_j")
    assert cached.value == Decimal("750")  # encore en cache
    invalidate_cache()
    fresh = await get_threshold(db, "R08", "seuil_conso_ref_l_j")
    assert fresh.value == Decimal("800")  # relu après invalidation


# ─────────────────────────── Règles (table-driven) ───────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject,expected",
    [
        (SimpleNamespace(vessel_id=1, recorded_at=datetime(2026, 4, 1, tzinfo=UTC)), "pass"),
        (SimpleNamespace(vessel_id=None, recorded_at=datetime(2026, 4, 1, tzinfo=UTC)), "fail"),
        (SimpleNamespace(vessel_id=1, recorded_at=None), "fail"),
        (
            SimpleNamespace(vessel_name="Anemos", datetime_utc=datetime(2026, 4, 1, tzinfo=UTC)),
            "pass",
        ),
    ],
)
async def test_r01_required_fields(db, subject, expected):
    out = await _run_rule(db, "R01", [subject], 0)
    assert out[0].result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject,expected",
    [
        (SimpleNamespace(leg_id=1, leg_code="1CFRBR6"), "pass"),  # bien formé
        (SimpleNamespace(leg_id=1, leg_code="1CFRBR"), "fail"),  # 6 caractères
        (SimpleNamespace(leg_id=1, leg_code="ABCDEFG"), "fail"),  # pas de chiffre en tête
        (SimpleNamespace(leg_id=None, leg_code="1CFRBR6"), "fail"),  # pas de voyage
        (SimpleNamespace(leg_id=1), "pass"),  # leg_code absent → OK
    ],
)
async def test_r02_voyage_binding(db, subject, expected):
    out = await _run_rule(db, "R02", [subject], 0)
    assert out[0].result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conso,expected",
    [
        (Decimal("700"), "pass"),  # < 750
        (Decimal("750"), "pass"),  # == 750 (borne incluse)
        (Decimal("800"), "fail"),  # > 750
        (Decimal("-1"), "fail"),  # négatif
    ],
)
async def test_r11_conso_bounds(db, conso, expected):
    await seed_reference_data(db)
    invalidate_cache()
    out = await _run_rule(db, "R11", [SimpleNamespace(conso_l_j=conso)], 0)
    assert out[0].result == expected


@pytest.mark.asyncio
async def test_r11_rob_bound(db):
    await seed_reference_data(db)
    invalidate_cache()
    assert (await _run_rule(db, "R11", [SimpleNamespace(rob_t=Decimal("350"))], 0))[
        0
    ].result == "fail"
    assert (await _run_rule(db, "R11", [SimpleNamespace(rob_t=Decimal("120"))], 0))[
        0
    ].result == "pass"


@pytest.mark.asyncio
async def test_r12_copy_paste(db):
    a = SimpleNamespace(latitude=49.0, longitude=-1.0, rob_t=Decimal("100"))
    b = SimpleNamespace(latitude=49.0, longitude=-1.0, rob_t=Decimal("100"))  # identique
    c = SimpleNamespace(latitude=48.0, longitude=-2.0, rob_t=Decimal("95"))  # différent
    seq = [a, b, c]
    assert (await _run_rule(db, "R12", seq, 0))[0].result == "pass"  # premier
    assert (await _run_rule(db, "R12", seq, 1))[0].result == "fail"  # copié
    assert (await _run_rule(db, "R12", seq, 2))[0].result == "pass"  # distinct


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "n_readings,result",
    [
        (2, "fail"),  # < 3 attendus/jour
        (3, "pass"),  # limite exacte == seuil (borne incluse)
        (6, "pass"),  # tous les créneaux (16/20/00/04/08/12h)
    ],
)
async def test_r12_weather_frequency_below_threshold(db, n_readings, result):
    """G7 — volet fréquence : NoonEvent avec moins de 3 relevés météo
    horodatés (créneau 4 h) sur le jour → warning."""
    subject = SimpleNamespace(weather_readings=[object()] * n_readings)
    out = await _run_rule(db, "R12", [subject], 0)
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"
        assert out[0].details["releves_meteo"] == n_readings


@pytest.mark.asyncio
async def test_r12_weather_frequency_abstains_without_attribute(db):
    """Duck-typé : un sujet sans ``weather_readings`` (pas un NoonEvent) ne
    déclenche pas le volet fréquence — retombe sur le copier-coller usuel."""
    a = SimpleNamespace(latitude=49.0, longitude=-1.0)
    b = SimpleNamespace(latitude=49.0, longitude=-1.0)  # identique → copier-coller
    out = await _run_rule(db, "R12", [a, b], 1)
    assert out[0].result == "fail"
    assert "identical_fields" in out[0].details


@pytest.mark.asyncio
async def test_r13_chronology(db):
    t0 = datetime(2026, 4, 1, 12, tzinfo=UTC)
    seq = [
        SimpleNamespace(recorded_at=t0),
        SimpleNamespace(recorded_at=t0 + timedelta(days=1)),  # croissant vs [0]
        SimpleNamespace(recorded_at=t0),  # antériorité vs [1]
        SimpleNamespace(recorded_at=t0),  # égal (doublon) vs [2]
    ]
    assert (await _run_rule(db, "R13", seq, 0))[0].result == "pass"
    assert (await _run_rule(db, "R13", seq, 1))[0].result == "pass"
    assert (await _run_rule(db, "R13", seq, 2))[0].result == "fail"
    assert (await _run_rule(db, "R13", seq, 3))[0].result == "fail"


def _hold(zone, temp_c):
    return SimpleNamespace(zone=zone, temp_c=temp_c)


@pytest.mark.asyncio
async def test_r29_complete_noon_passes(db):
    """G6 — voilure + températures air/mer présentes → conforme."""
    subject = SimpleNamespace(
        sail_readings=[SimpleNamespace()],
        hold_readings=[_hold("air", Decimal("22.5")), _hold("sea_water", Decimal("18.0"))],
    )
    out = await _run_rule(db, "R29", [subject], 0)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r29_missing_sail_and_temperatures_flags_info(db):
    """G6 — aucune voilure ni température → info, jamais bloquant."""
    subject = SimpleNamespace(sail_readings=[], hold_readings=[])
    out = await _run_rule(db, "R29", [subject], 0)
    assert out[0].result == "fail" and out[0].severity == "info"
    assert set(out[0].details["missing"]) == {"voilure", "température air", "température mer"}


@pytest.mark.asyncio
async def test_r29_missing_only_sea_temperature(db):
    """G6 — voilure et air renseignés, température mer absente → info ciblé."""
    subject = SimpleNamespace(
        sail_readings=[SimpleNamespace()],
        hold_readings=[_hold("air", Decimal("22.5"))],
    )
    out = await _run_rule(db, "R29", [subject], 0)
    assert out[0].result == "fail"
    assert out[0].details["missing"] == ["température mer"]


@pytest.mark.asyncio
async def test_r29_abstains_on_non_noon_subject(db):
    """Duck-typé : un sujet sans ``sail_readings`` (pas un Noon) s'abstient."""
    subject = SimpleNamespace(event_type="departure")
    out = await _run_rule(db, "R29", [subject], 0)
    assert out[0].result == "pass"


# ─────────────────────────── run_rules & snapshot ────────────────────────


@pytest.mark.asyncio
async def test_run_rules_persists_with_threshold_snapshot(db):
    await seed_reference_data(db)
    invalidate_cache()
    subjects = [
        SimpleNamespace(
            vessel_id=1,
            recorded_at=datetime(2026, 4, 1, tzinfo=UTC),
            leg_id=1,
            leg_code="1CFRBR6",
            conso_l_j=Decimal("800"),
        ),
    ]
    summary = await run_rules(db, "event", subjects, run_id="run_test_1")
    rows = list((await db.execute(select(QualityCheckResult))).scalars().all())
    assert rows, "des résultats doivent être persistés"
    assert summary.run_id == "run_test_1"
    assert all(r.run_id == "run_test_1" for r in rows)

    # R11 a échoué (conso 800 > 750) avec le snapshot du seuil consommé.
    r11 = [r for r in rows if r.rule_id == "R11"]
    assert r11 and r11[0].result == "fail"
    assert r11[0].severity_applied == "warning"
    used = (r11[0].details or {}).get("thresholds_used")
    assert used and any(
        u["parameter_name"] == "seuil_conso_ref_l_j" and Decimal(u["value"]) == Decimal("750")
        for u in used
    )
    # subject_type/subject_id renseignés (référence polymorphe).
    assert r11[0].subject_type and r11[0].leg_id == 1


@pytest.mark.asyncio
async def test_run_rules_only_runs_coded_active_rules(db):
    """Les règles seedées mais non codées (ex. R14) ne produisent rien."""
    await seed_reference_data(db)
    invalidate_cache()
    await run_rules(db, "voyage", [SimpleNamespace(id=1, leg_id=1)], run_id="rv")
    rows = list((await db.execute(select(QualityCheckResult))).scalars().all())
    # scope voyage : R14/R15/R17/R20/R26 sont seedées mais aucune n'est codée.
    assert rows == []


@pytest.mark.asyncio
async def test_run_rules_threshold_change_flips_verdict(db):
    """Critère d'acceptation : 750 → 800 change le verdict sans redéploiement."""
    await seed_reference_data(db)
    invalidate_cache()
    subj = [SimpleNamespace(conso_l_j=Decimal("780"))]

    s1 = await run_rules(db, "event", subj, run_id="before")
    before = next(r for r in s1.results if r.rule_id == "R11")
    assert before.result == "fail"  # 780 > 750

    row = (
        await db.execute(
            select(ValidationRuleThreshold).where(
                ValidationRuleThreshold.rule_id == "R11",
                ValidationRuleThreshold.parameter_name == "seuil_conso_ref_l_j",
                ValidationRuleThreshold.vessel_id.is_(None),
            )
        )
    ).scalar_one()
    row.value = Decimal("800")
    await db.flush()
    invalidate_cache()

    s2 = await run_rules(db, "event", subj, run_id="after")
    after = next(r for r in s2.results if r.rule_id == "R11")
    assert after.result == "pass"  # 780 < 800


@pytest.mark.asyncio
async def test_run_rules_rule_exception_is_fail_info(db, monkeypatch):
    await seed_reference_data(db)
    invalidate_cache()

    async def _boom(ctx):
        raise ValueError("règle cassée")

    monkeypatch.setitem(RULES, "R01", _boom)
    summary = await run_rules(db, "event", [SimpleNamespace(vessel_id=1)], run_id="rx")
    r01 = [r for r in summary.results if r.rule_id == "R01"]
    assert r01 and r01[0].result == "fail"
    assert r01[0].severity_applied == "info"
    assert "Erreur technique" in (r01[0].message or "")


@pytest.mark.asyncio
async def test_run_rules_pass_not_persisted_when_disabled(db):
    await seed_reference_data(db)
    invalidate_cache()
    subj = [
        SimpleNamespace(
            vessel_id=1, recorded_at=datetime(2026, 4, 1, tzinfo=UTC), leg_id=1, leg_code="1CFRBR6"
        )
    ]
    summary = await run_rules(db, "event", subj, run_id="np", persist_passes=False)
    rows = list((await db.execute(select(QualityCheckResult))).scalars().all())
    # Tous les contrôles passent → aucune ligne persistée, mais comptés.
    assert rows == []
    assert summary.passed >= 1 and summary.failed == 0
