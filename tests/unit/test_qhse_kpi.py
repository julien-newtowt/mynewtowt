"""Tests — indicateurs du tableau de bord QHSE (``app.services.qhse_kpi``).

Base SQLite en mémoire, même patron que ``test_qhse_ingestion.py``. Les
signalements sont construits directement (pas via l'ingestion) pour isoler
le calcul des indicateurs de tout comportement du pipeline d'import.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles contre Base.metadata
from app.database import Base
from app.models.qhse import CorrectiveAction, QhseReport, RootCauseEvaluation
from app.models.vessel import Vessel
from app.services.qhse_kpi import build_dashboard, list_vessels_with_reports, trend_bars

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _seed_vessels(db) -> tuple[Vessel, Vessel]:
    anemos = Vessel(code="ANE", name="Anemos")
    artemis = Vessel(code="ART", name="Artemis")
    db.add_all([anemos, artemis])
    await db.flush()
    return anemos, artemis


async def _report(db, vessel_id, *, subject="R", grade="near_miss", issued=None, closed=None):
    r = QhseReport(
        vessel_id=vessel_id,
        subject=subject,
        grade=grade,
        issued_date=issued or NOW,
        closed_date=closed,
    )
    db.add(r)
    await db.flush()
    return r


# ═══════════════════════════════════════ Cas vide


async def test_empty_fleet_returns_zeroed_dashboard_not_a_crash(db):
    dash = await build_dashboard(db, now=NOW)
    assert dash.total_reports == 0
    assert dash.open_count == 0
    assert dash.field_completeness_pct is None  # pas un 0% qui mentirait
    assert dash.corrective_on_time_pct is None
    assert len(dash.trend) == 12
    assert all(p.count == 0 for p in dash.trend)
    assert dash.open_items == []


# ═══════════════════════════════════════ Filtre navire


async def test_vessel_filter_isolates_the_right_fleet_slice(db):
    anemos, artemis = await _seed_vessels(db)
    await _report(db, anemos.id, subject="A1")
    await _report(db, anemos.id, subject="A2")
    await _report(db, artemis.id, subject="B1")

    fleet = await build_dashboard(db, now=NOW)
    only_anemos = await build_dashboard(db, vessel_id=anemos.id, now=NOW)

    assert fleet.total_reports == 3
    assert only_anemos.total_reports == 2


async def test_list_vessels_with_reports_excludes_vessels_without_data(db):
    anemos, artemis = await _seed_vessels(db)
    await _report(db, anemos.id)

    vessels = await list_vessels_with_reports(db)
    assert [v.id for v in vessels] == [anemos.id]  # Artemis n'a aucun signalement


# ═══════════════════════════════════════ Répartition par grade


async def test_grade_counts_cover_all_six_grades_even_at_zero(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, grade="accident")
    await _report(db, anemos.id, grade="accident")
    await _report(db, anemos.id, grade="observation")

    dash = await build_dashboard(db, now=NOW)
    tally = {g.grade: g.count for g in dash.grade_counts}
    assert tally == {
        "accident": 2,
        "non_conformity": 0,
        "near_miss": 0,
        "observation": 1,
        "deficiency": 0,
        "casualty": 0,
    }
    assert sum(tally.values()) == dash.total_reports


# ═══════════════════════════════════════ Ouverts / ancienneté


async def test_open_items_sorted_oldest_first_with_correct_age(db):
    anemos, _ = await _seed_vessels(db)
    old = await _report(db, anemos.id, subject="Old", issued=NOW - timedelta(days=30))
    recent = await _report(db, anemos.id, subject="Recent", issued=NOW - timedelta(days=2))
    await _report(db, anemos.id, subject="Closed", issued=NOW - timedelta(days=100), closed=NOW)

    dash = await build_dashboard(db, now=NOW)
    assert dash.open_count == 2  # le clos n'y est pas
    assert [item.id for item in dash.open_items] == [old.id, recent.id]
    assert dash.open_items[0].days_open == 30
    assert dash.open_items[1].days_open == 2


async def test_open_items_capped_at_ten(db):
    anemos, _ = await _seed_vessels(db)
    for i in range(15):
        await _report(db, anemos.id, subject=f"R{i}", issued=NOW - timedelta(days=i + 1))

    dash = await build_dashboard(db, now=NOW)
    assert dash.open_count == 15
    assert len(dash.open_items) == 10


# ═══════════════════════════════════════ Tendance 12 mois


async def test_trend_has_zero_for_months_without_reports(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, issued=NOW)  # mois courant seulement

    dash = await build_dashboard(db, now=NOW)
    assert len(dash.trend) == 12
    assert dash.trend[-1].count == 1  # dernier point = mois courant
    assert all(p.count == 0 for p in dash.trend[:-1])


async def test_trend_bars_geometry_is_sane(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, issued=NOW)
    dash = await build_dashboard(db, now=NOW)

    bars, meta = trend_bars(dash.trend)
    assert len(bars) == 12
    assert meta["width"] > 0 and meta["height"] > 0
    assert all(b["width"] >= 0 and b["height"] >= 0 for b in bars)
    # Le seul mois non-vide doit produire la barre la plus haute (ou nulle
    # si tout est à zéro — ici il y a exactement 1 signalement).
    assert max(b["height"] for b in bars) > 0


# ═══════════════════════════════════════ R1 — complétude


async def test_completeness_is_none_shaped_correctly_per_report(db):
    anemos, _ = await _seed_vessels(db)

    # 0/3 : rien de renseigné.
    bare = await _report(db, anemos.id, subject="bare")

    # 3/3 : cause racine, description corrective, responsable identifié.
    full = await _report(db, anemos.id, subject="full")
    db.add(CorrectiveAction(report_id=full.id, description="fixed", responsible_user_id=None))
    db.add(RootCauseEvaluation(report_id=full.id, root_cause_text="cause"))
    await db.flush()
    # responsable porté par le workflow corrective — suffit pour les 3/3
    action = (
        await db.execute(select(CorrectiveAction).where(CorrectiveAction.report_id == full.id))
    ).scalar_one()
    action.responsible_user_id = None  # laissé None volontairement : voir test suivant

    dash = await build_dashboard(db, now=NOW)
    # bare=0/3, full=2/3 (pas de responsable ici) -> moyenne = (0 + 2/3)/2 = 1/3 = 33.3%
    assert dash.field_completeness_pct == Decimal("33.3")


# ═══════════════════════════════════════ C1 — action corrective à temps


async def test_c1_excludes_unfinished_actions_from_denominator(db):
    """23 % des actions du jeu de données réel n'ont pas de date de
    finalisation (cahier des charges §3.4/§5.7) — elles doivent rester
    hors du dénominateur, jamais comptées comme "en retard"."""
    anemos, _ = await _seed_vessels(db)

    on_time = await _report(db, anemos.id, subject="on_time")
    db.add(
        CorrectiveAction(
            report_id=on_time.id,
            limit_date=date(2026, 3, 1),
            finished_date=date(2026, 2, 20),
        )
    )
    late = await _report(db, anemos.id, subject="late")
    db.add(
        CorrectiveAction(
            report_id=late.id,
            limit_date=date(2026, 3, 1),
            finished_date=date(2026, 3, 15),
        )
    )
    unfinished = await _report(db, anemos.id, subject="unfinished")
    db.add(CorrectiveAction(report_id=unfinished.id, limit_date=date(2026, 3, 1)))
    await db.flush()

    dash = await build_dashboard(db, now=NOW)
    assert dash.corrective_total_count == 3
    assert dash.corrective_finished_count == 2  # pas les 3 — l'inachevée est exclue
    assert dash.corrective_on_time_pct == 50.0  # 1 sur 2 finalisées, pas 1 sur 3


# ═══════════════════════════════════════ C2 — dénominateur = tous les rapports


async def test_c2_denominator_is_all_reports_not_just_evaluations_created(db):
    """Cahier des charges §5.7 C2 : le dénominateur est le TOTAL des
    rapports, pas seulement ceux qui ont une évaluation créée — sinon un
    rapport sans aucune évaluation disparaîtrait du calcul au lieu de
    compter comme « non complétée »."""
    anemos, _ = await _seed_vessels(db)

    evaluated = await _report(db, anemos.id, subject="evaluated")
    db.add(RootCauseEvaluation(report_id=evaluated.id, root_cause_text="x", finished_date=NOW.date()))
    await _report(db, anemos.id, subject="no_evaluation_at_all")
    await db.flush()

    dash = await build_dashboard(db, now=NOW)
    assert dash.total_reports == 2
    assert dash.root_cause_finished_count == 1
    assert dash.root_cause_completion_pct == 50.0  # 1/2, pas 1/1
