"""LOT 8 — règles R03-R07 (scope event, structurelles) : tests table-driven.

Chaque règle : ≥ 3 cas (pass / fail / limite exacte au seuil), vérifiant
verdict + sévérité (override par verdict, hook lot 8) + snapshot des seuils
dans ``details`` (via ``run_rules``). Inclut le cas réel AV-001 du dossier
client sur R02 amendé : ``1AFRBR6`` valide vs ``1AFRBZ6`` (pays « BZ » ≠ port
réel « BR ») — bien formé au sens du format, incohérent au sens des ports.
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
from app.models.port import Port
from app.models.validation import QualityCheckResult
from app.services.validation_engine import (
    RULES,
    RuleContext,
    invalidate_cache,
    run_rules,
    seed_reference_data,
)

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


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


async def _run(db, rid, subjects, index=0, **kw):
    return await RULES[rid](_ctx(db, rid, subjects, index, **kw))


# ═════════════════════════════════════════════ R03 — type d'événement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject,result,severity",
    [
        (SimpleNamespace(event_type="noon"), "pass", None),
        (SimpleNamespace(event_type="departure"), "pass", None),
        (SimpleNamespace(event_type=""), "fail", "bloquant"),  # manquant
        (SimpleNamespace(event_type="banquet"), "fail", "bloquant"),  # non reconnu
    ],
)
async def test_r03_event_type(db, subject, result, severity):
    out = await _run(db, "R03", [subject])
    assert out[0].result == result
    assert out[0].severity == severity


@pytest.mark.asyncio
async def test_r03_abstains_on_non_event_subject(db):
    """Sujet sans ``event_type`` (rapport, bunker…) → hors périmètre R03."""
    assert await _run(db, "R03", [SimpleNamespace(mass_t=1)]) == []


# ═════════════════════════════════════════════ R04 — datetime présent/plausible


@pytest.mark.asyncio
async def test_r04_missing_datetime_is_blocking(db):
    out = await _run(db, "R04", [SimpleNamespace(datetime_utc=None)])
    assert out[0].result == "fail" and out[0].severity == "bloquant"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dt,result",
    [
        (NOW - timedelta(days=1), "pass"),  # passé
        (NOW + timedelta(hours=24), "pass"),  # limite exacte (= tol)
        (NOW + timedelta(hours=24, seconds=1), "fail"),  # au-delà
    ],
)
async def test_r04_future_tolerance_exact_boundary(db, dt, result):
    out = await _run(db, "R04", [SimpleNamespace(datetime_utc=dt)])
    assert out[0].result == result
    if result == "fail":
        assert out[0].severity == "warning"  # plausibilité = warning, pas bloquant


@pytest.mark.asyncio
async def test_r04_snapshot_thresholds_in_details(db):
    """Le seuil consommé (tolerance_datetime_futur_h) est snapshotté (audit).

    ``run_rules`` évalue avec l'horloge réelle → le sujet est construit
    48 h dans le futur RÉEL (pas la constante de test NOW)."""
    subj = SimpleNamespace(datetime_utc=datetime.now(UTC) + timedelta(hours=48), leg_id=1)
    summary = await run_rules(db, "event", [subj], run_id="r04snap")
    r04 = next(r for r in summary.results if r.rule_id == "R04")
    assert r04.result == "fail" and r04.severity_applied == "warning"
    used = (r04.details or {}).get("thresholds_used") or []
    assert any(
        u["parameter_name"] == "tolerance_datetime_futur_h" and Decimal(u["value"]) == 24
        for u in used
    )


# ═════════════════════════════════════════════ R05 — position


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject,result,severity",
    [
        (SimpleNamespace(lat_decimal=Decimal("48.5"), lon_decimal=Decimal("-5.1")), "pass", None),
        (
            SimpleNamespace(lat_decimal=Decimal("90"), lon_decimal=Decimal("180")),
            "pass",
            None,
        ),  # bornes incluses
        (SimpleNamespace(lat_decimal=Decimal("91"), lon_decimal=Decimal("0")), "fail", "bloquant"),
        (
            SimpleNamespace(lat_decimal=Decimal("0"), lon_decimal=Decimal("-181")),
            "fail",
            "bloquant",
        ),
        (
            SimpleNamespace(
                lat_decimal=Decimal("48"),
                lon_decimal=Decimal("-5"),
                position_source="manuel_justifie",
            ),
            "fail",
            "bloquant",
        ),  # sans justification
        (
            SimpleNamespace(
                lat_decimal=Decimal("48"),
                lon_decimal=Decimal("-5"),
                position_source="manuel_justifie",
                position_justification="Thalos HS, point sextant",
            ),
            "pass",
            None,
        ),
    ],
)
async def test_r05_position(db, subject, result, severity):
    out = await _run(db, "R05", [subject])
    assert out[0].result == result
    assert out[0].severity == severity


@pytest.mark.asyncio
async def test_r05_abstains_without_position_fields(db):
    assert await _run(db, "R05", [SimpleNamespace(mass_t=1)]) == []


# ═════════════════════════════════════════════ R06 — ROB de référence (PortCall)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rob,result,severity",
    [
        (Decimal("100"), "pass", None),
        (Decimal("300"), "pass", None),  # limite exacte borne_max_rob_t
        (None, "fail", "bloquant"),  # manquant
        (Decimal("-1"), "fail", "bloquant"),  # négatif
        (Decimal("0"), "fail", "warning"),  # nul → warning
        (Decimal("300.001"), "fail", "warning"),  # > borne plausible
    ],
)
async def test_r06_rob_reference(db, rob, result, severity):
    subj = SimpleNamespace(event_type="departure", rob_t=rob)
    out = await _run(db, "R06", [subj])
    assert out[0].result == result
    assert out[0].severity == severity


@pytest.mark.asyncio
async def test_r06_only_applies_to_portcalls(db):
    """Hiérarchie R14-v2 : le ROB de référence n'existe QUE sur Departure/
    Arrival — un Noon (pas de champ ROB) est hors périmètre R06."""
    assert await _run(db, "R06", [SimpleNamespace(event_type="noon")]) == []


# ═════════════════════════════════════════════ R07 — ports du voyage (LOCODE)


async def _ports_leg(
    db, *, dep_locode="FRFEC", arr_locode="BRBEL", dep_country="FR", arr_country="BR"
):
    p1 = Port(name="Dep", country=dep_country, locode=dep_locode)
    p2 = Port(name="Arr", country=arr_country, locode=arr_locode)
    db.add_all([p1, p2])
    await db.flush()
    return SimpleNamespace(id=1, departure_port_id=p1.id, arrival_port_id=p2.id, leg_code="1AFRBR6")


@pytest.mark.asyncio
async def test_r07_valid_locodes_pass(db):
    leg = await _ports_leg(db)
    out = await _run(db, "R07", [SimpleNamespace(event_type="noon")], leg=leg)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r07_bad_locode_warns(db):
    leg = await _ports_leg(db, arr_locode="XX")  # 2 caractères ≠ 5
    out = await _run(db, "R07", [SimpleNamespace(event_type="noon")], leg=leg)
    assert out[0].result == "fail" and out[0].severity == "warning"
    assert "arrivée" in out[0].message


@pytest.mark.asyncio
async def test_r07_only_first_subject_and_needs_leg(db):
    leg = await _ports_leg(db)
    subjects = [SimpleNamespace(event_type="noon"), SimpleNamespace(event_type="noon")]
    # index 1 → évalué une seule fois par séquence (au 1er sujet).
    assert await _run(db, "R07", subjects, index=1, leg=leg) == []
    # sans contexte voyage → abstention.
    assert await _run(db, "R07", subjects, index=0, leg=None) == []


# ═════════════════════════ R02 amendé — cas réel AV-001 (« 1AFRBZ6 »)


@pytest.mark.asyncio
async def test_r02_real_case_1afrbr6_valid(db):
    """Cas réel : le code correct ``1AFRBR6`` (FR→BR) passe format ET pays."""
    leg = await _ports_leg(db)
    out = await _run(db, "R02", [SimpleNamespace(leg_id=1)], leg=leg)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_r02_real_case_1afrbz6_country_mismatch(db):
    """Cas réel AV-001 : ``1AFRBZ6`` est BIEN FORMÉ (1 chiffre + 5 lettres +
    1 chiffre) mais « BZ » (Belize) ≠ pays du port d'arrivée réel « BR »
    (Brésil) — détecté par le volet pays de R02 (lot 8), sévérité warning
    (codification voyage, ne bloque pas l'événement)."""
    leg = await _ports_leg(db)
    leg.leg_code = "1AFRBZ6"
    out = await _run(db, "R02", [SimpleNamespace(leg_id=1)], leg=leg)
    assert out[0].result == "fail"
    assert out[0].severity == "warning"
    assert "AV-001" in out[0].message
    assert any("BZ" in m for m in out[0].details["mismatches"])


@pytest.mark.asyncio
async def test_r02_format_only_without_leg_context(db):
    """Sans contexte voyage chargé, ``1AFRBZ6`` reste indétectable (format
    valide) — c'est exactement l'angle mort du lot 2 que le volet pays referme."""
    out = await _run(db, "R02", [SimpleNamespace(leg_id=1, leg_code="1AFRBZ6")], leg=None)
    assert out[0].result == "pass"


# ═════════════════════════ persistance run_rules (sévérité par verdict)


@pytest.mark.asyncio
async def test_event_scope_persists_severity_overrides(db):
    """Un même run persiste des sévérités graduées PAR VERDICT (hook lot 8) :
    R06 ROB=0 → warning alors que la sévérité par défaut de R06 est bloquant."""
    subj = SimpleNamespace(
        event_type="departure", rob_t=Decimal("0"), leg_id=1, datetime_utc=NOW - timedelta(days=1)
    )
    summary = await run_rules(db, "event", [subj], run_id="sevgrad")
    r06 = next(r for r in summary.results if r.rule_id == "R06")
    assert r06.result == "fail"
    assert r06.severity_applied == "warning"  # gradué, pas le défaut bloquant
    rows = (
        (
            await db.execute(
                select(QualityCheckResult).where(
                    QualityCheckResult.run_id == "sevgrad", QualityCheckResult.rule_id == "R06"
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows and rows[0].severity_applied == "warning"
