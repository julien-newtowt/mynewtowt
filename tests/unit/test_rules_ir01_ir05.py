"""LOT 8 — règles inter-rapports IR01-IR05 (séquences) : tests table-driven.

Séquences synthétiques + **cas réels du dossier client** (fiche
``QC_Noon_Reports_ANEMOS_ARTEMIS.xlsx`` + notebook QC BDD) :

- IR01 — doublon de date+type (BDD Noon : deux rapports au même jour) ;
- IR02 — ROB(J) ≈ ROB(J-1) − conso ± soutage (bornes R14 : > mineur warning,
  > critique bloquant) ;
- IR03 — **ROB ANEMOS figé à 72,3 t du 17 au 20/06 (4 jours)** puis saut
  brutal −7,6 t (CF-001→005) ;
- IR04 — compteur carburant régressant sans reset documenté → bloquant ;
- IR05 — position strictement figée sur ≥ N relevés consécutifs en mer.
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

T0 = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


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


def _ctx(db, rid, subjects, index, now=NOW) -> RuleContext:
    return RuleContext(db=db, rule_id=rid, subject=subjects[index],
                       subjects=list(subjects), index=index, now=now)


async def _run(db, rid, subjects, index):
    return await RULES[rid](_ctx(db, rid, subjects, index))


def _noon(day_offset, **extra):
    return SimpleNamespace(event_type="noon",
                           datetime_utc=T0 + timedelta(days=day_offset), **extra)


# ═════════════════════════════════════════════ IR01 — doublon de date+type


@pytest.mark.asyncio
async def test_ir01_duplicate_same_day_same_type_blocking(db):
    """Cas réel : deux Noon à la même DATE (BDD Noon Reports) → bloquant."""
    seq = [
        _noon(0),
        SimpleNamespace(event_type="noon",
                        datetime_utc=T0 + timedelta(hours=6)),  # même jour
    ]
    out = await _run(db, "IR01", seq, 1)
    assert out[0].result == "fail" and out[0].severity == "bloquant"
    assert out[0].details["date"] == "2026-06-17"


@pytest.mark.asyncio
async def test_ir01_same_day_different_type_passes(db):
    """Limite : même jour mais types différents (Arrival + Departure le même
    jour est un enchaînement d'escale normal) → pas un doublon."""
    seq = [
        SimpleNamespace(event_type="arrival", datetime_utc=T0),
        SimpleNamespace(event_type="departure", datetime_utc=T0 + timedelta(hours=8)),
    ]
    out = await _run(db, "IR01", seq, 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_ir01_distinct_days_pass_and_abstains_without_dt(db):
    seq = [_noon(0), _noon(1)]
    assert (await _run(db, "IR01", seq, 1))[0].result == "pass"
    # Sans datetime → abstention (présence portée par R01/R04).
    seq2 = [_noon(0), SimpleNamespace(event_type="noon", datetime_utc=None)]
    assert await _run(db, "IR01", seq2, 1) == []


# ═════════════════════════════════════════════ IR02 — continuité ROB


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cur_rob,result,severity",
    [
        (Decimal("99"), "pass", None),          # 100 − 1 = 99 → écart 0
        (Decimal("98.5"), "pass", None),        # limite : écart 0,5 == mineur
        (Decimal("98"), "fail", "warning"),     # écart 1 > mineur
        (Decimal("93"), "fail", "bloquant"),    # écart 6 > critique (5)
    ],
)
async def test_ir02_rob_continuity_r14_bounds(db, cur_rob, result, severity):
    seq = [
        _noon(0, rob_t=Decimal("100")),
        _noon(1, rob_t=cur_rob, conso_t=Decimal("1")),
    ]
    out = await _run(db, "IR02", seq, 1)
    assert out[0].result == result
    assert out[0].severity == severity


@pytest.mark.asyncio
async def test_ir02_bunkering_accounted(db):
    """ROB(J) = ROB(J-1) − conso + soutage : un soutage de 10 t est intégré."""
    seq = [
        _noon(0, rob_t=Decimal("100")),
        _noon(1, rob_t=Decimal("109"), conso_t=Decimal("1"), bunkered_t=Decimal("10")),
    ]
    out = await _run(db, "IR02", seq, 1)
    assert out[0].result == "pass"


@pytest.mark.asyncio
async def test_ir02_abstains_when_conso_unknown(db):
    """Conso indisponible (ni attribut ni compteurs) → abstention : la
    continuité fine est portée par R14 (chaîne calculée), jamais un faux
    positif sur ROB seul."""
    seq = [_noon(0, rob_t=Decimal("100")), _noon(1, rob_t=Decimal("93"))]
    assert await _run(db, "IR02", seq, 1) == []


# ═════════════════════════════════════════════ IR03 — ROB figé (cas réel CF-001→005)


def _anemos_frozen_sequence():
    """Reproduction du cas réel : ROB ANEMOS figé à 72,3 t du 17 au 20/06
    (4 relevés) avec conso réelle ≈ 1,257 t/j, puis saut brutal −7,6 t le 21."""
    conso = Decimal("1.257")
    return [
        _noon(0, rob_t=Decimal("72.3")),
        _noon(1, rob_t=Decimal("72.3"), conso_t=conso),
        _noon(2, rob_t=Decimal("72.3"), conso_t=conso),
        _noon(3, rob_t=Decimal("72.3"), conso_t=conso),
        _noon(4, rob_t=Decimal("64.7"), conso_t=conso),  # saut −7,6 t
    ]


@pytest.mark.asyncio
async def test_ir03_real_case_rob_frozen_4_days(db):
    """CF-001→005 : le figement est détecté au 3ᵉ relevé consécutif (seuil
    ``ir03_min_reports_figes`` = 3), UNE seule fois (pas au 4ᵉ — anti-bruit)."""
    seq = _anemos_frozen_sequence()
    assert (await _run(db, "IR03", seq, 0))[0].result == "pass"
    assert (await _run(db, "IR03", seq, 1))[0].result == "pass"   # 2 relevés figés
    out3 = await _run(db, "IR03", seq, 2)                          # 3ᵉ → alerte
    assert out3[0].result == "fail" and out3[0].severity == "warning"
    assert out3[0].details["reports"] == 3
    assert Decimal(out3[0].details["rob_t"]) == Decimal("72.3")
    assert (await _run(db, "IR03", seq, 3))[0].result == "pass"   # 4ᵉ : déjà signalé


@pytest.mark.asyncio
async def test_ir03_real_case_brutal_jump_caught_by_ir02(db):
    """Le SAUT brutal −7,6 t (vs −1,257 t attendu → écart 6,343 t > critique 5)
    est capté par IR02 en bloquant — le couple IR03+IR02 reproduit la fiche."""
    seq = _anemos_frozen_sequence()
    out = await _run(db, "IR02", seq, 4)
    assert out[0].result == "fail" and out[0].severity == "bloquant"
    assert Decimal(out[0].details["ecart_t"]) == Decimal("6.343")


@pytest.mark.asyncio
async def test_ir03_frozen_with_zero_conso_is_legitimate(db):
    """Limite : ROB figé avec conso CONNUE ≤ 0,05 t (escale moteur coupé) →
    cohérent, pas d'alerte."""
    seq = [
        _noon(0, rob_t=Decimal("72.3")),
        _noon(1, rob_t=Decimal("72.3"), conso_t=Decimal("0")),
        _noon(2, rob_t=Decimal("72.3"), conso_t=Decimal("0.05")),  # == seuil
    ]
    assert (await _run(db, "IR03", seq, 2))[0].result == "pass"


@pytest.mark.asyncio
async def test_ir03_frozen_with_unknown_conso_is_suspicious(db):
    """Conso totalement inconnue + ROB strictement figé 3 relevés → suspect
    (symptôme réel 2025 : macro de consolidation figée)."""
    seq = [
        _noon(0, rob_t=Decimal("72.3")),
        _noon(1, rob_t=Decimal("72.3")),
        _noon(2, rob_t=Decimal("72.3")),
    ]
    out = await _run(db, "IR03", seq, 2)
    assert out[0].result == "fail"
    assert out[0].details["span_conso_t"] is None


# ═════════════════════════════════════════════ IR04 — compteur régressant


def _counter(day, fuel, *, reset=False, confirmed=False):
    ns = _noon(day, fuel_counter_l=Decimal(str(fuel)), is_counter_reset=reset)
    if confirmed:
        ns.reset_confirmed = True
    return ns


@pytest.mark.asyncio
async def test_ir04_regression_without_reset_blocking(db):
    """Cas réel : compteur carburant (L) régressant d'un rapport à l'autre
    sans reset documenté → BLOQUANT (notebook QC, règle IR04)."""
    seq = [_counter(0, 34000), _counter(1, 33000)]
    out = await _run(db, "IR04", seq, 1)
    assert out[0].result == "fail" and out[0].severity == "bloquant"


@pytest.mark.asyncio
async def test_ir04_documented_reset_passes(db):
    """Un reset DOCUMENTÉ par le bord (``is_counter_reset``) suffit à IR04 —
    la CONFIRMATION Administrateur relève de R10 (distinction des deux règles)."""
    seq = [_counter(0, 34000), _counter(1, 100, reset=True)]
    assert (await _run(db, "IR04", seq, 1))[0].result == "pass"
    # ... alors que R10, lui, exige la confirmation :
    out_r10 = await _run(db, "R10", seq, 1)
    assert out_r10[0].result == "fail" and out_r10[0].severity in ("warning", "bloquant")


@pytest.mark.asyncio
async def test_ir04_monotonic_and_flat_pass(db):
    seq = [_counter(0, 34000), _counter(1, 34000), _counter(2, 35000)]
    assert (await _run(db, "IR04", seq, 1))[0].result == "pass"  # limite : Δ == 0
    assert (await _run(db, "IR04", seq, 2))[0].result == "pass"


# ═════════════════════════════════════════════ IR05 — position figée en mer


def _pos(day, lat, lon, et="noon"):
    return SimpleNamespace(event_type=et, datetime_utc=T0 + timedelta(days=day),
                           lat_decimal=Decimal(str(lat)), lon_decimal=Decimal(str(lon)))


@pytest.mark.asyncio
async def test_ir05_real_case_position_frozen_at_sea(db):
    """Cas réel : position STRICTEMENT identique sur 3 Noon consécutifs en
    mer → warning au 3ᵉ (une fois, anti-bruit)."""
    seq = [_pos(0, 48.5, -5.1), _pos(1, 48.5, -5.1), _pos(2, 48.5, -5.1),
           _pos(3, 48.5, -5.1)]
    assert (await _run(db, "IR05", seq, 1))[0].result == "pass"   # 2 relevés
    out = await _run(db, "IR05", seq, 2)                           # 3ᵉ → alerte
    assert out[0].result == "fail" and out[0].severity == "warning"
    assert out[0].details["reports"] == 3
    assert (await _run(db, "IR05", seq, 3))[0].result == "pass"   # déjà signalé


@pytest.mark.asyncio
async def test_ir05_moving_vessel_passes(db):
    seq = [_pos(0, 48.5, -5.1), _pos(1, 47.2, -6.0), _pos(2, 46.0, -7.2)]
    assert (await _run(db, "IR05", seq, 2))[0].result == "pass"


@pytest.mark.asyncio
async def test_ir05_portcall_out_of_scope_breaks_run(db):
    """« En mer » = Noon : un PortCall (position à quai légitimement figée)
    est hors périmètre ET casse la série."""
    # Le sujet courant PortCall → abstention.
    seq_pc = [_pos(0, 48.5, -5.1), _pos(1, 48.5, -5.1, et="arrival")]
    assert await _run(db, "IR05", seq_pc, 1) == []
    # Un PortCall intercalé casse la série des Noon figés.
    seq = [_pos(0, 48.5, -5.1), _pos(1, 48.5, -5.1, et="arrival"),
           _pos(2, 48.5, -5.1), _pos(3, 48.5, -5.1)]
    assert (await _run(db, "IR05", seq, 3))[0].result == "pass"  # série = 2 Noon


# ═════════════════════ run séquence complète (cas réel bout-en-bout)


@pytest.mark.asyncio
async def test_full_sequence_run_reproduces_dossier_anomalies(db):
    """Run scope ``event`` sur la séquence ANEMOS réelle (ROB figé 4 j + saut)
    → IR03 (figé) ET IR02 bloquant (saut) persistés dans le journal, avec le
    snapshot des seuils consommés (reproductibilité d'audit)."""
    seq = [
        SimpleNamespace(event_type="noon", vessel_id=1, leg_id=1,
                        datetime_utc=s.datetime_utc, rob_t=s.rob_t,
                        conso_t=getattr(s, "conso_t", None))
        for s in _anemos_frozen_sequence()
    ]
    summary = await run_rules(db, "event", seq, run_id="anemos2026")
    fails = {(r.rule_id, r.severity_applied) for r in summary.results if r.result == "fail"}
    assert ("IR03", "warning") in fails
    assert ("IR02", "bloquant") in fails
    ir02 = next(r for r in summary.results
                if r.rule_id == "IR02" and r.result == "fail")
    used = {u["parameter_name"] for u in ir02.details["thresholds_used"]}
    assert "seuil_rob_ecart_critique_t" in used
