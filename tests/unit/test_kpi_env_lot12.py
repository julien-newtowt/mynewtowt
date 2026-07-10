"""Tests unitaires — Dashboard Performance Environnementale (LOT 12).

Couvre les formules serveur de ``services.kpi_env`` ajoutées au LOT 12 :

- **profil de propulsion** (spec §5.4) : les 4 catégories (vélique pur /
  hybride / mécanique / statique), la classification par tranche 4 h, et la
  règle STRICTE « les tranches sans relevé sont exclues du dénominateur » —
  cas canonique : 6 tranches théoriques, 4 renseignées ⇒ dénominateur 4,
  complétude 66,7 % ;
- **jauge conso vs cible** : le verdict d'affichage change quand on déplace le
  seuil paramétrable (750 → 800) ;
- **quality_overview** : compteurs par sévérité + non-acquittés + par règle ;
- **voyage_detail** (fixtures golden lot 13, ``1EGB5`` avec mouillage +
  soutage) : ROB timeline (points + soutage intercalé), répartition ME/AE,
  profil de propulsion réel.

Les tests DB utilisent une base SQLite en mémoire locale (même patron que
``test_kpi_env.py`` — isolation, pas d'enforcement FK).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.validation import QualityCheckResult
from app.services.kpi_env import (
    PROPULSION_CATEGORIES,
    build_propulsion_profile,
    classify_propulsion_slot,
    conso_vs_target,
    propulsion_profile,
    quality_overview,
    voyage_detail,
)


def _slot(**kw) -> SimpleNamespace:
    """Relevé de voilure duck-typé (attributs de NavEventSailReading)."""
    base = dict(
        j0=False, fwd_j1=False, fwd_ms=False, aft_j1=False, aft_ms=False,
        me_ps_load_pct=Decimal("0"), me_sb_load_pct=Decimal("0"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ═══════════════════════════════ classify_propulsion_slot (4 catégories)


def test_classify_velique_pur_sail_no_engine():
    # j0 seul = voile (petit foc), moteur nul ⇒ vélique pur.
    assert classify_propulsion_slot(_slot(j0=True)) == "velique_pur"
    assert classify_propulsion_slot(_slot(aft_ms=True)) == "velique_pur"


def test_classify_hybride_sail_and_engine():
    assert classify_propulsion_slot(_slot(fwd_j1=True, me_ps_load_pct=Decimal("50"))) == "hybride"


def test_classify_mecanique_engine_no_sail():
    assert classify_propulsion_slot(_slot(me_sb_load_pct=Decimal("76"))) == "mecanique"


def test_classify_statique_neither():
    # Ni voile ni moteur ⇒ statique (jamais confondu avec « mécanique »).
    assert classify_propulsion_slot(_slot()) == "statique"


# ═══════════════════════ build_propulsion_profile (slot manquant exclu)


def test_propulsion_missing_slots_excluded_from_denominator():
    """6 tranches théoriques, 4 renseignées ⇒ dénominateur 4 (spec stricte).

    Les 2 tranches sans relevé ne comptent PAS comme « statique » et ne
    gonflent pas le dénominateur — un trou de saisie ne fait pas chuter le %
    de vélique."""
    readings = [
        _slot(j0=True),                                       # vélique pur
        _slot(aft_j1=True, me_ps_load_pct=Decimal("40")),     # hybride
        _slot(me_ps_load_pct=Decimal("80")),                  # mécanique
        _slot(),                                              # statique (relevé présent)
    ]
    profile = build_propulsion_profile(readings, theoretical_slots=6)

    assert profile.filled_slots == 4  # dénominateur = tranches RENSEIGNÉES
    assert profile.theoretical_slots == 6
    assert profile.counts == {
        "velique_pur": 1, "hybride": 1, "mecanique": 1, "statique": 1
    }
    # Chaque catégorie = 1/4 = 25,0 % (dénominateur 4, pas 6).
    by_cat = {s.category: s.pct for s in profile.segments}
    for cat in PROPULSION_CATEGORIES:
        assert by_cat[cat] == Decimal("25.0")
    # Complétude AFFICHÉE = 4 / 6 = 66,7 %.
    assert profile.completeness_pct == Decimal("66.7")
    assert profile.na_reason is None


def test_propulsion_empty_is_na():
    profile = build_propulsion_profile([], theoretical_slots=6)
    assert profile.filled_slots == 0
    assert profile.na_reason is not None
    assert all(s.pct is None for s in profile.segments)


def test_propulsion_all_static_still_counts():
    """Un voyage 100 % statique reste calculé (statique EST une catégorie)."""
    profile = build_propulsion_profile([_slot(), _slot(), _slot()], theoretical_slots=3)
    assert profile.filled_slots == 3
    assert profile.counts["statique"] == 3
    assert profile.completeness_pct == Decimal("100.0")


# ═══════════════════════ conso_vs_target (jauge, seuil paramétrable)


def test_conso_target_verdict_flips_with_threshold():
    """À conso égale (780 L/j), déplacer la cible 750 → 800 change le verdict."""
    at_750 = conso_vs_target(Decimal("780"), Decimal("750"))
    at_800 = conso_vs_target(Decimal("780"), Decimal("800"))

    assert at_750.over_target is True
    assert at_750.delta_pct == Decimal("4.0")  # (780-750)/750*100
    assert at_800.over_target is False
    assert at_800.delta_pct == Decimal("2.5")  # (800-780)/800*100
    assert at_750.over_target != at_800.over_target


def test_conso_target_na_when_no_daily():
    result = conso_vs_target(None, Decimal("750"))
    assert result.daily_l_j is None
    assert result.over_target is False
    assert result.na_reason is not None


# ═══════════════════════════════════════ DB fixture (SQLite in-memory)


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
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


# ═══════════════════════════════════════ quality_overview (compteurs)


def _qcr(rule_id, severity, *, ack=False):
    return QualityCheckResult(
        rule_id=rule_id,
        subject_type="bunker",
        subject_id=1,
        run_id="run-test",
        result="fail",
        severity_applied=severity,
        message=f"{rule_id} test",
        executed_at=datetime(2026, 6, 1, tzinfo=UTC),
        acknowledged_at=(datetime(2026, 6, 2, tzinfo=UTC) if ack else None),
    )


@pytest.mark.asyncio
async def test_quality_overview_counters(db):
    db.add_all([
        _qcr("R14", "warning"),               # non acquitté
        _qcr("R14", "warning", ack=True),     # acquitté
        _qcr("R10", "bloquant"),              # non acquitté
        QualityCheckResult(                    # pass → ignoré
            rule_id="R08", subject_type="event", subject_id=2, run_id="r",
            result="pass", severity_applied="info",
            executed_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    ])
    await db.flush()

    o = await quality_overview(db, now=datetime(2026, 7, 1, tzinfo=UTC))

    by_sev = {s.severity: s for s in o.severity_counts}
    assert by_sev["warning"].total == 2
    assert by_sev["warning"].unacknowledged == 1
    assert by_sev["bloquant"].total == 1
    assert by_sev["bloquant"].unacknowledged == 1
    assert by_sev["info"].total == 0
    assert o.total_fails == 3
    assert o.total_unacknowledged == 2

    by_rule = {r.rule_id: r.count for r in o.by_rule}
    assert by_rule == {"R14": 2, "R10": 1}
    # Tri par fréquence décroissante : R14 (2) avant R10 (1).
    assert o.by_rule[0].rule_id == "R14"
    # 12 points de tendance, le fail de juin 2026 est compté.
    assert len(o.trend) == 12
    assert sum(p.count for p in o.trend) == 3


@pytest.mark.asyncio
async def test_quality_overview_empty_db_no_error(db):
    o = await quality_overview(db, now=datetime(2026, 7, 1, tzinfo=UTC))
    assert o.total_fails == 0
    assert o.pending_resets == []
    assert o.unreconciled_bunkers == []


# ═══════════════════ voyage_detail + propulsion (fixtures golden 1EGB5)


@pytest.mark.asyncio
async def test_voyage_detail_rob_timeline_with_bunker(db):
    """Voyage réel avec mouillage + soutage (fixture lot 13 ``1EGB5``).

    ROB timeline : points chaînés + point de RÉFÉRENCE au Departure (R14-v2,
    ROB déclaré 73,6 t) + marqueur de soutage intercalé (BDN 258663)."""
    from tests.fixtures.mrv_2025.loader import load_voyage

    fixture = await load_voyage(db, "1EGB5")
    detail = await voyage_detail(db, fixture.leg.id)

    assert detail is not None
    assert detail.leg_code == "1EGB5"
    assert detail.source == "events"

    # Point de référence au Departure = ROB déclaré du dataset.
    departure_pts = [p for p in detail.rob_chain if p.event_type == "departure"]
    assert departure_pts and departure_pts[0].rob_declared_t == Decimal("73.6")

    # Soutage intercalé présent (marqueur ROB timeline).
    assert len(detail.bunkers) == 1
    assert detail.bunkers[0].bdn_number == "258663"

    # Répartition ME/AE calculée (le grand livre a le split events).
    assert detail.me_pct is not None and detail.ae_pct is not None
    assert (detail.me_pct + detail.ae_pct) == Decimal("100.0")

    # La géométrie SVG de la timeline (helper routeur) inclut le soutage.
    from app.routers.dashboard_env_router import _rob_timeline

    rob = _rob_timeline(detail.rob_chain, detail.bunkers)
    assert rob["has_data"] is True
    assert len(rob["bunker_markers"]) == 1
    # Points de référence (Departure/Arrival) marqués distinctement.
    assert any(p["is_ref"] for p in rob["points"])


@pytest.mark.asyncio
async def test_propulsion_profile_from_fixture(db):
    """``propulsion_profile`` sur un voyage réel : théorique = noons × 6,
    renseigné = relevés présents, catégories sommant au renseigné."""
    from tests.fixtures.mrv_2025.loader import load_voyage

    fixture = await load_voyage(db, "1EGB5")
    n_noons = sum(1 for e in fixture.events if e.event_type == "noon")

    profile = await propulsion_profile(db, fixture.leg.id)

    assert profile.theoretical_slots == n_noons * 6
    assert profile.filled_slots == sum(profile.counts.values())
    assert profile.filled_slots > 0
    # Complétude bornée à 100 % (relevés présents ≤ théoriques).
    assert profile.completeness_pct is not None
